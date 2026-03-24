"""Agent runner: spawns claude -p in a subprocess and tracks lifecycle."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _find_claude() -> str:
    """Resolve the full path to the claude CLI.

    Needed because conda environments may not inherit nvm/asdf paths.
    """
    path = shutil.which("claude")
    if path:
        return path
    # Common locations
    for candidate in [
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".nvm" / "versions" / "node",
    ]:
        if candidate.is_file():
            return str(candidate)
        # Search nvm node versions
        if candidate.is_dir():
            for node_dir in sorted(candidate.iterdir(), reverse=True):
                claude_bin = node_dir / "bin" / "claude"
                if claude_bin.is_file():
                    return str(claude_bin)
    return "claude"  # fallback, hope PATH has it


CLAUDE_BIN = _find_claude()


class AgentState(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Agent:
    """Tracks a running claude agent."""

    bead_id: str
    process: subprocess.Popen
    worktree_path: Optional[Path]
    repo_path: Optional[Path] = None
    state: AgentState = AgentState.RUNNING
    output: str = ""
    error: str = ""
    output_lines: list[str] = field(default_factory=list)
    _output_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _reader_thread: Optional[threading.Thread] = field(default=None, repr=False)


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


def _stdout_reader(agent: Agent) -> None:
    """Read stdout line-by-line in a daemon thread, appending to output_lines."""
    if agent.process.stdout is None:
        return
    for line in agent.process.stdout:
        with agent._output_lock:
            agent.output_lines.append(line.rstrip("\n"))
    agent.process.stdout.close()


def run_agent(bead: dict[str, Any], worktree_path: Optional[Path], cwd: Path) -> Agent:
    """Spawn a claude -p subprocess for the given bead.

    Args:
        bead: Bead data dict (from bd show --json).
        worktree_path: Path to the git worktree (None if using repo directly).
        cwd: Working directory for the agent.

    Returns:
        An Agent instance with the running process.
    """
    prompt = build_prompt(bead)
    process = subprocess.Popen(
        [CLAUDE_BIN, "-p", prompt],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ag = Agent(
        bead_id=bead["id"],
        process=process,
        worktree_path=worktree_path,
    )
    thread = threading.Thread(target=_stdout_reader, args=(ag,), daemon=True)
    thread.start()
    ag._reader_thread = thread
    return ag


def poll_agent(agent: Agent) -> AgentState:
    """Check if an agent has completed. Updates agent state in place."""
    if agent.state != AgentState.RUNNING:
        return agent.state

    retcode = agent.process.poll()
    if retcode is None:
        return AgentState.RUNNING

    # Process finished — join reader thread and collect stderr
    if agent._reader_thread is not None:
        agent._reader_thread.join(timeout=5)
        if agent._reader_thread.is_alive():
            logger.warning("Reader thread for %s did not finish in time", agent.bead_id)
    with agent._output_lock:
        agent.output = "\n".join(agent.output_lines)
    stderr = agent.process.stderr
    agent.error = stderr.read() if stderr else ""
    if stderr:
        stderr.close()
    agent.state = AgentState.COMPLETED if retcode == 0 else AgentState.FAILED
    return agent.state
