"""ENGPLAT-003A — Project Bootstrap Planning and Filesystem Creation.

This module provides the deterministic bootstrap API for the engineering
platform. It creates a new managed project from generic templates without
requiring the destination to already have governance files.

Library-only API. No CLI is authorized in this slice.

Public API
----------
plan_bootstrap(input: BootstrapInput) -> BootstrapPlan
    Deterministic dry-run planning. Zero persistent writes.

apply_bootstrap(input: BootstrapInput) -> BootstrapResult
    Safe filesystem creation. Returns generated ProjectConfig.

Transaction model: PRE-FLIGHT + FAIL-FAST, PARTIAL STATE POSSIBLE.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, TextIO

from engineering.models import (
    GovernanceFiles,
    ProjectConfig,
    WorkflowFiles,
    _check_qa_command_safety,
    parse_project_config,
)

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template identifiers and relative destination paths
# ---------------------------------------------------------------------------

# Ordered list of artifact specs: (template_name, relative_destination)
_ARTIFACT_SPECS = (
    ("AGENTS.md.template", "AGENTS.md"),
    ("AGENT_BACKLOG.md.template", "AGENT_BACKLOG.md"),
    ("AGENT_OPERATING_PLAN.md.template", "AGENT_OPERATING_PLAN.md"),
    ("OWNERS.md.template", "OWNERS.md"),
    ("AUTONOMOUS_ENGINEERING_HANDOFF.md.template", "AUTONOMOUS_ENGINEERING_HANDOFF.md"),
)

# Schema version matching the current ProjectConfig contract
_CURRENT_SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Frozen input type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BootstrapInput:
    """Explicit input for bootstrap planning and apply.

    Attributes
    ----------
    project_id : str
        Unique project identifier (slug format).
    display_name : str
        Human-readable project name.
    destination : Path
        Absolute path to the managed repository root. Required; no cwd fallback.
    authoritative_base_branch : str
        Base branch for all feature work. Defaults to "main".
    manager_role : str
        Name of the manager agent role. Defaults to "manager".
    exec_role : str
        Name of the executor agent role. Defaults to "exec".
    dashboard_role : str
        Name of the dashboard agent role. Defaults to "dashboard".
    qa_commands : tuple[str, ...]
        Pre-configured pytest commands. Defaults to safe minimal commands.
    qa_timeout_seconds : int
        Maximum QA runtime in seconds. Must be positive.
    owner_ids : tuple[str, ...]
        Human owner identifiers. At least one required.
    agent_owners : tuple[str, ...]
        Authorized agent identities.
    prohibited_operations : tuple[str, ...]
        Operations banned for this project.
    agents_may_merge : bool
        Whether agents may merge. Always False initially.
    """

    project_id: str
    display_name: str
    destination: Path
    authoritative_base_branch: str = "main"
    manager_role: str = "manager"
    exec_role: str = "exec"
    dashboard_role: str = "dashboard"
    qa_commands: tuple[str, ...] = ("python -m pytest tests/ -q",)
    qa_timeout_seconds: int = 300
    owner_ids: tuple[str, ...] = ("owner",)
    agent_owners: tuple[str, ...] = ("manager",)
    prohibited_operations: tuple[str, ...] = ()
    agents_may_merge: bool = False


# ---------------------------------------------------------------------------
# Artifact plan types (local to this module)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactPlan:
    """Plan for one planned artifact.

    Attributes
    ----------
    relative_path : Path
        Artifact path relative to the destination root.
    action : str
        "CREATE" or "CONFLICT".
    template_name : str
        Source template identifier.
    byte_count : int
        Generated file byte count.
    line_count : int
        Generated file line count.
    sha256_digest : str
        Lowercase hex SHA-256 digest of generated content.
    summary : str
        Bounded one-line summary of the generated content.
    """

    relative_path: Path
    action: str
    template_name: str
    byte_count: int
    line_count: int
    sha256_digest: str
    summary: str


@dataclass(frozen=True)
class BootstrapPlan:
    """Result of plan_bootstrap(). Deterministic dry-run plan.

    Attributes
    ----------
    input : BootstrapInput
        The validated input.
    artifacts : tuple[ArtifactPlan, ...]
        Ordered artifact plans. Length is always 5.
    warnings : tuple[str, ...]
        Bounded warnings (e.g. destination not empty).
    validation_errors : tuple[str, ...]
        Validation errors collected during pre-flight.
        Non-empty means plan is invalid and apply must not run.
    project_config : ProjectConfig
        The generated ProjectConfig (passes parse + validate).
    """

    input: BootstrapInput
    artifacts: tuple[ArtifactPlan, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    validation_errors: tuple[str, ...] = field(default_factory=tuple)
    project_config: ProjectConfig | None = None


@dataclass(frozen=True)
class BootstrapResult:
    """Result of apply_bootstrap().

    Attributes
    ----------
    input : BootstrapInput
        The input that was applied.
    success : bool
        True only when all five files were written without error.
    written_paths : tuple[Path, ...]
        Paths successfully written before any error; empty if apply failed.
    failed_target : Path | None
        The path that failed to write; None if no failure occurred.
    partial_state : bool
        True when success is False and written_paths is non-empty.
    project_config : ProjectConfig
        The generated ProjectConfig (always present, even on partial apply).
    plan : BootstrapPlan
        The pre-flight plan that was used.
    error_message : str | None
        Error message from the write failure; None on success.
    """

    input: BootstrapInput
    success: bool
    written_paths: tuple[Path, ...] = field(default_factory=tuple)
    failed_target: Path | None = None
    partial_state: bool = False
    project_config: ProjectConfig | None = None
    plan: BootstrapPlan | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Template resolution
# ---------------------------------------------------------------------------

# The templates directory is resolved relative to this module.
_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_template(template_name: str) -> str:
    """Load a template file by name.

    Raises FileNotFoundError if the template does not exist.
    """
    path = _TEMPLATES_DIR / template_name
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _render_template(template_name: str, input: BootstrapInput) -> str:
    """Render a template with project-specific substitutions."""
    raw = _load_template(template_name)
    repo_root = str(input.destination)

    # Build substitution dict from BootstrapInput fields
    subs = {
        "project_id": input.project_id,
        "display_name": input.display_name,
        "repository_root": repo_root,
        "manager_role": input.manager_role,
        "exec_role": input.exec_role,
        "dashboard_role": input.dashboard_role,
    }

    try:
        rendered = raw.format(**subs)
    except KeyError as exc:
        # Template had an unsubstituted placeholder — substitute the key name
        # to avoid leaking template-internal structure into error messages
        rendered = raw.replace(f"{{{exc.args[0]}}}", f"<{exc.args[0]}>")

    return rendered


# ---------------------------------------------------------------------------
# Artifact generation helpers
# ---------------------------------------------------------------------------


def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _summarize(content: str, max_len: int = 120) -> str:
    """Return a bounded one-line summary of the content.

    Uses the first non-empty line, stripped, truncated to max_len.
    """
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            if len(stripped) > max_len:
                return stripped[:max_len] + "…"
            return stripped
    return "(empty)"


def _generate_artifact(
    template_name: str,
    relative_path: Path,
    input: BootstrapInput,
) -> tuple[str, ArtifactPlan]:
    """Generate content and ArtifactPlan for one artifact.

    Returns (rendered_content, ArtifactPlan).

    Raises
    ------
    FileNotFoundError
        If the template does not exist.
    """
    content = _render_template(template_name, input)
    byte_count = len(content.encode("utf-8"))
    line_count = len(content.splitlines())
    digest = _sha256_hex(content)
    summary = _summarize(content)

    plan = ArtifactPlan(
        relative_path=relative_path,
        action="CREATE",
        template_name=template_name,
        byte_count=byte_count,
        line_count=line_count,
        sha256_digest=digest,
        summary=summary,
    )
    return content, plan


# ---------------------------------------------------------------------------
# Destination validation helpers
# ---------------------------------------------------------------------------


def _validate_destination(dest: Path) -> list[str]:
    """Validate destination root and return list of validation error strings.

    Checks:
    - dest is absolute
    - dest is not a symlink
    - dest does not escape after resolution
    """
    errors: list[str] = []

    if not dest.is_absolute():
        errors.append(f"destination must be absolute; got: {dest}")
        return errors

    # Check symlink
    try:
        if dest.is_symlink():
            errors.append(f"destination root is a symlink; reject: {dest}")
            return errors
    except OSError as exc:
        errors.append(f"destination root is not accessible; {exc}")
        return errors

    # Resolve and check for traversal escape
    try:
        resolved = dest.resolve()
    except OSError as exc:
        errors.append(f"destination root could not be resolved; {exc}")
        return errors

    # Ensure resolved path is the destination itself (no traversal)
    # We check by comparing resolved to dest after normpath
    try:
        resolved.relative_to(dest)
    except ValueError:
        # This should not happen for absolute dest with resolve()
        errors.append(f"destination resolved path escapes; resolved={resolved} dest={dest}")

    return errors


def _mark_artifact_conflicts(
    artifacts: tuple[ArtifactPlan, ...],
    dest: Path,
) -> tuple[tuple[ArtifactPlan, ...], list[tuple[Path, Path]]]:
    """Mark existing planned artifacts as CONFLICT without writing.

    Returns the updated artifact plans and a list of (relative_path, absolute_path)
    conflicts. Any conflict makes apply fail before writing any artifact.
    """
    conflicts: list[tuple[Path, Path]] = []
    updated: list[ArtifactPlan] = []
    for artifact in artifacts:
        abs_path = dest / artifact.relative_path
        if artifact.action == "CREATE" and abs_path.exists():
            conflicts.append((artifact.relative_path, abs_path))
            updated.append(
                ArtifactPlan(
                    relative_path=artifact.relative_path,
                    action="CONFLICT",
                    template_name=artifact.template_name,
                    byte_count=artifact.byte_count,
                    line_count=artifact.line_count,
                    sha256_digest=artifact.sha256_digest,
                    summary=artifact.summary,
                )
            )
        else:
            updated.append(artifact)
    return tuple(updated), conflicts


def _check_traversal(relative_path: Path, dest: Path) -> bool:
    """Return True if the relative_path would escape dest after normalization."""
    try:
        (dest / relative_path).resolve().relative_to(dest.resolve())
        return False
    except ValueError:
        return True


# ---------------------------------------------------------------------------
# ProjectConfig construction
# ---------------------------------------------------------------------------


def _build_project_config(input: BootstrapInput) -> ProjectConfig:
    """Construct a ProjectConfig from BootstrapInput.

    Does not validate or persist. Caller must validate before use.
    """
    repo_root = input.destination.resolve()

    governance_files = GovernanceFiles(
        backlog_path=repo_root / "AGENT_BACKLOG.md",
        operating_plan_path=repo_root / "AGENT_OPERATING_PLAN.md",
        owners_path=repo_root / "OWNERS.md",
        handoff_path=repo_root / "AUTONOMOUS_ENGINEERING_HANDOFF.md",
    )

    workflow_files = WorkflowFiles(
        workflow_store_path=repo_root / "engineering" / "workflow_store.json",
        event_store_path=repo_root / "engineering" / "event_store.db",
        report_dir=repo_root / "reports",
    )

    return ProjectConfig(
        schema_version=_CURRENT_SCHEMA_VERSION,
        project_id=input.project_id,
        display_name=input.display_name,
        repository_root=repo_root,
        authoritative_base_branch=input.authoritative_base_branch,
        governance_files=governance_files,
        workflow_files=workflow_files,
        qa_commands=input.qa_commands,
        qa_timeout_seconds=input.qa_timeout_seconds,
        prohibited_operations=input.prohibited_operations,
        agents_may_merge=input.agents_may_merge,
        owner_ids=input.owner_ids,
        agent_owners=input.agent_owners,
    )


def _project_config_mapping(config: ProjectConfig) -> dict[str, object]:
    """Return a structural ProjectConfig mapping without persisting it."""
    return {
        "schema_version": config.schema_version,
        "project_id": config.project_id,
        "display_name": config.display_name,
        "repository_root": str(config.repository_root),
        "authoritative_base_branch": config.authoritative_base_branch,
        "governance_files": {
            "backlog_path": str(config.governance_files.backlog_path),
            "operating_plan_path": str(config.governance_files.operating_plan_path),
            "owners_path": str(config.governance_files.owners_path),
            "handoff_path": str(config.governance_files.handoff_path),
        },
        "workflow_files": {
            "workflow_store_path": str(config.workflow_files.workflow_store_path),
            "event_store_path": str(config.workflow_files.event_store_path),
            "report_dir": str(config.workflow_files.report_dir),
        },
        "qa_commands": config.qa_commands,
        "qa_timeout_seconds": config.qa_timeout_seconds,
        "prohibited_operations": config.prohibited_operations,
        "agents_may_merge": config.agents_may_merge,
        "owner_ids": config.owner_ids,
        "agent_owners": config.agent_owners,
    }


def _validate_bootstrap_project_config_intrinsic(config: ProjectConfig) -> list[str]:
    """Validate ProjectConfig semantics that are knowable before 003A writes.

    ``validate_project_config()`` also checks runtime filesystem readiness
    (governance file existence, workflow parent directories, report parent
    directories). ENGPLAT-003A must not create those runtime directories merely
    to satisfy readiness checks. This helper validates intrinsic configuration
    semantics before the first artifact write while leaving runtime readiness to
    later runtime components. It does not filter or weaken
    ``validate_project_config()`` output; it avoids calling the runtime-readiness
    validator in this bootstrap slice.
    """
    errors: list[str] = []

    parse_result = parse_project_config(_project_config_mapping(config))
    if parse_result.errors:
        errors.extend(f"ProjectConfig structural error: {err}" for err in parse_result.errors)
    if parse_result.config is None:
        return errors

    cfg = parse_result.config
    if not cfg.project_id or not cfg.project_id.strip():
        errors.append("project_id cannot be empty")
    if not cfg.display_name or not cfg.display_name.strip():
        errors.append("display_name cannot be empty")
    if not cfg.repository_root.is_absolute():
        errors.append(f"repository_root must be absolute; got: {cfg.repository_root}")

    try:
        repo_resolved = cfg.repository_root.resolve()
    except OSError as exc:
        errors.append(f"repository_root could not be resolved: {exc}")
        return errors

    def _check_path(path: Path, label: str) -> None:
        if not path.is_absolute():
            errors.append(f"{label}: must be absolute; got {path}")
            return
        try:
            path.resolve().relative_to(repo_resolved)
        except ValueError:
            errors.append(
                f"{label}: path escapes repository_root; "
                f"path={path} root={cfg.repository_root}"
            )
        except OSError:
            errors.append(f"{label}: could not resolve path: {path}")

    gf = cfg.governance_files
    _check_path(gf.backlog_path, "governance_files.backlog_path")
    _check_path(gf.operating_plan_path, "governance_files.operating_plan_path")
    _check_path(gf.owners_path, "governance_files.owners_path")
    _check_path(gf.handoff_path, "governance_files.handoff_path")

    wf = cfg.workflow_files
    _check_path(wf.workflow_store_path, "workflow_files.workflow_store_path")
    _check_path(wf.event_store_path, "workflow_files.event_store_path")
    _check_path(wf.report_dir, "workflow_files.report_dir")

    if not cfg.qa_commands:
        errors.append("qa_commands cannot be empty")
    else:
        errors.extend(_check_qa_command_safety(cfg.qa_commands))
    if cfg.qa_timeout_seconds <= 0:
        errors.append(f"qa_timeout_seconds must be positive; got {cfg.qa_timeout_seconds}")
    if not cfg.owner_ids:
        errors.append("owner_ids cannot be empty; at least one human owner required")
    elif len(set(cfg.owner_ids)) != len(cfg.owner_ids):
        errors.append("owner_ids contains duplicate entries")
    if not cfg.agent_owners:
        errors.append("agent_owners cannot be empty")
    elif len(set(cfg.agent_owners)) != len(cfg.agent_owners):
        errors.append("agent_owners contains duplicate entries")
    if cfg.agents_may_merge:
        errors.append("agents_may_merge=True: bootstrap projects require approval-gated merging")

    return sorted(errors)


# ---------------------------------------------------------------------------
# Pre-flight validation
# ---------------------------------------------------------------------------


def _preflight(
    input: BootstrapInput,
    *,
    create_destination: bool = False,
    skip_existence_checks: bool = False,
) -> tuple[list[str], list[str], tuple[ArtifactPlan, ...], ProjectConfig]:
    """Run full pre-flight validation.

    Parameters
    ----------
    input : BootstrapInput
        The bootstrap input to validate.
    create_destination : bool
        If True, create the destination directory if it does not exist.
        Used by apply_bootstrap; plan_bootstrap sets this to False.
    skip_existence_checks : bool
        If True, skip file-existence and parent-directory semantic checks
        in validate_project_config(). Used by plan_bootstrap since files
        do not exist yet (bootstrap creates them).

    Returns
    -------
    (errors, warnings, artifacts, project_config)

    Errors: validation failures that prevent apply
    Warnings: non-fatal conditions to report
    artifacts: planned artifact set
    project_config: constructed config (passes parse but may fail semantic validation
        if skip_existence_checks=True; full validation done after apply)

    If errors is non-empty, apply must not run.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Basic input validation
    if not input.project_id or not input.project_id.strip():
        errors.append("project_id cannot be empty")
    if not input.display_name or not input.display_name.strip():
        errors.append("display_name cannot be empty")
    if input.qa_timeout_seconds <= 0:
        errors.append(f"qa_timeout_seconds must be positive; got {input.qa_timeout_seconds}")
    if not input.owner_ids:
        errors.append("owner_ids cannot be empty")
    if not input.agent_owners:
        errors.append("agent_owners cannot be empty")
    if not input.qa_commands:
        errors.append("qa_commands cannot be empty")

    # 2. Destination validation
    dest_errors = _validate_destination(input.destination)
    errors.extend(dest_errors)
    if dest_errors:
        # Cannot proceed with invalid destination
        return errors, warnings, (), ProjectConfig()

    # 3. Destination existence
    if not input.destination.exists():
        if create_destination:
            try:
                input.destination.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                errors.append(f"destination could not be created; {exc}")
                return errors, warnings, (), ProjectConfig()
        else:
            # plan_bootstrap: destination doesn't need to exist for pre-flight checks
            # (apply will create it when actually writing)
            pass

    # 4. Check destination is a directory (if it exists)
    if input.destination.exists() and not input.destination.is_dir():
        errors.append(f"destination exists but is not a directory; {input.destination}")

    # 5. Check for non-empty destination (warning only)
    if input.destination.exists():
        try:
            entries = list(input.destination.iterdir())
            if entries:
                warnings.append(
                    f"destination is non-empty; {len(entries)} existing entries; "
                    "unrelated files will not be removed"
                )
        except OSError:
            pass  # Non-fatal; permission or I/O issue during warning check

    # 6. Generate artifact plans
    artifacts: list[ArtifactPlan] = []
    for template_name, rel_path in _ARTIFACT_SPECS:
        # Check traversal escape
        if _check_traversal(Path(rel_path), input.destination):
            errors.append(f"artifact path escapes destination; {rel_path}")
            continue

        try:
            _, plan = _generate_artifact(template_name, Path(rel_path), input)
            artifacts.append(plan)
        except FileNotFoundError:
            errors.append(f"template not found: {template_name}")
        except Exception as exc:
            errors.append(f"template render failed for {template_name}; {exc}")

    if len(artifacts) != 5:
        errors.append(f"expected 5 artifacts, got {len(artifacts)}")

    # 7. Check for existing conflicts (only if destination exists)
    artifact_tuple = tuple(artifacts)
    if input.destination.exists():
        artifact_tuple, conflicts = _mark_artifact_conflicts(artifact_tuple, input.destination)
        if conflicts:
            for rel_path, _ in conflicts:
                errors.append(f"planned artifact already exists (CONFLICT); {rel_path}")

    # 8. Build and validate ProjectConfig intrinsic semantics before writes.
    config = _build_project_config(input)
    errors.extend(_validate_bootstrap_project_config_intrinsic(config))

    return errors, warnings, artifact_tuple, config


