"""Runtime-state initialization for registered engineering projects.

Provides a single entry point for ensuring a project's runtime directories exist
before the engineering workflow engine attempts to use them.

This module is intentionally decoupled from manager.py and workflow handlers so
that runtime initialization can be tested independently and used by any caller
that needs to activate a project.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engineering.models import ProjectConfig


@dataclass(frozen=True)
class RuntimeInitializationResult:
    """Result of ensure_project_runtime_dirs().

    Attributes
    ----------
    created_paths : tuple[Path, ...]
        Directories that were newly created by this call.
        Empty tuple means all directories already existed.
    warnings : tuple[str, ...]
        Non-fatal conditions encountered during initialization.
    """

    created_paths: tuple[Path, ...]
    warnings: tuple[str, ...]


class RuntimeInitError(Exception):
    """Raised when runtime directory initialization fails.

    Failures are deterministic and contain no secrets, raw paths (beyond the
    field name), or command output.
    """

    pass


def ensure_project_runtime_dirs(
    config: ProjectConfig,
) -> RuntimeInitializationResult:
    """Ensure minimum runtime state directories exist under repository_root.

    Creates the minimum set of directories required before a project can operate,
    derived from the ProjectConfig's workflow path fields. Does NOT create
    workflow_store.json, event_store.db, or any governance file — those are
    created lazily by their respective stores.

    Parameters
    ----------
    config : ProjectConfig
        A validated ProjectConfig for the project to initialize.
        Must have passed structural parsing (``parse_project_config``) before
        being passed here.

    Returns
    -------
    RuntimeInitializationResult
        Describes which directories were newly created and any warnings.

    Raises
    ------
    RuntimeInitError
        When a target path escapes repository_root (via traversal or symlink),
        or when a target path exists as a non-directory (blocking directory creation).

    Idempotency
    -----------
    Safe to call multiple times. If a directory already exists, it is a no-op.
    If the function raises, zero directories are created.
    """
    repo_root = config.repository_root
    wf = config.workflow_files

    # Collect unique directories that must exist.
    # report_dir is included as a directory (not just its parent) so that
    # mkdir creates it directly when needed.
    required_dirs = {wf.workflow_store_path.parent, wf.event_store_path.parent, wf.report_dir}

    # Preflight: validate every target before creating any directory.
    # This ensures zero partial state on failure.
    for target_dir in required_dirs:
        _validate_target(target_dir, repo_root)

    # All targets preflighted successfully — create directories.
    created: list[Path] = []
    warnings: list[str] = []

    for target_dir in sorted(required_dirs, key=str):
        created_dir, warning = _ensure_directory(target_dir)
        if created_dir is not None:
            created.append(created_dir)
        if warning:
            warnings.append(warning)

    return RuntimeInitializationResult(
        created_paths=tuple(created),
        warnings=tuple(warnings),
    )


def _validate_target(target_dir: Path, repo_root: Path) -> None:
    """Validate a target directory is contained within repo_root.

    Raises RuntimeInitError if the path escapes, cannot be resolved,
    or exists as a non-directory (blocking creation).
    """
    if not target_dir.is_absolute():
        raise RuntimeInitError(
            f"Target directory must be absolute: {target_dir}"
        )

    try:
        resolved = target_dir.resolve()
        repo_resolved = repo_root.resolve()
        resolved.relative_to(repo_resolved)
    except ValueError:
        raise RuntimeInitError(
            f"Target directory escapes repository_root: {target_dir}"
        )
    except OSError as exc:
        raise RuntimeInitError(
            f"Target directory could not be resolved: {target_dir}: {exc}"
        )

    # Reject existing non-directories that would block mkdir.
    if target_dir.exists() and not target_dir.is_dir():
        raise RuntimeInitError(
            f"Target path exists as a non-directory (not a directory): {target_dir}"
        )


def _ensure_directory(target_dir: Path) -> tuple[Path | None, str | None]:
    """Create target_dir if it does not exist as a directory.

    Returns (created_path, warning) where created_path is the directory
    that was newly created (or None if it already existed) and warning
    is a non-fatal message (or None).
    """
    if target_dir.is_dir():
        return None, None

    if target_dir.exists():
        # Exists but is not a directory — mkdir would fail; should have
        # been caught by preflight but handle defensively.
        return None, f"Skipped non-directory path: {target_dir}"

    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir, None
