"""Git worktree management."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Union

WORKTREE_BASE = Path.home() / ".worktrees"


def _ref_exists(repo: Path, ref: str) -> bool:
    """Check if a git ref (branch, tag, commit) exists."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def create_worktree(repo_path: Union[str, Path], branch: str, worktree_name: str) -> Path:
    """Create a git worktree and return its path.

    Worktrees are created under ~/.worktrees/<repo_name>-<worktree_name>/.
    If ``branch`` does not exist as a ref, the worktree is based off HEAD.
    """
    repo = Path(repo_path).resolve()
    worktree_dir = WORKTREE_BASE / f"{repo.name}-{worktree_name}"
    WORKTREE_BASE.mkdir(parents=True, exist_ok=True)

    # Determine the base ref: use branch if it exists, otherwise HEAD
    base_ref = branch if _ref_exists(repo, branch) else "HEAD"

    worktree_branch = f"{worktree_name}/{branch}"
    subprocess.run(
        ["git", "worktree", "add", "-b", worktree_branch, str(worktree_dir), base_ref],
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
