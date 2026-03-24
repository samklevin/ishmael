"""Git worktree management."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Union


def create_worktree(repo_path: Union[str, Path], branch: str, worktree_name: str) -> Path:
    """Create a git worktree and return its path.

    Worktrees are created under <repo>/.worktrees/<worktree_name>/.
    """
    repo = Path(repo_path).resolve()
    worktree_dir = repo / ".worktrees" / worktree_name
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    worktree_branch = f"{worktree_name}/{branch}"
    subprocess.run(
        ["git", "worktree", "add", "-b", worktree_branch, str(worktree_dir), branch],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return worktree_dir


def remove_worktree(repo_path: Union[str, Path], worktree_path: Union[str, Path]) -> None:
    """Remove a git worktree and clean up."""
    repo = Path(repo_path).resolve()
    worktree = Path(worktree_path).resolve()

    subprocess.run(
        ["git", "worktree", "remove", str(worktree), "--force"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
