"""CLI entry point: python -m ishmael."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys

from .config import Config
from .tui import IshmaelApp


def cmd_run(args: argparse.Namespace) -> None:
    """Start the orchestrator TUI."""
    config = Config(
        max_agents=args.max_agents,
        poll_interval=args.poll_interval,
        beads_dir=args.db,
    )
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = IshmaelApp(config)
    app.run()


def cmd_status(args: argparse.Namespace) -> None:
    """Show current state."""
    import os

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
    import os

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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ishmael",
        description="Agent orchestrator using beads as task database",
    )
    parser.add_argument("--db", default=None, help="Beads DB path")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Start the orchestrator")
    p_run.add_argument("--max-agents", type=int, default=3)
    p_run.add_argument("--poll-interval", type=float, default=5.0)

    # status
    sub.add_parser("status", help="Show current state")

    # add
    p_add = sub.add_parser("add", help="Create a bead")
    p_add.add_argument("title", help="Bead title")
    p_add.add_argument("--repo", required=True, help="Target repo path")
    p_add.add_argument("--branch", default=None, help="Git branch for worktree")
    p_add.add_argument("-p", "--priority", type=int, default=2)
    p_add.add_argument("-d", "--description", default=None)

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "add":
        cmd_add(args)


if __name__ == "__main__":
    main()
