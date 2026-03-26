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

from .worktree import WORKTREE_BASE

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
    blocked_by: str = "",
    worktree: str = "",
) -> str:
    """Create a bead and an isolated git worktree for an agent to work in.

    Args:
        title: Short title for the bead.
        repo: Absolute path to the git repository.
        branch: Branch to base the worktree on.
        description: What the agent should do.
        priority: 0 (lowest) to 4 (highest), default 2.
        blocked_by: Comma-separated bead IDs that must close before this bead becomes ready.
        worktree: Optional path to an existing worktree to reuse. When creating
            a chain of dependent beads, pass the worktree from the first bead so
            all beads in the chain share the same working directory.
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

    # -- Create or reuse worktree ------------------------------------------
    if worktree:
        wt_dir = Path(worktree).expanduser().resolve()
    else:
        repo_name = repo_path.name
        wt_dir = WORKTREE_BASE / f"{repo_name}-{bead_id}"

    if not wt_dir.is_dir():
        try:
            wt_dir.parent.mkdir(parents=True, exist_ok=True)
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

    # -- Wire up dependencies (blocked_by) ---------------------------------
    dep_errors: list[str] = []
    blocker_ids: list[str] = []
    if blocked_by:
        for raw_id in blocked_by.split(","):
            blocker = raw_id.strip()
            if not blocker:
                continue
            blocker_ids.append(blocker)
            dep_result = _run_bd("dep", "add", bead_id, blocker)
            if dep_result.returncode != 0:
                dep_errors.append(f"  dep {blocker}: {dep_result.stderr.strip()}")

    summary = (
        f"Bead created.\n"
        f"  id:         {bead_id}\n"
        f"  title:      {title}\n"
        f"  repo:       {repo_path}\n"
        f"  branch:     {branch}\n"
        f"  worktree:   {wt_dir}\n"
        f"  priority:   {priority}"
    )
    if blocker_ids:
        summary += f"\n  blocked_by: {', '.join(blocker_ids)}"
    if dep_errors:
        summary += "\n  dep errors:\n" + "\n".join(dep_errors)
    return summary


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
def retry_bead(bead_id: str) -> str:
    """Close a stuck/failed bead and recreate it with the same title, description, and priority.

    Cleans up the old worktree (if any) and creates a fresh one. Use this when
    an agent failed and you want to re-queue the work.

    Args:
        bead_id: The bead ID to retry (e.g. "samuellevin-82u").
    """
    if not bead_id or not bead_id.strip():
        return "Error: bead_id must be a non-empty string."

    # Fetch the original bead
    result = _run_bd("show", bead_id, "--json")
    if result.returncode != 0:
        return f"Error fetching bead: {result.stderr.strip()}"

    try:
        bead = json.loads(result.stdout)
        if isinstance(bead, list):
            bead = bead[0]
    except (json.JSONDecodeError, IndexError) as exc:
        return f"Error parsing bead: {exc}"

    title = bead.get("title", "")
    description = bead.get("description", "")
    priority = bead.get("priority", 2)

    meta = bead.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}

    repo = meta.get("repo", "")
    branch = meta.get("branch", "main")
    old_worktree = meta.get("worktree", "")

    if not repo:
        return f"Error: bead {bead_id} has no repo in metadata, cannot retry."

    # Close the old bead
    close_result = _run_bd("close", bead_id, "--reason", "Retrying — closed for re-creation")
    if close_result.returncode != 0:
        # May already be closed
        logger.warning("Failed to close %s: %s", bead_id, close_result.stderr)

    # Clean up old worktree
    if old_worktree:
        wt_path = Path(old_worktree)
        if wt_path.is_dir():
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(wt_path)],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                )
            except Exception as exc:
                logger.warning("Failed to remove old worktree %s: %s", wt_path, exc)

    # Create new bead via the existing create_bead tool
    return create_bead(
        title=title,
        repo=repo,
        branch=branch,
        description=description,
        priority=priority,
    )


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


@mcp.tool()
def list_templates() -> str:
    """List available workflow templates from ~/.ishmael/templates/.

    Returns template names, descriptions, parameters, and step chains.
    """
    from .config import Config
    from .templates import list_templates as _list_templates

    config = Config()
    templates = _list_templates(config.templates_dir)
    if not templates:
        return f"No templates found in {config.templates_dir}"

    result = []
    for t in templates:
        result.append({
            "name": t.name,
            "description": t.description,
            "params": {k: v.get("description", "") for k, v in t.params.items()},
            "steps": [
                {"id": s.id, "title": s.title, "type": s.type, "blocked_by": s.blocked_by}
                for s in t.steps
            ],
        })
    return json.dumps(result, indent=2)


@mcp.tool()
def instantiate_workflow(
    template_name: str,
    repo: str,
    branch: str,
    params: str = "",
) -> str:
    """Instantiate a workflow template, creating beads with dependencies.

    Args:
        template_name: Name of the template (filename without .yaml).
        repo: Absolute path to the git repository.
        branch: Branch to base worktrees on.
        params: JSON string of template parameters, e.g. '{"story_id": "2-1"}'.
    """
    from .config import Config
    from .templates import get_template, instantiate_workflow as _instantiate

    config = Config()
    template = get_template(template_name, config.templates_dir)
    if not template:
        return f"Error: template '{template_name}' not found in {config.templates_dir}"

    parsed_params: dict[str, str] = {}
    if params:
        try:
            parsed_params = json.loads(params)
        except json.JSONDecodeError:
            return "Error: params must be a valid JSON string."

    # Validate required params
    missing = [k for k in template.params if k not in parsed_params]
    if missing:
        return f"Error: missing required params: {', '.join(missing)}"

    results = _instantiate(
        template=template,
        params=parsed_params,
        repo=repo,
        branch=branch,
    )

    return json.dumps(results, indent=2)


@mcp.tool()
def add_dependency(bead_id: str, depends_on: str) -> str:
    """Add a dependency so that bead_id is blocked by depends_on.

    The bead will not appear in ``bd ready`` until depends_on is closed.

    Args:
        bead_id: The bead that should wait.
        depends_on: The bead that must complete first.
    """
    if not bead_id or not bead_id.strip():
        return "Error: bead_id must be a non-empty string."
    if not depends_on or not depends_on.strip():
        return "Error: depends_on must be a non-empty string."

    result = _run_bd("dep", "add", bead_id.strip(), depends_on.strip())
    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"
    return f"Dependency added: {bead_id} is now blocked by {depends_on}."


@mcp.tool()
def remove_dependency(bead_id: str, depends_on: str) -> str:
    """Remove a dependency so that bead_id is no longer blocked by depends_on.

    Args:
        bead_id: The bead to unblock.
        depends_on: The blocker to remove.
    """
    if not bead_id or not bead_id.strip():
        return "Error: bead_id must be a non-empty string."
    if not depends_on or not depends_on.strip():
        return "Error: depends_on must be a non-empty string."

    result = _run_bd("dep", "remove", bead_id.strip(), depends_on.strip())
    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"
    return f"Dependency removed: {bead_id} is no longer blocked by {depends_on}."


@mcp.tool()
def list_dependencies(bead_id: str) -> str:
    """List what blocks a bead (its dependencies).

    Args:
        bead_id: The bead to query.
    """
    if not bead_id or not bead_id.strip():
        return "Error: bead_id must be a non-empty string."

    result = _run_bd("dep", "list", bead_id.strip(), "--json")
    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"

    output = result.stdout.strip()
    if not output:
        return f"No dependencies for {bead_id}."

    try:
        deps = json.loads(output)
    except json.JSONDecodeError:
        return f"Dependencies for {bead_id}:\n{output}"

    if not deps:
        return f"No dependencies for {bead_id}."

    return json.dumps(deps, indent=2)


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
