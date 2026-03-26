"""Configuration for the Ishmael orchestrator."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    """Orchestrator configuration."""

    max_agents: int = 3
    poll_interval: float = 5.0
    beads_dir: Optional[str] = None
    workers_dir: str = os.path.expanduser("~/.ishmael/workers")
    tmux_session: str = "ishmael"
    templates_dir: str = os.path.expanduser("~/.ishmael/templates")

    def bd_env(self) -> dict[str, str]:
        """Return environment variables for bd commands."""
        env = os.environ.copy()
        if self.beads_dir:
            env["BEADS_DIR"] = self.beads_dir
        elif "BEADS_DIR" not in env:
            env["BEADS_DIR"] = os.path.expanduser("~/.beads")
        return env