# ---------------------------------------------------------------------------
# plan_bootstrap — dry-run API
# ---------------------------------------------------------------------------


def plan_bootstrap(input: BootstrapInput) -> BootstrapPlan:
    """Produce a deterministic dry-run bootstrap plan.

    Performs full pre-flight validation without writing any files and without
    creating the destination directory.

    Parameters
    ----------
    input : BootstrapInput
        Explicit bootstrap input. destination must be absolute.

    Returns
    -------
    BootstrapPlan
        Bounded plan containing artifact plans, warnings, validation errors,
        and the generated ProjectConfig.

    Raises
    ------
    TypeError
        If input is not a BootstrapInput instance.
    """
    if not isinstance(input, BootstrapInput):
        raise TypeError(f"input must be BootstrapInput, got {type(input).__name__}")

    # plan_bootstrap: do NOT create destination, skip file-existence checks
    # since bootstrap creates those files
    errors, warnings, artifacts, config = _preflight(
        input,
        create_destination=False,
    )

    return BootstrapPlan(
        input=input,
        artifacts=artifacts,
        warnings=tuple(warnings),
        validation_errors=tuple(errors),
        project_config=config if config.schema_version else None,
    )


# ---------------------------------------------------------------------------
# apply_bootstrap — apply API
# ---------------------------------------------------------------------------


