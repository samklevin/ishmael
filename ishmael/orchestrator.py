"""Main orchestrator loop: poll bd ready, assign to agents, manage lifecycle."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import agent as agent_mod
from . import tmux as tmux_mod
from . import worktree
from .agent import Agent, AgentState
from .config import Config

logger = logging.getLogger(__name__)


class Orchestrator:
    STATUS_DIR = os.path.expanduser("~/.ishmael")

    def __init__(self, config: Config) -> None:
        self.config = config
        self.agents: list[Agent] = []
        self._agents_lock = threading.Lock()
        self.completed_count = 0
        self.failed_count = 0

        os.makedirs(self.STATUS_DIR, exist_ok=True)
        self._reconnect()

    def _bd(self, *args: str) -> subprocess.CompletedProcess:
        """Run a bd command and return the result."""
        return subprocess.run(
            ["bd", *args],
            env=self.config.bd_env(),
            capture_output=True,
            text=True,
        )

    def _reconnect(self) -> None:
        """Reconnect to any live worker processes from a previous session."""
        recovered = agent_mod.reconnect_agents(self.config.workers_dir)
        for ag in recovered:
            if ag.state == AgentState.RUNNING:
                if ag.pid and agent_mod._is_ishmael_worker(ag.pid):
                    ag._tmux_session = self.config.tmux_session
                    with self._agents_lock:
                        self.agents.append(ag)
                    logger.info("Reconnected to live worker for %s (pid %d)", ag.bead_id, ag.pid)
                else:
                    logger.warning("Orphaned worker for %s (pid dead), resetting bead", ag.bead_id)
                    self._bd("update", ag.bead_id, "--status", "open")
                    agent_mod.cleanup_worker_dir(ag.bead_id, self.config.workers_dir)
            else:
                agent_mod.cleanup_worker_dir(ag.bead_id, self.config.workers_dir)

    def get_ready_beads(self) -> list[dict]:
        """Fetch ready beads from bd."""
        result = self._bd("ready", "--json", "--limit", "10")
        if result.returncode != 0:
            logger.error("bd ready failed: %s", result.stderr)
            return []
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            logger.error("Failed to parse bd ready output: %s", result.stdout)
            return []

    def claim_bead(self, bead_id: str) -> bool:
        """Claim a bead so no other agent picks it up."""
        result = self._bd("update", bead_id, "--claim")
        if result.returncode != 0:
            logger.warning("Failed to claim %s: %s", bead_id, result.stderr)
            return False
        return True

    def close_bead(self, bead_id: str, reason: str) -> None:
        """Close a bead with a reason."""
        result = self._bd("close", bead_id, "--reason", reason)
        if result.returncode != 0:
            logger.warning("Failed to close %s: %s", bead_id, result.stderr)

    def _get_bead_metadata(self, bead: dict) -> dict:
        """Extract metadata from a bead, parsing JSON if needed."""
        meta = bead.get("metadata")
        if meta is None:
            return {}
        if isinstance(meta, str):
            try:
                return json.loads(meta)
            except json.JSONDecodeError:
                return {}
        return meta

    def _is_manual_bead(self, bead: dict) -> bool:
        """Check if a bead is a manual (human) bead."""
        meta = self._get_bead_metadata(bead)
        return meta.get("type") == "manual"

    def _resolve_workdir(self, bead: dict) -> tuple[Path | None, Path | None]:
        """Determine worktree path and cwd for an agent.

        Returns:
            (worktree_path_or_None, cwd) or (None, None) if bead has no repo.
        """
        meta = self._get_bead_metadata(bead)

        if wt := meta.get("worktree"):
            wt_path = Path(wt)
            if wt_path.is_dir():
                return None, wt_path
            logger.warning(
                "Bead %s has worktree metadata but path missing: %s",
                bead["id"], wt,
            )

        repo = meta.get("repo")
        if not repo:
            logger.error("Bead %s has no repo in metadata, skipping", bead["id"])
            return None, None
        branch = meta.get("branch", "main")

        try:
            wt_path = worktree.create_worktree(repo, branch, bead["id"])
            return wt_path, wt_path
        except subprocess.CalledProcessError as e:
            logger.error("Failed to create worktree for %s: %s", bead["id"], e.stderr)
            return None, Path(repo)

    def assign_bead(self, bead: dict) -> Agent | None:
        """Claim a bead, create worktree, and spawn an agent.

        Manual beads get a placeholder tmux window instead of a worker.
        """
        bead_id = bead["id"]

        if self._is_manual_bead(bead):
            if not self.claim_bead(bead_id):
                return None
            # Create a placeholder tmux window with a waiting message
            desc = bead.get("description", "")
            msg = f"echo 'Waiting for human — run: ishmael board {bead_id}'; echo '{desc}'; read -r -p \"Press Enter to dismiss...\" _"
            tmux_mod.create_window(
                session=self.config.tmux_session,
                name=bead_id,
                command=f"bash -c {_shell_quote(msg)}",
            )
            logger.info("Manual bead %s: placeholder window created", bead_id)
            return None

        if not self.claim_bead(bead_id):
            return None

        worktree_path, cwd = self._resolve_workdir(bead)
        if cwd is None:
            return None

        ag = agent_mod.spawn_agent(
            bead, worktree_path, cwd,
            beads_dir=self.config.beads_dir,
            workers_dir=self.config.workers_dir,
            tmux_session=self.config.tmux_session,
        )
        with self._agents_lock:
            self.agents.append(ag)

        logger.info("Assigned bead %s to agent", bead_id)
        return ag

    def get_agent(self, bead_id: str) -> Agent | None:
        """Look up a running agent by bead ID."""
        with self._agents_lock:
            for ag in self.agents:
                if ag.bead_id == bead_id:
                    return ag
        return None

    def poll_agents(self) -> None:
        """Check all running agents for completion."""
        with self._agents_lock:
            snapshot = list(self.agents)

        still_running = []
        for ag in snapshot:
            state = agent_mod.poll_agent(ag)

            if state == AgentState.RUNNING:
                still_running.append(ag)
                continue

            if state == AgentState.KILLED and ag.pid and agent_mod._pid_alive(ag.pid):
                still_running.append(ag)
                continue

            if state == AgentState.COMPLETED:
                self.completed_count += 1
                logger.info("Agent for %s completed", ag.bead_id)
            elif state == AgentState.KILLED:
                logger.info("Agent for %s was killed", ag.bead_id)
            else:
                from .worker import read_meta, worker_dir
                wdir = worker_dir(ag.bead_id, self.config.workers_dir)
                meta = read_meta(wdir)
                if meta.get("status") == "running":
                    self._bd("update", ag.bead_id, "--status", "open")
                    logger.warning("Agent for %s crashed, resetting bead", ag.bead_id)
                else:
                    self.failed_count += 1
                    logger.warning("Agent for %s failed", ag.bead_id)

            agent_mod.cleanup_worker_dir(ag.bead_id, self.config.workers_dir)

        with self._agents_lock:
            newly_added = [a for a in self.agents if a not in snapshot]
            self.agents = still_running + newly_added

    def kill_agent(self, bead_id: str) -> bool:
        """Kill a running agent's worker process."""
        ag = self.get_agent(bead_id)
        if ag is None:
            return False
        agent_mod.kill_agent(ag)
        logger.info("Killed agent for %s", bead_id)
        return True

    def close_bead_and_kill(self, bead_id: str, reason: str) -> None:
        """Kill the agent (if running) and close the bead."""
        self.kill_agent(bead_id)
        self.close_bead(bead_id, reason)

    def get_all_beads(self) -> list[dict]:
        """Fetch all non-closed beads from bd."""
        result = self._bd("list", "--json", "--flat", "-n", "50")
        if result.returncode != 0:
            logger.error("bd list failed: %s", result.stderr)
            return []
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            logger.error("Failed to parse bd list output: %s", result.stdout)
            return []

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        """Format elapsed seconds as e.g. '5m 23s' or '1h 02m'."""
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, sec = divmod(s, 60)
        if m < 60:
            return f"{m}m {sec:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h {m:02d}m"

    def _write_status_files(self) -> None:
        """Write bead-list.txt and agent-status.txt for the dashboard panes."""
        now = time.time()

        # --- bead list ---
        beads = self.get_all_beads()
        lines = [f"{'ID':<16} {'Title':<30} {'Status':<12} {'Pri'}"]
        lines.append("-" * 63)
        for b in beads:
            lines.append(
                f"{b.get('id', '?'):<16} "
                f"{(b.get('title', '') or '')[:30]:<30} "
                f"{b.get('status', '?'):<12} "
                f"{b.get('priority', '?')}"
            )
        if not beads:
            lines.append("(no beads)")
        self._atomic_write(
            os.path.join(self.STATUS_DIR, "bead-list.txt"),
            "\n".join(lines) + "\n",
        )

        # --- agent status ---
        with self._agents_lock:
            agents_snapshot = list(self.agents)
        header = (
            f"Agents: {len(agents_snapshot)}/{self.config.max_agents} | "
            f"Done: {self.completed_count} | Failed: {self.failed_count}"
        )
        alines = [header, ""]
        if agents_snapshot:
            alines.append(f"{'ID':<16} {'Title':<30} {'Elapsed'}")
            alines.append("-" * 55)
            for ag in agents_snapshot:
                elapsed = self._format_elapsed(now - ag.started_at) if ag.started_at else "?"
                title = (ag.title or "")[:30]
                alines.append(f"{ag.bead_id:<16} {title:<30} {elapsed}")
        else:
            alines.append("(no running agents)")
        self._atomic_write(
            os.path.join(self.STATUS_DIR, "agent-status.txt"),
            "\n".join(alines) + "\n",
        )

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        """Write content to path atomically via tmp+rename."""
        dir_ = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            os.write(fd, content.encode())
            os.close(fd)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _print_status(self, ready_count: int) -> None:
        """Print a status line to stdout (visible in tmux window 0)."""
        agents_str = ", ".join(
            f"{a.bead_id}({a.state.value})" for a in self.agents
        ) or "none"
        print(
            f"[ishmael] agents={len(self.agents)}/{self.config.max_agents} "
            f"ready={ready_count} done={self.completed_count} "
            f"failed={self.failed_count} | {agents_str}",
            flush=True,
        )

    def poll_once(self) -> None:
        """Run a single poll cycle."""
        self.poll_agents()

        slots = self.config.max_agents - len(self.agents)
        ready_count = 0
        if slots > 0:
            ready = self.get_ready_beads()
            ready_count = len(ready)
            for bead in ready[:slots]:
                self.assign_bead(bead)
        else:
            ready = self.get_ready_beads()
            ready_count = len(ready)

        self._print_status(ready_count)
        self._write_status_files()

    def shutdown(self) -> None:
        """Kill all workers on exit."""
        logger.info("Shutting down; killing %d worker(s)", len(self.agents))
        for ag in self.agents:
            agent_mod.kill_agent(ag)

    def run(self) -> None:
        """Main orchestrator loop — prints status to stdout."""
        logger.info("Starting Ishmael orchestrator")
        print("[ishmael] Orchestrator started. Ctrl+C to stop.", flush=True)
        try:
            while True:
                self.poll_once()
                time.sleep(self.config.poll_interval)
        except KeyboardInterrupt:
            print("\n[ishmael] Shutting down...", flush=True)
        finally:
            self.shutdown()


def _shell_quote(s: str) -> str:
    """Single-quote a string for shell use."""
    return "'" + s.replace("'", "'\\''") + "'"
