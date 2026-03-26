"""Workflow template loading and instantiation.

Templates are YAML files in ~/.ishmael/templates/ that define chains of beads
with dependencies, e.g. create-story -> dev-story -> code-review -> validate.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class WorkflowStep:
    """A single step in a workflow template."""

    id: str
    title: str
    prompt: str = ""
    description: str = ""
    type: str = "auto"  # "auto" or "manual"
    blocked_by: list[str] = field(default_factory=list)


@dataclass
class WorkflowTemplate:
    """A workflow template that defines a chain of bead steps."""

    name: str
    description: str = ""
    params: dict[str, dict[str, str]] = field(default_factory=dict)
    steps: list[WorkflowStep] = field(default_factory=list)


def load_template(path: Path) -> WorkflowTemplate:
    """Parse a YAML template file into a WorkflowTemplate."""
    data = yaml.safe_load(path.read_text())

    params = {}
    for k, v in data.get("params", {}).items():
        if isinstance(v, dict):
            params[k] = v
        else:
            params[k] = {"description": str(v)}

    steps = []
    for s in data.get("steps", []):
        steps.append(WorkflowStep(
            id=s["id"],
            title=s.get("title", s["id"]),
            prompt=s.get("prompt", ""),
            description=s.get("description", ""),
            type=s.get("type", "auto"),
            blocked_by=s.get("blocked_by", []),
        ))

    return WorkflowTemplate(
        name=data.get("name", path.stem),
        description=data.get("description", ""),
        params=params,
        steps=steps,
    )


def list_templates(templates_dir: str) -> list[WorkflowTemplate]:
    """List all templates in the templates directory."""
    tdir = Path(templates_dir)
    if not tdir.is_dir():
        return []
    templates = []
    for p in sorted(tdir.glob("*.yaml")):
        try:
            templates.append(load_template(p))
        except Exception as e:
            logger.warning("Failed to load template %s: %s", p, e)
    for p in sorted(tdir.glob("*.yml")):
        try:
            templates.append(load_template(p))
        except Exception as e:
            logger.warning("Failed to load template %s: %s", p, e)
    return templates


def get_template(name: str, templates_dir: str) -> Optional[WorkflowTemplate]:
    """Get a template by name."""
    tdir = Path(templates_dir)
    for ext in ("yaml", "yml"):
        path = tdir / f"{name}.{ext}"
        if path.exists():
            return load_template(path)
    return None


def _render(text: str, params: dict[str, str]) -> str:
    """Simple string formatting with param substitution."""
    result = text
    for k, v in params.items():
        result = result.replace(f"{{{k}}}", v)
    return result


def _bd_env(beads_dir: Optional[str] = None) -> dict[str, str]:
    """Return env dict for bd subprocesses."""
    env = os.environ.copy()
    if beads_dir:
        env["BEADS_DIR"] = beads_dir
    elif "BEADS_DIR" not in env:
        env["BEADS_DIR"] = str(Path.home() / ".beads")
    return env


def instantiate_workflow(
    template: WorkflowTemplate,
    params: dict[str, str],
    repo: str,
    branch: str,
    beads_dir: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Create beads from a template, wiring up blocked_by dependencies.

    Returns a list of dicts with step_id, bead_id, title for each created bead.
    """
    env = _bd_env(beads_dir)
    repo_path = str(Path(repo).expanduser().resolve())

    # Map step_id -> bead_id as we create them
    step_to_bead: dict[str, str] = {}
    results: list[dict[str, Any]] = []

    for step in template.steps:
        title = _render(step.title, params)
        prompt = _render(step.prompt, params)
        description = _render(step.description, params)

        metadata: dict[str, Any] = {"repo": repo_path, "branch": branch}
        if step.type == "manual":
            metadata["type"] = "manual"

        # Create the bead
        cmd = [
            "bd", "create", title,
            "-p", "2",
            "-d", description or prompt,
            "--metadata", json.dumps(metadata),
            "--json",
        ]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("Failed to create bead for step %s: %s", step.id, result.stderr)
            results.append({"step_id": step.id, "error": result.stderr.strip()})
            continue

        try:
            bead = json.loads(result.stdout)
            bead_id = bead["id"]
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse bead output for step %s: %s", step.id, e)
            results.append({"step_id": step.id, "error": str(e)})
            continue

        step_to_bead[step.id] = bead_id

        # Wire up dependencies
        dep_errors = []
        for dep_step_id in step.blocked_by:
            dep_bead_id = step_to_bead.get(dep_step_id)
            if dep_bead_id:
                dep_result = subprocess.run(
                    ["bd", "dep", "add", bead_id, dep_bead_id],
                    env=env, capture_output=True, text=True,
                )
                if dep_result.returncode != 0:
                    dep_errors.append(f"{dep_step_id}: {dep_result.stderr.strip()}")
            else:
                dep_errors.append(f"{dep_step_id}: step not found/created")

        results.append({
            "step_id": step.id,
            "bead_id": bead_id,
            "title": title,
            "type": step.type,
            "dep_errors": dep_errors if dep_errors else None,
        })

    return results
