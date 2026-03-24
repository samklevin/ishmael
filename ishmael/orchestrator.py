"""Main orchestrator loop: poll bd ready, assign to agents, manage lifecycle."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import agent as agent_mod
from . import worktree
from .agent import Agent, AgentState
from .config import Config

logger = logging.getLogger(__name__)

# Callback type aliases
EventCallback = Callable[..., Any]


class Orchestrator:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.agents: list[Agent] = []
        self._agents_lock = threading.Lock()
        self.completed_count = 0
        self.failed_count = 0

        # Callbacks (set by TUI or other consumers)
        self.on_agent_started: Optional[EventCallback] = None
        self.on_agent_completed: Optional[EventCallback] = None
        self.on_agent_failed: Optional[EventCallback] = None
        self.on_state_changed: Optional[EventCallback] = None

    def _bd(self, *args: str) -> subprocess.CompletedProcess:
        """Run a bd command and return the result."""
        return subprocess.run(
            ["bd", *args],
            env=self.config.bd_env(),
            capture_output=True,
            text=True,
        )

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

    def _resolve_workdir(self, bead: dict) -> tuple[Path | None, Path | None]:
        """Determine worktree path and cwd for an agent.

        Returns:
            (worktree_path_or_None, cwd) or (None, None) if bead has no repo.
        """
        meta = self._get_bead_metadata(bead)

        # If the bead specifies a pre-existing worktree, verify it exists
        if wt := meta.get("worktree"):
            wt_path = Path(wt)
            if wt_path.is_dir():
                return None, wt_path
            logger.warning(
                "Bead %s has worktree metadata but path missing: %s",
                bead["id"], wt,
            )
            # Fall through to create a new one

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
            # Fall back to repo directly
            return None, Path(repo)

    def assign_bead(self, bead: dict) -> Agent | None:
        """Claim a bead, create worktree, and spawn an agent."""
        bead_id = bead["id"]
        if not self.claim_bead(bead_id):
            return None

        worktree_path, cwd = self._resolve_workdir(bead)
        if cwd is None:
            return None

        meta = self._get_bead_metadata(bead)
        repo = meta.get("repo")

        # Spawn the agent
        ag = agent_mod.run_agent(bead, worktree_path, cwd)
        ag.repo_path = Path(repo) if repo else None
        with self._agents_lock:
            self.agents.append(ag)

        logger.info("Assigned bead %s to agent", bead_id)
        if self.on_agent_started:
            self.on_agent_started(bead_id)
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
        still_running = []
        for ag in self.agents:
            state = agent_mod.poll_agent(ag)

            if state == AgentState.RUNNING:
                still_running.append(ag)
                continue

            # Agent finished
            if state == AgentState.COMPLETED:
                reason = f"Completed by agent. Output: {ag.output[:500]}"
                self.close_bead(ag.bead_id, reason)
                self.completed_count += 1
                logger.info("Agent for %s completed", ag.bead_id)
                if self.on_agent_completed:
                    self.on_agent_completed(ag.bead_id, ag.output)
            else:
                reason = f"Agent failed. Error: {ag.error[:500]}"
                self._bd("note", ag.bead_id, "--", reason)
                self.failed_count += 1
                logger.warning("Agent for %s failed: %s", ag.bead_id, ag.error[:200])
                if self.on_agent_failed:
                    self.on_agent_failed(ag.bead_id, ag.error)

            # Cleanup worktree
            if ag.worktree_path and ag.repo_path:
                try:
                    worktree.remove_worktree(
                        ag.repo_path, ag.worktree_path
                    )
                except subprocess.CalledProcessError:
                    logger.warning(
                        "Failed to remove worktree %s", ag.worktree_path
                    )

        with self._agents_lock:
            self.agents = still_running

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

    def dashboard_state(self, beads: list[dict] | None = None) -> dict:
        """Build state dict for the dashboard."""
        return {
            "active": len(self.agents),
            "max": self.config.max_agents,
            "ready": "?",  # Updated each cycle
            "completed": self.completed_count,
            "failed": self.failed_count,
            "agents": [
                {"bead_id": a.bead_id, "state": a.state.value}
                for a in self.agents
            ],
            "beads": beads or [],
        }

    def poll_once(self) -> dict:
        """Run a single poll cycle. Returns dashboard state dict."""
        # Poll existing agents
        self.poll_agents()

        # Fetch all beads for the dashboard
        all_beads = self.get_all_beads()

        # Fetch and assign new beads if we have capacity
        slots = self.config.max_agents - len(self.agents)
        if slots > 0:
            ready = self.get_ready_beads()
            state = self.dashboard_state(beads=all_beads)
            state["ready"] = len(ready)

            for bead in ready[:slots]:
                self.assign_bead(bead)
        else:
            state = self.dashboard_state(beads=all_beads)

        if self.on_state_changed:
            self.on_state_changed(state)

        return state

    def shutdown(self) -> None:
        """Kill any remaining agent processes."""
        for ag in self.agents:
            if ag.process.poll() is None:
                ag.process.terminate()

    def run(self) -> None:
        """Main orchestrator loop (for headless/non-TUI use)."""
        logger.info("Starting Ishmael orchestrator")
        try:
            while True:
                self.poll_once()
                time.sleep(self.config.poll_interval)
        except KeyboardInterrupt:
            logger.info("Shutting down orchestrator")
        finally:
            self.shutdown()
