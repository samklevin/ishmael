"""CLI entry point: python -m ishmael."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys

from .config import Config
from . import tmux as tmux_mod


def cmd_run(args: argparse.Namespace) -> None:
    """Start the orchestrator in a tmux session."""
    session = args.session

    if tmux_mod.session_exists(session):
        print(f"Session '{session}' already exists, attaching...")
        tmux_mod.attach_session(session)
        return  # attach_session uses execvp, so this is unreachable

    # Build the orchestrator command for window 0
    orch_cmd_parts = [
        sys.executable, "-m", "ishmael._orchestrator_main",
        "--max-agents", str(args.max_agents),
        "--poll-interval", str(args.poll_interval),
        "--session", session,
    ]
    if args.db:
        orch_cmd_parts.extend(["--db", args.db])
    if args.verbose:
        orch_cmd_parts.append("-v")

    orch_cmd = " ".join(shlex.quote(p) for p in orch_cmd_parts)

    # Create tmux session with orchestrator in window 0
    tmux_mod.create_session(session, "orchestrator", orch_cmd)
    print(f"Created tmux session '{session}', attaching...")
    tmux_mod.attach_session(session)


def cmd_board(args: argparse.Namespace) -> None:
    """Board a bead — switch to running agent or resume completed one."""
    session = args.session
    bead_id = args.bead_id

    # Check if there's a running tmux window for this bead
    if tmux_mod.session_exists(session) and tmux_mod.window_exists(session, bead_id):
        print(f"Agent running — switching to window '{bead_id}'...")
        if os.environ.get("TMUX"):
            # Already inside tmux — switch to the window
            tmux_mod.select_window(session, bead_id)
        else:
            # Outside tmux — attach and select the window
            tmux_mod.select_window(session, bead_id)
            tmux_mod.attach_session(session)
        return

    # Agent not running — try to resume in the worktree
    from .worker import read_meta, worker_dir
    wdir = worker_dir(bead_id, Config().workers_dir)
    meta = read_meta(wdir)
    worktree_path = meta.get("worktree_path") or meta.get("cwd")

    if not worktree_path:
        # Try bead metadata
        env = os.environ.copy()
        if args.db:
            env["BEADS_DIR"] = args.db
        result = subprocess.run(
            ["bd", "show", bead_id, "--json"],
            env=env, capture_output=True, text=True,
        )
        if result.returncode == 0:
            try:
                bead = json.loads(result.stdout)
                if isinstance(bead, list):
                    bead = bead[0]
                bead_meta = bead.get("metadata", {})
                if isinstance(bead_meta, str):
                    bead_meta = json.loads(bead_meta)
                worktree_path = bead_meta.get("worktree") or bead_meta.get("repo")
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

    if not worktree_path:
        print(f"Cannot find working directory for bead {bead_id}", file=sys.stderr)
        sys.exit(1)

    # Open claude --resume in the worktree
    print(f"Resuming session for bead {bead_id} in {worktree_path}...")
    os.chdir(worktree_path)
    os.execvp("claude", ["claude", "--resume", f"bead-{bead_id}"])


def cmd_status(args: argparse.Namespace) -> None:
    """Show current state."""
    env = os.environ.copy()
    if args.db:
        env["BEADS_DIR"] = args.db

    result = subprocess.run(
        ["bd", "status"],
        env=env,
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)


def cmd_add(args: argparse.Namespace) -> None:
    """Create a bead (wraps bd create)."""
    env = os.environ.copy()
    if args.db:
        env["BEADS_DIR"] = args.db

    cmd = ["bd", "create", args.title, "-p", str(args.priority)]

    metadata = {"repo": str(args.repo)}
    if args.branch:
        metadata["branch"] = args.branch
    cmd.extend(["--metadata", json.dumps(metadata)])

    if args.description:
        cmd.extend(["-d", args.description])

    result = subprocess.run(
        cmd,
        cwd=args.repo,
        env=env,
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(1)


def cmd_setup_skills(args: argparse.Namespace) -> None:
    """Symlink ishmael skills into ~/.claude/skills/ for global availability."""
    from pathlib import Path

    # Skills live alongside this package in the repo
    repo_root = Path(__file__).resolve().parent.parent
    src_skills = repo_root / ".claude" / "skills"
    dst_skills = Path.home() / ".claude" / "skills"

    if not src_skills.is_dir():
        print(f"Skills directory not found: {src_skills}", file=sys.stderr)
        sys.exit(1)

    dst_skills.mkdir(parents=True, exist_ok=True)

    for skill_dir in sorted(src_skills.iterdir()):
        if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").exists():
            continue
        link = dst_skills / skill_dir.name
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            print(f"  skip {skill_dir.name} (already exists and is not a symlink)")
            continue
        link.symlink_to(skill_dir)
        print(f"  {skill_dir.name} -> {skill_dir}")

    print("Done. Skills are now available globally in Claude Code.")


def cmd_workflow_list(args: argparse.Namespace) -> None:
    """List available workflow templates."""
    from .templates import list_templates

    templates_dir = args.templates_dir or Config().templates_dir
    templates = list_templates(templates_dir)

    if not templates:
        print(f"No templates found in {templates_dir}")
        return

    for t in templates:
        params_str = ", ".join(t.params.keys()) if t.params else "none"
        steps_str = " -> ".join(s.id for s in t.steps)
        print(f"  {t.name}: {t.description}")
        print(f"    params: {params_str}")
        print(f"    steps:  {steps_str}")
        print()


def cmd_workflow_run(args: argparse.Namespace) -> None:
    """Instantiate a workflow template."""
    from .templates import get_template, instantiate_workflow

    templates_dir = args.templates_dir or Config().templates_dir
    template = get_template(args.template, templates_dir)
    if not template:
        print(f"Template '{args.template}' not found in {templates_dir}", file=sys.stderr)
        sys.exit(1)

    # Parse --param key=value pairs
    params: dict[str, str] = {}
    for p in args.param or []:
        if "=" not in p:
            print(f"Invalid param format: {p} (expected key=value)", file=sys.stderr)
            sys.exit(1)
        k, v = p.split("=", 1)
        params[k] = v

    # Validate required params
    for k in template.params:
        if k not in params:
            print(f"Missing required param: {k}", file=sys.stderr)
            sys.exit(1)

    results = instantiate_workflow(
        template=template,
        params=params,
        repo=args.repo,
        branch=args.branch,
        beads_dir=args.db,
    )

    print(f"Workflow '{template.name}' instantiated:")
    for r in results:
        if "error" in r:
            print(f"  {r['step_id']}: ERROR - {r['error']}")
        else:
            type_tag = f" [{r['type']}]" if r.get("type") == "manual" else ""
            print(f"  {r['step_id']}: {r['bead_id']} — {r['title']}{type_tag}")
            if r.get("dep_errors"):
                for de in r["dep_errors"]:
                    print(f"    dep error: {de}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ishmael",
        description="Agent orchestrator using beads as task database",
    )
    parser.add_argument("--db", default=None, help="Beads DB path")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--session", default="ishmael", help="tmux session name")

    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Start the orchestrator (tmux)")
    p_run.add_argument("--max-agents", type=int, default=3)
    p_run.add_argument("--poll-interval", type=float, default=5.0)

    # board
    p_board = sub.add_parser("board", help="Board a bead (watch or resume)")
    p_board.add_argument("bead_id", help="Bead ID to board")

    # status
    sub.add_parser("status", help="Show current state")

    # add
    p_add = sub.add_parser("add", help="Create a bead")
    p_add.add_argument("title", help="Bead title")
    p_add.add_argument("--repo", required=True, help="Target repo path")
    p_add.add_argument("--branch", default=None, help="Git branch for worktree")
    p_add.add_argument("-p", "--priority", type=int, default=2)
    p_add.add_argument("-d", "--description", default=None)

    # setup-skills
    sub.add_parser("setup-skills", help="Symlink ishmael skills into ~/.claude/skills/")

    # workflow
    p_wf = sub.add_parser("workflow", help="Workflow template commands")
    wf_sub = p_wf.add_subparsers(dest="wf_command", required=True)

    # workflow list
    p_wf_list = wf_sub.add_parser("list", help="List available templates")
    p_wf_list.add_argument("--templates-dir", default=None)

    # workflow run
    p_wf_run = wf_sub.add_parser("run", help="Instantiate a workflow template")
    p_wf_run.add_argument("template", help="Template name")
    p_wf_run.add_argument("--repo", required=True, help="Target repo path")
    p_wf_run.add_argument("--branch", default="main", help="Git branch")
    p_wf_run.add_argument("--param", action="append", help="key=value param (repeatable)")
    p_wf_run.add_argument("--templates-dir", default=None)

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "board":
        cmd_board(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "setup-skills":
        cmd_setup_skills(args)
    elif args.command == "workflow":
        if args.wf_command == "list":
            cmd_workflow_list(args)
        elif args.wf_command == "run":
            cmd_workflow_run(args)


if __name__ == "__main__":
    main()
