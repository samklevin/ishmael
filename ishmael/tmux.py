"""Stateless wrapper around the tmux CLI."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class TmuxWindow:
    """A tmux window."""

    index: int
    name: str
    active: bool


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a tmux command."""
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
    )


def session_exists(session: str) -> bool:
    """Check if a tmux session exists."""
    result = _run("has-session", "-t", session)
    return result.returncode == 0


def create_session(session: str, window_name: str, command: str) -> None:
    """Create a new detached tmux session with the given command in window 0."""
    _run(
        "new-session", "-d",
        "-s", session,
        "-n", window_name,
        command,
    )


def attach_session(session: str) -> None:
    """Attach to a tmux session, replacing the current process.

    If already inside tmux ($TMUX set), uses switch-client instead.
    """
    if os.environ.get("TMUX"):
        os.execvp("tmux", ["tmux", "switch-client", "-t", session])
    else:
        os.execvp("tmux", ["tmux", "attach-session", "-t", session])


def create_window(
    session: str,
    name: str,
    command: str,
    cwd: Optional[str] = None,
) -> TmuxWindow:
    """Create a new window in the session. Kills stale window with same name first."""
    if window_exists(session, name):
        kill_window(session, name)

    args = ["new-window", "-t", session, "-n", name, "-d"]
    if cwd:
        args.extend(["-c", cwd])
    args.append(command)
    _run(*args)

    # Find the window we just created
    for w in list_windows(session):
        if w.name == name:
            return w
    # Fallback
    return TmuxWindow(index=-1, name=name, active=False)


def kill_window(session: str, name: str) -> bool:
    """Kill a window by name. Returns True if successful."""
    result = _run("kill-window", "-t", f"{session}:{name}")
    return result.returncode == 0


def select_window(session: str, name: str) -> bool:
    """Select (focus) a window by name. Returns True if successful."""
    result = _run("select-window", "-t", f"{session}:{name}")
    return result.returncode == 0


def list_windows(session: str) -> list[TmuxWindow]:
    """List all windows in a session."""
    result = _run(
        "list-windows", "-t", session,
        "-F", "#{window_index}\t#{window_name}\t#{window_active}",
    )
    if result.returncode != 0:
        return []
    windows = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            windows.append(TmuxWindow(
                index=int(parts[0]),
                name=parts[1],
                active=parts[2] == "1",
            ))
    return windows


def window_exists(session: str, name: str) -> bool:
    """Check if a window with the given name exists in the session."""
    for w in list_windows(session):
        if w.name == name:
            return True
    return False


def split_window(
    session: str,
    target: str,
    command: str,
    vertical: bool = False,
    percent: Optional[int] = None,
) -> None:
    """Split a tmux pane.

    Args:
        session: tmux session name.
        target: pane target (e.g. window name or pane id).
        command: shell command to run in the new pane.
        vertical: True for top/bottom split (-v), False for side-by-side (-h).
        percent: size of the new pane as a percentage.
    """
    args = ["split-window", "-t", f"{session}:{target}", "-d"]
    args.append("-v" if vertical else "-h")
    if percent is not None:
        args.extend(["-p", str(percent)])
    args.append(command)
    _run(*args)
