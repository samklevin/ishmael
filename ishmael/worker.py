"""Detached worker process: runs a Claude agent SDK query for a single bead.

Usage: python -m ishmael.worker <bead_id> <prompt_file> <cwd>
           [--beads-dir PATH] [--worktree-path PATH] [--repo-path PATH]

The worker is self-sufficient: it closes beads on success, adds notes on
failure, and handles SIGTERM gracefully.  IPC with the orchestrator is
file-based via ~/.ishmael/workers/<bead_id>/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default base dir — overridable via --workers-dir
_DEFAULT_WORKERS_DIR = os.path.expanduser("~/.ishmael/workers")


# ---------------------------------------------------------------------------
# Helpers (importable by agent.py)
# ---------------------------------------------------------------------------

def worker_dir(bead_id: str, workers_dir: str = _DEFAULT_WORKERS_DIR) -> Path:
    """Return the worker directory for a bead."""
    return Path(workers_dir) / bead_id


def read_meta(wdir: Path) -> dict:
    """Read meta.json from a worker directory (safe due to atomic writes)."""
    meta_path = wdir / "meta.json"
    try:
        return json.loads(meta_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_meta(wdir: Path, meta: dict) -> None:
    """Atomically write meta.json (tmp + rename)."""
    meta_path = wdir / "meta.json"
    fd, tmp = tempfile.mkstemp(dir=wdir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(meta, f)
        os.replace(tmp, meta_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# bd helper
# ---------------------------------------------------------------------------

def _bd(args: list[str], beads_dir: str | None) -> subprocess.CompletedProcess:
    """Run a bd command."""
    env = os.environ.copy()
    if beads_dir:
        env["BEADS_DIR"] = beads_dir
    return subprocess.run(
        ["bd", *args], env=env, capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# SDK-based worker
# ---------------------------------------------------------------------------

async def _run_sdk(
    bead_id: str,
    prompt: str,
    cwd: str,
    output_path: Path,
    shutdown: asyncio.Event,
    beads_dir: str | None = None,
) -> tuple[str, int, dict]:
    """Run the Claude agent SDK, streaming output to stdout and a log file.

    Returns (status, returncode, sdk_info).
    """
    from claude_agent_sdk import (
        query,
        ClaudeAgentOptions,
        AssistantMessage,
        ResultMessage,
        SystemMessage,
    )

    # Inherit MCP servers from user's ~/.claude.json
    from .config import load_user_mcp_servers, DEFAULT_ALLOWED_TOOLS
    mcp_servers = load_user_mcp_servers()
    logger.info("Inherited %d MCP server(s) from user config: %s",
                len(mcp_servers), list(mcp_servers.keys()))

    # Ensure ishmael-mcp is always present (overrides any user-defined "ishmael" key)
    ishmael_mcp_path = shutil.which("ishmael-mcp")
    if ishmael_mcp_path:
        mcp_env: dict[str, str] = {}
        if beads_dir:
            mcp_env["BEADS_DIR"] = beads_dir
        elif "BEADS_DIR" in os.environ:
            mcp_env["BEADS_DIR"] = os.environ["BEADS_DIR"]
        mcp_servers["ishmael"] = {
            "command": ishmael_mcp_path,
            "env": mcp_env,
        }
        logger.info("Configured ishmael MCP server at %s", ishmael_mcp_path)
    else:
        logger.info("ishmael-mcp not found on PATH; agents won't have MCP tools")

    # Build allowed_tools: base tools + mcp__<server>__ prefix for each server
    allowed_tools = list(DEFAULT_ALLOWED_TOOLS)
    for server_name in mcp_servers:
        allowed_tools.append(f"mcp__{server_name}")
    logger.info("Allowed tools: %s", allowed_tools)

    options = ClaudeAgentOptions(
        cwd=cwd,
        allowed_tools=allowed_tools,
        permission_mode="bypassPermissions",
        allow_dangerously_skip_permissions=True,
        max_turns=50,
        mcp_servers=mcp_servers if mcp_servers else None,
    )

    sdk_info: dict[str, Any] = {
        "session_id": None,
        "num_turns": 0,
        "total_cost_usd": None,
    }

    start_time = time.monotonic()

    try:
        with open(output_path, "a") as out:
            async for message in query(prompt=prompt, options=options):
                # Check for shutdown between messages
                if shutdown.is_set():
                    return "killed", -1, sdk_info

                if isinstance(message, SystemMessage):
                    if message.subtype == "init":
                        sdk_info["session_id"] = message.session_id
                        logger.info("SDK session: %s", message.session_id)
                    else:
                        logger.debug("SystemMessage subtype=%s", message.subtype)

                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        block_type = getattr(block, "type", None)
                        if block_type == "text":
                            text = block.text
                            sys.stdout.write(text)
                            sys.stdout.flush()
                            out.write(text)
                            out.flush()
                        elif block_type == "tool_use":
                            marker = f"\n[tool: {block.name}]\n"
                            sys.stdout.write(marker)
                            sys.stdout.flush()
                            out.write(marker)
                            out.flush()
                    sdk_info["num_turns"] += 1

                elif isinstance(message, ResultMessage):
                    result_text = f"\n--- Result ---\n{message.result}\n"
                    sys.stdout.write(result_text)
                    sys.stdout.flush()
                    out.write(result_text)
                    out.flush()

        sdk_info["duration_s"] = round(time.monotonic() - start_time, 1)
        return "completed", 0, sdk_info

    except asyncio.CancelledError:
        sdk_info["duration_s"] = round(time.monotonic() - start_time, 1)
        return "killed", -1, sdk_info

    except Exception as exc:
        sdk_info["duration_s"] = round(time.monotonic() - start_time, 1)
        sdk_info["error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("SDK query failed for bead %s", bead_id)
        return "failed", 1, sdk_info


# ---------------------------------------------------------------------------
# Async main
# ---------------------------------------------------------------------------

async def _async_main(args: argparse.Namespace) -> None:
    """Async entry point containing the main worker logic."""
    bead_id = args.bead_id
    wdir = worker_dir(bead_id, args.workers_dir)
    wdir.mkdir(parents=True, exist_ok=True)

    # Setup logging to worker.log
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        filename=str(wdir / "worker.log"),
    )

    # Write PID
    (wdir / "pid").write_text(str(os.getpid()))

    # Write initial meta
    meta: dict[str, Any] = {
        "status": "running",
        "error": None,
        "started_at": time.time(),
        "completed_at": None,
        "cwd": args.cwd,
        "worktree_path": args.worktree_path,
        "repo_path": args.repo_path,
    }
    write_meta(wdir, meta)

    prompt = Path(args.prompt_file).read_text()
    output_path = wdir / "output.log"

    # SIGTERM: set shutdown event and cancel the SDK task
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    sdk_task: asyncio.Task | None = None

    def _sigterm_handler() -> None:
        shutdown.set()
        if sdk_task is not None:
            sdk_task.cancel()

    loop.add_signal_handler(signal.SIGTERM, _sigterm_handler)

    try:
        sdk_task = asyncio.current_task()
        status, returncode, sdk_info = await _run_sdk(
            bead_id, prompt, args.cwd, output_path, shutdown,
            beads_dir=args.beads_dir,
        )
        meta["status"] = status
        meta["completed_at"] = time.time()
        # Store SDK info in meta
        meta["session_id"] = sdk_info.get("session_id")
        meta["num_turns"] = sdk_info.get("num_turns", 0)
        meta["total_cost_usd"] = sdk_info.get("total_cost_usd")
        meta["duration_s"] = sdk_info.get("duration_s")
        write_meta(wdir, meta)

        if status == "killed":
            logger.info("Worker killed for %s", bead_id)
            _bd(["note", bead_id, "--", "Killed by user"], args.beads_dir)
        elif status == "completed":
            # Close the bead — do NOT remove worktree (preserved for boarding)
            output_text = ""
            if output_path.exists():
                output_text = output_path.read_text()[-500:]
            _bd(
                ["close", bead_id, "--reason", f"Completed by agent. Output: {output_text}"],
                args.beads_dir,
            )
        else:
            # failed
            error_msg = sdk_info.get("error", f"SDK query failed (exit {returncode})")
            meta["error"] = error_msg
            write_meta(wdir, meta)
            _bd(["note", bead_id, "--", f"Agent failed: {error_msg[:500]}"], args.beads_dir)

    except asyncio.CancelledError:
        logger.info("Worker cancelled (SIGTERM) for %s", bead_id)
        meta["status"] = "killed"
        meta["completed_at"] = time.time()
        write_meta(wdir, meta)
        _bd(["note", bead_id, "--", "Killed by SIGTERM"], args.beads_dir)

    except Exception as exc:
        logger.exception("Worker failed for %s", bead_id)
        meta["status"] = "failed"
        meta["error"] = str(exc)
        meta["completed_at"] = time.time()
        write_meta(wdir, meta)
        _bd(["note", bead_id, "--", f"Agent failed: {str(exc)[:500]}"], args.beads_dir)


# ---------------------------------------------------------------------------
# Sync entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ishmael worker process")
    parser.add_argument("bead_id")
    parser.add_argument("prompt_file")
    parser.add_argument("cwd")
    parser.add_argument("--beads-dir", default=None)
    parser.add_argument("--workers-dir", default=_DEFAULT_WORKERS_DIR)
    parser.add_argument("--worktree-path", default=None)
    parser.add_argument("--repo-path", default=None)
    args = parser.parse_args()

    # Clean env vars that block nested Claude Code sessions
    os.environ.pop("CLAUDECODE", None)
    os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)

    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
