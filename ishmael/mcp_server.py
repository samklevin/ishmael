"""MCP server for the Ishmael agent orchestrator.

Exposes tools over stdio for creating, reading, and updating beads, plus
visibility into active agents.  Run via ``ishmael-mcp`` (see pyproject.toml).
All logging goes to stderr so stdout stays clean for the MCP JSON-RPC transport.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging → stderr (stdout is the MCP stdio transport)
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ishmael.mcp")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKTREE_BASE = Path.home() / ".worktrees"


def _bd_env() -> dict[str, str]:
    """Return env dict for bd subprocesses."""
    env = os.environ.copy()
    if "BEADS_DIR" not in env:
        env["BEADS_DIR"] = str(Path.home() / ".beads")
    return env


def _run_bd(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bd", *args],
        env=_bd_env(),
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP("ishmael")


@mcp.tool()
def create_bead(
    title: str,
    repo: str,
    branch: str,
    description: str,
    priority: int = 2,
) -> str:
    """Create a bead and an isolated git worktree for an agent to work in.

    Args:
        title: Short title for the bead.
        repo: Absolute path to the git repository.
        branch: Branch to base the worktree on.
        description: What the agent should do.
        priority: 0 (lowest) to 4 (highest), default 2.
    """

    # -- Validate inputs ---------------------------------------------------
    if not title or not title.strip():
        return "Error: title must be a non-empty string."
    if not repo or not repo.strip():
        return "Error: repo must be a non-empty string."
    if not branch or not branch.strip():
        return "Error: branch must be a non-empty string."
    if not description or not description.strip():
        return "Error: description must be a non-empty string."

    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        return f"Error: repo path does not exist: {repo_path}"
    if not (repo_path / ".git").exists():
        return f"Error: repo path is not a git repository: {repo_path}"

    if not isinstance(priority, int) or not (0 <= priority <= 4):
        return "Error: priority must be an integer between 0 and 4."

    # -- Create bead -------------------------------------------------------
    metadata = {"repo": str(repo_path), "branch": branch}
    result = _run_bd(
        "create",
        title,
        "-p", str(priority),
        "-d", description,
        "--metadata", json.dumps(metadata),
        "--json",
    )
    if result.returncode != 0:
        return f"Error creating bead: {result.stderr.strip()}"

    try:
        bead = json.loads(result.stdout)
        bead_id = bead["id"]
    except (json.JSONDecodeError, KeyError) as exc:
        return f"Error parsing bd output: {exc}\nRaw output: {result.stdout}"

    # -- Create worktree ---------------------------------------------------
    repo_name = repo_path.name
    wt_dir = WORKTREE_BASE / f"{repo_name}-{bead_id}"

    try:
        WORKTREE_BASE.mkdir(parents=True, exist_ok=True)
        worktree_branch = f"{bead_id}/{branch}"
        subprocess.run(
            ["git", "worktree", "add", "-b", worktree_branch, str(wt_dir), branch],
            cwd=repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        # Bead exists but worktree failed — leave a note so operators can see
        _run_bd("note", bead_id, "--", f"Worktree creation failed: {exc.stderr.strip()}")
        return (
            f"Error: bead {bead_id} created but worktree failed.\n"
            f"git stderr: {exc.stderr.strip()}"
        )

    # -- Update bead metadata with worktree path ---------------------------
    metadata["worktree"] = str(wt_dir)
    upd = _run_bd("update", bead_id, "--metadata", json.dumps(metadata))
    if upd.returncode != 0:
        logger.warning("Failed to update bead metadata: %s", upd.stderr)

    return (
        f"Bead created.\n"
        f"  id:       {bead_id}\n"
        f"  title:    {title}\n"
        f"  repo:     {repo_path}\n"
        f"  branch:   {branch}\n"
        f"  worktree: {wt_dir}\n"
        f"  priority: {priority}"
    )


@mcp.tool()
def get_bead(bead_id: str) -> str:
    """Get full details of a bead by ID.

    Returns all bead fields including status, assignee, description, notes,
    metadata, and timestamps.

    Args:
        bead_id: The bead ID (e.g. "bd-a3f8e9").
    """
    if not bead_id or not bead_id.strip():
        return "Error: bead_id must be a non-empty string."

    result = _run_bd("show", bead_id, "--json")
    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"

    try:
        bead = json.loads(result.stdout)
    except json.JSONDecodeError:
        return f"Error parsing bd output: {result.stdout}"

    return json.dumps(bead, indent=2)


@mcp.tool()
def list_beads(
    status: str = "",
    repo: str = "",
    assignee: str = "",
    limit: int = 20,
) -> str:
    """List beads with optional filters.

    Args:
        status: Filter by status — "open", "in_progress", "blocked", "deferred", or "closed". Empty for all open.
        repo: Filter to beads whose metadata contains this repo path.
        assignee: Filter by assignee name.
        limit: Max results to return (default 20).
    """
    args: list[str] = ["list", "--json", "--flat", "-n", str(limit)]
    if status:
        args += ["--status", status]
    if assignee:
        args += ["--assignee", assignee]
    if repo:
        args += ["--has-metadata-key", "repo"]

    result = _run_bd(*args)
    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"

    try:
        beads = json.loads(result.stdout)
    except json.JSONDecodeError:
        return f"Error parsing bd output: {result.stdout}"

    # If repo filter requested, do client-side filtering on metadata
    if repo:
        repo_resolved = str(Path(repo).expanduser().resolve())
        filtered = []
        for b in beads:
            meta = b.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    continue
            if isinstance(meta, dict) and meta.get("repo") == repo_resolved:
                filtered.append(b)
        beads = filtered

    if not beads:
        return "No beads found matching filters."

    return json.dumps(beads, indent=2)


@mcp.tool()
def update_bead(
    bead_id: str,
    description: str = "",
    priority: int = -1,
    status: str = "",
    assignee: str = "",
    labels_add: str = "",
    labels_remove: str = "",
    note: str = "",
    metadata: str = "",
) -> str:
    """Update an existing bead's fields.

    All fields except bead_id are optional — only provided fields are updated.

    Args:
        bead_id: The bead ID to update.
        description: New description text.
        priority: New priority (0-4). Pass -1 to leave unchanged.
        status: New status — "open", "in_progress", "blocked", "deferred", or "closed".
        assignee: New assignee name.
        labels_add: Comma-separated labels to add.
        labels_remove: Comma-separated labels to remove.
        note: Append a note to the bead (does not replace existing notes).
        metadata: JSON string of metadata to set (replaces existing metadata).
    """
    if not bead_id or not bead_id.strip():
        return "Error: bead_id must be a non-empty string."

    args: list[str] = ["update", bead_id]
    has_updates = False

    if description:
        args += ["-d", description]
        has_updates = True
    if priority >= 0:
        if not (0 <= priority <= 4):
            return "Error: priority must be between 0 and 4."
        args += ["-p", str(priority)]
        has_updates = True
    if status:
        args += ["--status", status]
        has_updates = True
    if assignee:
        args += ["--assignee", assignee]
        has_updates = True
    if labels_add:
        for label in labels_add.split(","):
            label = label.strip()
            if label:
                args += ["--add-label", label]
                has_updates = True
    if labels_remove:
        for label in labels_remove.split(","):
            label = label.strip()
            if label:
                args += ["--remove-label", label]
                has_updates = True
    if metadata:
        try:
            json.loads(metadata)
        except json.JSONDecodeError:
            return "Error: metadata must be a valid JSON string."
        args += ["--metadata", metadata]
        has_updates = True

    # Handle note separately via bd note (append, not replace)
    if note:
        note_result = _run_bd("note", bead_id, "--", note)
        if note_result.returncode != 0:
            return f"Error adding note: {note_result.stderr.strip()}"
        if not has_updates:
            return f"Note added to {bead_id}."

    if not has_updates:
        return "Error: no fields to update. Provide at least one field."

    result = _run_bd(*args)
    if result.returncode != 0:
        return f"Error updating bead: {result.stderr.strip()}"

    suffix = " Note also added." if note else ""
    return f"Bead {bead_id} updated.{suffix}"


@mcp.tool()
def list_active_agents() -> str:
    """List beads currently being worked on by agents (status=in_progress).

    Returns bead details including ID, title, assignee, repo, worktree path,
    and how long the agent has been working on it.
    """
    result = _run_bd("list", "--status", "in_progress", "--json", "--flat")
    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"

    try:
        beads = json.loads(result.stdout)
    except json.JSONDecodeError:
        return f"Error parsing bd output: {result.stdout}"

    if not beads:
        return "No active agents — no beads are in_progress."

    # Summarize each active agent
    summaries = []
    for b in beads:
        meta = b.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}

        summaries.append({
            "bead_id": b.get("id"),
            "title": b.get("title"),
            "assignee": b.get("assignee", ""),
            "repo": meta.get("repo", ""),
            "branch": meta.get("branch", ""),
            "worktree": meta.get("worktree", ""),
            "claimed_at": b.get("updated_at", ""),
        })

    return json.dumps(summaries, indent=2)


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