def apply_bootstrap(input: BootstrapInput) -> BootstrapResult:
    """Apply bootstrap to the destination, creating exactly five managed files.

    Transaction model: PRE-FLIGHT + FAIL-FAST, PARTIAL STATE POSSIBLE.

    Before writing, performs full pre-flight validation. If any validation error
    or conflict is found, performs zero writes and returns a result with
    success=False and an empty written_paths.

    If an unexpected write failure occurs, stops immediately and returns a
    result with partial_state=True, the paths already written, and the failed
    target.

    After all writes succeed, performs semantic validation of the generated
    ProjectConfig. If semantic validation fails, returns success=False with
    partial_state=True (files were written but config is invalid).

    Parameters
    ----------
    input : BootstrapInput
        Explicit bootstrap input. destination must be absolute.

    Returns
    -------
    BootstrapResult
        Contains success flag, written_paths, failed_target, partial_state flag,
        generated ProjectConfig, the pre-flight plan, and error_message if any.

    Raises
    ------
    TypeError
        If input is not a BootstrapInput instance.
    """
    if not isinstance(input, BootstrapInput):
        raise TypeError(f"input must be BootstrapInput, got {type(input).__name__}")

    # Full pre-flight validation (creates destination directory)
    errors, warnings, artifacts, config = _preflight(
        input,
        create_destination=True,
    )

    # Build pre-flight plan for result
    preflight_plan = BootstrapPlan(
        input=input,
        artifacts=artifacts,
        warnings=tuple(warnings),
        validation_errors=tuple(errors),
        project_config=config if config.schema_version else None,
    )

    # If any pre-flight errors, zero writes
    if errors:
        return BootstrapResult(
            input=input,
            success=False,
            written_paths=(),
            failed_target=None,
            partial_state=False,
            project_config=config if config.schema_version else None,
            plan=preflight_plan,
            error_message="pre-flight validation failed; zero writes performed",
        )

    # Write artifacts
    written: list[Path] = []
    failed_target: Path | None = None
    error_message: str | None = None

    for template_name, rel_path in _ARTIFACT_SPECS:
        abs_path = input.destination / rel_path
        try:
            content = _render_template(template_name, input)
            # Fail if destination already exists (should be impossible since
            # we checked conflicts in pre-flight, but be safe)
            if abs_path.exists():
                failed_target = abs_path
                error_message = f"unexpected existing file; {rel_path}"
                break
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            written.append(abs_path)
        except OSError as exc:
            failed_target = abs_path
            error_message = f"write failed for {rel_path}; {exc}"
            break
        except Exception as exc:
            failed_target = abs_path
            error_message = f"unexpected error writing {rel_path}; {exc}"
            break

    # Determine final state
    all_written = len(written) == 5
    write_failure = failed_target is not None
    write_success = all_written and not write_failure

    success = write_success
    partial_state = (len(written) > 0) and not success

    return BootstrapResult(
        input=input,
        success=success,
        written_paths=tuple(written),
        failed_target=failed_target,
        partial_state=partial_state,
        project_config=config if config.schema_version else None,
        plan=preflight_plan,
        error_message=error_message,
    )
