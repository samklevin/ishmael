"""Agent runner: spawns workers in tmux windows and tracks lifecycle."""

from __future__ import annotations

import json as _json
import logging
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from . import tmux as tmux_mod
from .worker import read_meta, worker_dir, write_meta

logger = logging.getLogger(__name__)

# Default workers dir
_DEFAULT_WORKERS_DIR = os.path.expanduser("~/.ishmael/workers")


class AgentState(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


@dataclass
class Agent:
    """Tracks a worker process running in a tmux window."""

    bead_id: str
    title: Optional[str] = None
    worktree_path: Optional[Path] = None
    repo_path: Optional[Path] = None
    pid: Optional[int] = None
    state: AgentState = AgentState.RUNNING
    started_at: Optional[float] = None
    _output_offset: int = field(default=0, repr=False)
    _workers_dir: str = field(default=_DEFAULT_WORKERS_DIR, repr=False)
    _tmux_session: Optional[str] = field(default=None, repr=False)


def build_prompt(bead: dict[str, Any]) -> str:
    """Build a prompt for claude from bead fields."""
    parts = [f"You are working on: {bead.get('title', 'Unknown task')}"]

    if desc := bead.get("description"):
        parts.append(f"\nDescription:\n{desc}")

    if acceptance := bead.get("acceptance"):
        parts.append(f"\nAcceptance Criteria:\n{acceptance}")

    if design := bead.get("design"):
        parts.append(f"\nDesign Notes:\n{design}")

    if notes := bead.get("notes"):
        parts.append(f"\nNotes:\n{notes}")

    parts.append(
        "\nWork in the current directory. When done, summarize what you did."
    )
    return "\n".join(parts)


def spawn_agent(
    bead: dict[str, Any],
    worktree_path: Optional[Path],
    cwd: Path,
    beads_dir: Optional[str] = None,
    workers_dir: str = _DEFAULT_WORKERS_DIR,
    tmux_session: str = "ishmael",
) -> Agent:
    """Spawn a worker process in a tmux window for the given bead."""
    bead_id = bead["id"]
    wdir = worker_dir(bead_id, workers_dir)
    wdir.mkdir(parents=True, exist_ok=True)

    # Write prompt file
    prompt = build_prompt(bead)
    prompt_file = wdir / "prompt.txt"
    prompt_file.write_text(prompt)

    # Build the worker command
    cmd_parts = [
        sys.executable, "-m", "ishmael.worker",
        bead_id,
        str(prompt_file),
        str(cwd),
        "--workers-dir", workers_dir,
    ]
    if beads_dir:
        cmd_parts.extend(["--beads-dir", beads_dir])
    if worktree_path:
        cmd_parts.extend(["--worktree-path", str(worktree_path)])

    meta = bead.get("metadata", {})
    if isinstance(meta, str):
        try:
            meta = _json.loads(meta)
        except Exception:
            meta = {}
    repo_path = meta.get("repo")
    if repo_path:
        cmd_parts.extend(["--repo-path", repo_path])

    # Shell-escape and join for tmux
    import shlex
    cmd_str = " ".join(shlex.quote(p) for p in cmd_parts)

    # Spawn in a tmux window
    tmux_mod.create_window(
        session=tmux_session,
        name=bead_id,
        command=cmd_str,
        cwd=str(cwd),
    )

    # PID is written asynchronously by the worker; we'll read it lazily
    return Agent(
        bead_id=bead_id,
        title=bead.get("title"),
        worktree_path=worktree_path,
        repo_path=Path(repo_path) if repo_path else None,
        pid=None,
        state=AgentState.RUNNING,
        started_at=time.time(),
        _workers_dir=workers_dir,
        _tmux_session=tmux_session,
    )


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_ishmael_worker(pid: int) -> bool:
    """Verify a PID is actually an ishmael worker (guards against PID reuse)."""
    if not _pid_alive(pid):
        return False
    try:
        import subprocess
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        return "ishmael.worker" in result.stdout
    except Exception:
        return _pid_alive(pid)


def _read_pid(agent: Agent) -> Optional[int]:
    """Lazily read the PID file written by the worker."""
    if agent.pid is not None:
        return agent.pid
    wdir = worker_dir(agent.bead_id, agent._workers_dir)
    pid_file = wdir / "pid"
    if pid_file.exists():
        try:
            agent.pid = int(pid_file.read_text().strip())
        except ValueError:
            pass
    return agent.pid


# Grace period (seconds) before declaring a PID-less agent as failed
_SPAWN_GRACE = 10.0


def poll_agent(agent: Agent) -> AgentState:
    """Check if an agent's worker process has completed."""
    if agent.state != AgentState.RUNNING:
        return agent.state

    wdir = worker_dir(agent.bead_id, agent._workers_dir)
    meta = read_meta(wdir)
    status = meta.get("status", "running")

    if status == "completed":
        agent.state = AgentState.COMPLETED
    elif status == "failed":
        agent.state = AgentState.FAILED
    elif status == "killed":
        agent.state = AgentState.KILLED
    else:
        # Still "running" — check for crashes
        pid = _read_pid(agent)
        if pid and not _pid_alive(pid):
            # PID dead but meta still says running — hard crash
            agent.state = AgentState.FAILED
        elif not pid:
            # PID not yet written — check grace period
            started = meta.get("started_at")
            if not started:
                # No meta at all yet — also check if tmux window is gone
                if agent._tmux_session and not tmux_mod.window_exists(agent._tmux_session, agent.bead_id):
                    agent.state = AgentState.FAILED
            # If within grace period, leave as RUNNING
        # Also check if tmux window disappeared
        elif agent._tmux_session and not tmux_mod.window_exists(agent._tmux_session, agent.bead_id):
            agent.state = AgentState.FAILED

    return agent.state


def kill_agent(agent: Agent) -> None:
    """Send SIGTERM to the worker process group and kill the tmux window."""
    pid = _read_pid(agent)
    if pid:
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    # Also kill the tmux window
    if agent._tmux_session:
        tmux_mod.kill_window(agent._tmux_session, agent.bead_id)

    agent.state = AgentState.KILLED


def read_new_output(agent: Agent) -> list[str]:
    """Read new output lines from the worker's output.log since last read."""
    wdir = worker_dir(agent.bead_id, agent._workers_dir)
    output_path = wdir / "output.log"
    try:
        with open(output_path, "r") as f:
            f.seek(agent._output_offset)
            data = f.read()
            agent._output_offset = f.tell()
    except FileNotFoundError:
        return []

    if not data:
        return []
    return data.splitlines()


def reconnect_agents(workers_dir: str = _DEFAULT_WORKERS_DIR) -> list[Agent]:
    """Scan worker directories and reconstruct Agent objects."""
    base = Path(workers_dir)
    if not base.is_dir():
        return []

    agents = []
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        bead_id = entry.name
        meta = read_meta(entry)
        if not meta:
            continue

        pid_file = entry / "pid"
        pid = None
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
            except ValueError:
                pass

        status = meta.get("status", "running")
        state_map = {
            "running": AgentState.RUNNING,
            "completed": AgentState.COMPLETED,
            "failed": AgentState.FAILED,
            "killed": AgentState.KILLED,
        }
        state = state_map.get(status, AgentState.FAILED)

        wt = meta.get("worktree_path")
        rp = meta.get("repo_path")

        started = meta.get("started_at")
        agents.append(Agent(
            bead_id=bead_id,
            title=meta.get("title"),
            worktree_path=Path(wt) if wt else None,
            repo_path=Path(rp) if rp else None,
            pid=pid,
            state=state,
            started_at=started,
            _workers_dir=workers_dir,
        ))

    return agents


def cleanup_worker_dir(bead_id: str, workers_dir: str = _DEFAULT_WORKERS_DIR) -> None:
    """Remove the worker directory for a bead."""
    wdir = worker_dir(bead_id, workers_dir)
    if wdir.is_dir():
        shutil.rmtree(wdir, ignore_errors=True)
