"""ENGPLAT-003B Slice 1 — Persistent Project Registry Loader.

Architecture
=============
The registry is a versioned JSON file containing an ordered list of
ProjectConfig-compatible mappings.  This module provides read-only loading;
registry mutation (add / remove / update projects) is out of scope for Slice 1.

Registry file location
----------------------
``~/.openclaw/engineering-registry.json``

This path is outside both the platform workspace and any managed repository,
so it is not entangled in any project's git history.

Registry format
---------------
::

    {
        "registry_version": "1",
        "schema_version": "1.0",
        "projects": [
            { ... ProjectConfig mapping ... },
            ...
        ]
    }

``registry_version`` is the registry format version (loader concern).
``schema_version`` inside each project entry is the ProjectConfig schema
version (passed to ``parse_project_config``; must be "1.0").

No project_id special-casing
-----------------------------
This module contains zero ``if project_id == ...`` branches.  The loader is
purely structural; semantic differences between projects are handled by the
existing ``validate_project_config`` call on each entry.

Slice 1 scope
-------------
- Load registry from JSON file
- Validate registry-level structure (version, type)
- Parse each project entry through existing parse_project_config
- Validate each project entry through validate_project_config
- Construct and return ProjectRegistry
- Fail closed on any structural or duplicate-id error
- Raise ProjectNotFoundError on unknown project lookup
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from engineering.models import (
    DuplicateProjectId,
    ParseResult,
    ProjectConfig,
    ProjectRegistry,
    parse_project_config,
    validate_project_config,
)

# ---------------------------------------------------------------------------
# Registry-level constants
# ---------------------------------------------------------------------------

REGISTRY_FILENAME = "engineering-registry.json"
REGISTRY_VERSION = "1"

# Key names in the registry JSON
_REGISTRY_VERSION_KEY = "registry_version"
_SCHEMA_VERSION_KEY = "schema_version"
_PROJECTS_KEY = "projects"

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RegistryError(Exception):
    """Base class for registry loading errors."""


class MalformedRegistryError(RegistryError):
    """Raised when the registry JSON is unreadable or structurally invalid."""


class UnsupportedRegistryVersionError(RegistryError):
    """Raised when the registry_version is not supported by this loader."""


class ProjectNotFoundError(RegistryError):
    """Raised when a project_id lookup finds no matching entry."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"project not found: {project_id!r}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    """Load and parse a JSON registry file.

    Raises MalformedRegistryError if the file cannot be read or is not valid JSON.
    """
    if not path.exists():
        raise MalformedRegistryError(
            f"registry file not found: {path}. "
            "Create the registry file or run the bootstrap process."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MalformedRegistryError(f"cannot read registry file {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedRegistryError(f"registry file {path} is not valid JSON: {exc}") from exc


def _check_registry_structure(data: dict[str, Any]) -> None:
    """Validate registry-level structure and version.

    Raises UnsupportedRegistryVersionError if registry_version is present and
    unsupported.  Raises MalformedRegistryError if the top-level structure is
    not a dict with a projects list.
    """
    if not isinstance(data, dict):
        raise MalformedRegistryError(
            f"registry must be a JSON object, got {type(data).__name__}"
        )
    registry_version = data.get(_REGISTRY_VERSION_KEY, None)
    if registry_version is not None and registry_version != REGISTRY_VERSION:
        raise UnsupportedRegistryVersionError(
            f"unsupported registry_version {registry_version!r}; "
            f"only version {REGISTRY_VERSION!r} is supported"
        )
    projects = data.get(_PROJECTS_KEY, None)
    if projects is None:
        raise MalformedRegistryError(
            f"missing required top-level key {_PROJECTS_KEY!r}"
        )
    if not isinstance(projects, list):
        raise MalformedRegistryError(
            f"{_PROJECTS_KEY} must be a JSON array, got {type(projects).__name__}"
        )


def _check_no_duplicate_ids(projects: list[dict[str, Any]]) -> None:
    """Raise DuplicateProjectId if two projects share the same project_id."""
    seen: dict[str, int] = {}
    for i, entry in enumerate(projects):
        if not isinstance(entry, dict):
            continue  # parse will catch this
        pid = entry.get("project_id", None)
        if pid is not None and isinstance(pid, str):
            if pid in seen:
                raise DuplicateProjectId(pid)
            seen[pid] = i


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_registry(
    registry_path: Path | None = None,
    *,
    skip_workflow_files: bool = False,
) -> ProjectRegistry:
    """Load and validate the project registry.

    Parameters
    ----------
    registry_path:
        Path to the registry JSON file.  Defaults to
        ``~/.openclaw/engineering-registry.json``.
    skip_workflow_files:
        If True, skip semantic validation of workflow file paths and parent
        directory existence.  This is useful when the workflow files have not
        yet been created (e.g., before bootstrap).  Structural parsing of
        workflow_files is still performed.  Defaults to False.

    Returns
    -------
    ProjectRegistry
        Populated with all parsed and validated ProjectConfig entries.

    Raises
    ------
    MalformedRegistryError
        The registry file is missing, unreadable, not valid JSON, or missing
        the required top-level keys.
    UnsupportedRegistryVersionError
        The ``registry_version`` field is present but not supported.
    DuplicateProjectId
        Two project entries share the same ``project_id``.
    ProjectNotFoundError
        (Only from ``get_project`` — not from this function.)

    Notes
    -----
    Each project entry is parsed through ``parse_project_config`` (structural)
    and then through ``validate_project_config`` (semantic).  All structural
    and semantic errors are collected before the registry is constructed;
    if any entry has errors, the entire load fails with a
    ``MalformedRegistryError`` that lists every error found.

    The function makes no repository discovery calls, uses no ``Path.cwd()``
    fallback, and contains zero ``if project_id == ...`` branches.
    """
    if registry_path is None:
        registry_path = Path.home() / ".openclaw" / REGISTRY_FILENAME

    # Step 1: Load JSON
    data = _load_json(registry_path)

    # Step 2: Validate registry-level structure
    _check_registry_structure(data)

    projects: list[dict[str, Any]] = data[_PROJECTS_KEY]

    # Step 3: Check for duplicate IDs before parsing (faster failure)
    _check_no_duplicate_ids(projects)

    # Step 4: Parse and validate each project entry
    all_errors: dict[str, list[str]] = {}  # project_id -> errors
    configs: list[ProjectConfig] = []

    for entry in projects:
        if not isinstance(entry, dict):
            raise MalformedRegistryError(
                f"registry[_PROJECTS_KEY][{len(configs)}]: "
                f"expected JSON object, got {type(entry).__name__}"
            )

        # Structural parse
        parse_result: ParseResult = parse_project_config(entry)
        if parse_result.config is None:
            all_errors[entry.get("project_id", "<unknown>") or "<unknown>"] = (
                list(parse_result.errors)
            )
            continue

        config: ProjectConfig = parse_result.config

        # Semantic validation (with optional workflow-file skip)
        if skip_workflow_files:
            # Run everything except the workflow file parent-dir checks
            semantic_errors = _validate_without_workflow_dirs(config)
        else:
            semantic_errors = validate_project_config(config)

        if semantic_errors:
            all_errors[config.project_id] = semantic_errors
            continue

        configs.append(config)

    if all_errors:
        lines = ["registry contains projects with validation errors:"]
        for pid, errs in all_errors.items():
            for e in errs:
                lines.append(f"  [{pid}] {e}")
        raise MalformedRegistryError("\n".join(lines))

    # Step 5: Build and return ProjectRegistry
    return ProjectRegistry.from_projects(configs)


def get_project(
    registry: ProjectRegistry,
    project_id: str,
) -> ProjectConfig:
    """Look up a single project by project_id.

    Parameters
    ----------
    registry:
        A registry returned by ``load_registry``.
    project_id:
        The unique project identifier to look up.

    Returns
    -------
    ProjectConfig

    Raises
    ------
    ProjectNotFoundError
        No project with the given project_id exists in the registry.

    Notes
    -----
    This function contains zero ``if project_id == ...`` branches.
    """
    projects = registry.projects
    if project_id not in projects:
        raise ProjectNotFoundError(project_id)
    return projects[project_id]


# ---------------------------------------------------------------------------
# Internal validation helpers (for skip_workflow_files path)
# ---------------------------------------------------------------------------

def _validate_without_workflow_dirs(config: ProjectConfig) -> list[str]:
    """Run validate_project_config but skip workflow-file parent-directory checks.

    This is used when the workflow persistence files have not yet been created
    (e.g., before bootstrap has been run for a new project).  Governance file
    existence is still required since those files must exist for any project.
    """
    errors: list[str] = []

    # Replicate the critical non-workflow checks from validate_project_config
    if not config.project_id or not config.project_id.strip():
        errors.append("project_id cannot be empty")
    if not config.display_name or not config.display_name.strip():
        errors.append("display_name cannot be empty")
    if not config.repository_root.is_absolute():
        errors.append(f"repository_root must be absolute; got: {config.repository_root}")
    if not config.repository_root.exists():
        errors.append(f"repository_root does not exist: {config.repository_root}")

    # Governance file path and existence checks
    def _check_gov_path(path: Path, label: str) -> None:
        if not path.is_absolute():
            errors.append(f"{label}: must be absolute; got {path}")
            return
        try:
            resolved = path.resolve()
            repo_resolved = config.repository_root.resolve()
            resolved.relative_to(repo_resolved)
        except ValueError:
            errors.append(f"{label}: path escapes repository_root")
        except OSError:
            errors.append(f"{label}: could not resolve path: {path}")

    gf = config.governance_files
    _check_gov_path(gf.backlog_path, "governance_files.backlog_path")
    _check_gov_path(gf.operating_plan_path, "governance_files.operating_plan_path")
    _check_gov_path(gf.owners_path, "governance_files.owners_path")
    _check_gov_path(gf.handoff_path, "governance_files.handoff_path")

    # Governance files must exist
    for label, path in [
        ("governance_files.backlog_path", gf.backlog_path),
        ("governance_files.operating_plan_path", gf.operating_plan_path),
        ("governance_files.owners_path", gf.owners_path),
        ("governance_files.handoff_path", gf.handoff_path),
    ]:
        if not path.exists():
            errors.append(f"{label}: required governance file does not exist: {path}")

    # Workflow file structural checks (no existence required)
    def _check_wf_path(path: Path, label: str) -> None:
        if not path.is_absolute():
            errors.append(f"{label}: must be absolute; got {path}")
            return
        try:
            resolved = path.resolve()
            repo_resolved = config.repository_root.resolve()
            resolved.relative_to(repo_resolved)
        except ValueError:
            errors.append(f"{label}: path escapes repository_root")
        except OSError:
            errors.append(f"{label}: could not resolve path: {path}")

    wf = config.workflow_files
    _check_wf_path(wf.workflow_store_path, "workflow_files.workflow_store_path")
    _check_wf_path(wf.event_store_path, "workflow_files.event_store_path")
    _check_wf_path(wf.report_dir, "workflow_files.report_dir")

    # QA / policy checks
    if not config.qa_commands:
        errors.append("qa_commands cannot be empty")
    if config.qa_timeout_seconds <= 0:
        errors.append(
            f"qa_timeout_seconds must be positive; got {config.qa_timeout_seconds}"
        )
    if not config.owner_ids:
        errors.append("owner_ids cannot be empty; at least one human owner required")
    elif len(set(config.owner_ids)) != len(config.owner_ids):
        errors.append("owner_ids contains duplicate entries")
    if not config.agent_owners:
        errors.append("agent_owners cannot be empty")
    elif len(set(config.agent_owners)) != len(config.agent_owners):
        errors.append("agent_owners contains duplicate entries")

    # agents_may_merge must be False
    if config.agents_may_merge:
        errors.append(
            "agents_may_merge=True: project does not have an approval policy; "
            "set to False or define a full approval policy in governance"
        )

    return errors
