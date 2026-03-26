"""Detached worker process: runs a Claude CLI query for a single bead.

Usage: python -m ishmael.worker <bead_id> <prompt_file> <cwd>
           [--beads-dir PATH] [--worktree-path PATH] [--repo-path PATH]

The worker is self-sufficient: it closes beads on success, adds notes on
failure, and handles SIGTERM gracefully.  IPC with the orchestrator is
file-based via ~/.ishmael/workers/<bead_id>/.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
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
# Worker process main
# ---------------------------------------------------------------------------

def _bd(args: list[str], beads_dir: str | None) -> subprocess.CompletedProcess:
    """Run a bd command."""
    env = os.environ.copy()
    if beads_dir:
        env["BEADS_DIR"] = beads_dir
    return subprocess.run(
        ["bd", *args], env=env, capture_output=True, text=True,
    )


def _run_cli(
    bead_id: str,
    prompt: str,
    cwd: str,
    output_path: Path,
    shutdown: threading.Event,
) -> tuple[str, int]:
    """Run the Claude CLI, streaming output to stdout and a log file.

    Returns (status, returncode).
    """
    cli_path = shutil.which("claude")
    if not cli_path:
        raise RuntimeError("claude CLI not found on PATH")

    # Clean env vars that block nested Claude CLI sessions
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    proc = subprocess.Popen(
        [
            cli_path, "-p", prompt,
            "--name", f"bead-{bead_id}",
            "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep",
            "--max-turns", "50",
        ],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    with open(output_path, "a") as out:
        for line in proc.stdout:
            if shutdown.is_set():
                proc.terminate()
                proc.wait()
                return "killed", -1
            sys.stdout.write(line)
            sys.stdout.flush()
            out.write(line)
            out.flush()

    rc = proc.wait()
    return ("completed" if rc == 0 else "failed"), rc


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

    # SIGTERM: set flag + forward to child
    shutdown = threading.Event()
    _child_proc: list[subprocess.Popen] = []

    def _sigterm_handler(signum: int, frame: Any) -> None:
        shutdown.set()
        for p in _child_proc:
            try:
                p.terminate()
            except OSError:
                pass

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        status, returncode = _run_cli(
            bead_id, prompt, args.cwd, output_path, shutdown,
        )
        meta["status"] = status
        meta["completed_at"] = time.time()
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
            meta["error"] = f"claude exited with code {returncode}"
            write_meta(wdir, meta)
            _bd(["note", bead_id, "--", f"Agent failed (exit {returncode})"], args.beads_dir)

    except Exception as exc:
        logger.exception("Worker failed for %s", bead_id)
        meta["status"] = "failed"
        meta["error"] = str(exc)
        meta["completed_at"] = time.time()
        write_meta(wdir, meta)
        _bd(["note", bead_id, "--", f"Agent failed: {str(exc)[:500]}"], args.beads_dir)


if __name__ == "__main__":
    main()
