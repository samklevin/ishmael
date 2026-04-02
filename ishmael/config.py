"""Configuration for the Ishmael orchestrator."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Default tools available to worker agents
DEFAULT_ALLOWED_TOOLS = [
    "Read", "Write", "Edit", "Bash", "Glob", "Grep",
    "Task", "WebSearch", "WebFetch",
]


@dataclass
class Config:
    """Orchestrator configuration."""

    max_agents: int = 3
    poll_interval: float = 5.0
    beads_dir: Optional[str] = None
    workers_dir: str = os.path.expanduser("~/.ishmael/workers")
    tmux_session: str = "ishmael"
    templates_dir: str = os.path.expanduser("~/.ishmael/templates")

    # SDK worker defaults (worker currently hardcodes these; will read from
    # config once we add config-file support to the worker CLI)
    max_turns: int = 50
    permission_mode: str = "bypassPermissions"
    allowed_tools: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_TOOLS))

    def bd_env(self) -> dict[str, str]:
        """Return environment variables for bd commands."""
        env = os.environ.copy()
        if self.beads_dir:
            env["BEADS_DIR"] = self.beads_dir
        elif "BEADS_DIR" not in env:
            env["BEADS_DIR"] = os.path.expanduser("~/.beads")
        return env


def load_user_mcp_servers() -> dict[str, Any]:
    """Load MCP server configs from the user's ~/.claude.json."""
    config_path = Path.home() / ".claude.json"
    try:
        data = json.loads(config_path.read_text())
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            return {}
        return dict(servers)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read MCP servers from %s: %s", config_path, exc)
        return {}
