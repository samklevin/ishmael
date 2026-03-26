"""Entry point for the orchestrator running in tmux window 0.

This is a separate module because `ishmael run` uses execvp to become the
tmux client, so the orchestrator must run as a distinct process inside the
tmux session.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from . import tmux as tmux_mod
from .config import Config
from .orchestrator import Orchestrator

STATUS_DIR = os.path.expanduser("~/.ishmael")


def _setup_dashboard(session: str) -> None:
    """Split window 0 into a 3-pane dashboard layout.

    Layout:
        Left (30%)  — bead list (watch)
        Right-top   — agent status (watch)
        Right-bottom — orchestrator log (current pane)
    """
    bead_list_cmd = f"bash -c 'while true; do clear; cat {STATUS_DIR}/bead-list.txt 2>/dev/null; sleep 5; done'"
    agent_status_cmd = f"bash -c 'while true; do clear; cat {STATUS_DIR}/agent-status.txt 2>/dev/null; sleep 5; done'"

    # Seed status files so watch doesn't error before the first poll
    os.makedirs(STATUS_DIR, exist_ok=True)
    for fname in ("bead-list.txt", "agent-status.txt"):
        path = os.path.join(STATUS_DIR, fname)
        if not os.path.exists(path):
            with open(path, "w") as f:
                f.write("(waiting for first poll...)\n")

    # Step 1: side-by-side split — new pane (bead list) appears on right (.1)
    tmux_mod.split_window(session, "orchestrator", bead_list_cmd, vertical=False, percent=66)
    time.sleep(0.3)

    # Swap so bead list moves to the left (.0)
    tmux_mod._run("swap-pane", "-t", f"{session}:orchestrator.0", "-s", f"{session}:orchestrator.1")
    time.sleep(0.1)

    # Step 2: split right pane (.1 = orchestrator) vertically — new pane below
    tmux_mod._run(
        "split-window", "-t", f"{session}:orchestrator.1",
        "-v", "-d", "-p", "50",
        agent_status_cmd,
    )

    # Swap so agent-status is on top (.1) and orchestrator on bottom (.2)
    tmux_mod._run("swap-pane", "-t", f"{session}:orchestrator.1", "-s", f"{session}:orchestrator.2")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ishmael orchestrator (tmux window 0)")
    parser.add_argument("--max-agents", type=int, default=3)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--db", default=None, help="Beads DB path")
    parser.add_argument("--session", default="ishmael", help="tmux session name")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = Config(
        max_agents=args.max_agents,
        poll_interval=args.poll_interval,
        beads_dir=args.db,
        tmux_session=args.session,
    )

    _setup_dashboard(args.session)

    orch = Orchestrator(config)
    orch.run()


if __name__ == "__main__":
    main()
