"""ProjectContext factory — ENGPLAT-002A Option B contract.

Factory contract for building a ProjectContext from a ProjectConfig.

ENGPLAT-002A Option B: concrete adapter construction is deferred to 002B.
This module provides the factory signature, internal validation, and the
NotImplementedError signal that 002B replaces with a concrete implementation.

No side effects: no file creation, network access, Git mutation, QA execution,
or workflow state changes. All errors are deterministic and sanitized.
"""

from __future__ import annotations

from pathlib import Path

from engineering.adapters import (
    EventAdapter,
    FileReadAdapter,
    GitReadAdapter,
    GovernanceAdapter,
    ProjectContext,
    ProjectMetadata,
    QAAdapter,
    WorkflowAdapter,
)
from engineering.models import (
    ProjectConfig,
    validate_project_config,
)


# Deferred adapter NotImplementedError message — a pure signal, never a diagnostic.
# Intentionally contains no config values, paths, or runtime state.
_DEFERRED_MSG = "concrete adapter not yet implemented (ENGPLAT-002A Option B; see 002B)"


# ---------------------------------------------------------------------------
# Deferred adapter stubs (private; publicly raised via factory)
# ---------------------------------------------------------------------------
# These are defined at module level so they are importable by tests.
# They exist ONLY to satisfy structural Protocol checks; all methods raise
# NotImplementedError. 002B replaces them with concrete implementations.


class _DeferredGitAdapter(GitReadAdapter):
    """Deferred GitReadAdapter — 002B provides concrete implementation."""

    def current_branch(self) -> str:
        raise NotImplementedError(_DEFERRED_MSG)

    def is_clean(self) -> bool:
        raise NotImplementedError(_DEFERRED_MSG)

    def repository_state(self):
        raise NotImplementedError(_DEFERRED_MSG)

    def branch_exists(self, branch: str) -> bool:
        raise NotImplementedError(_DEFERRED_MSG)

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        raise NotImplementedError(_DEFERRED_MSG)


class _DeferredGovernanceAdapter(GovernanceAdapter):
    """Deferred GovernanceAdapter — 002B provides concrete implementation."""

    def load_backlog(self):
        raise NotImplementedError(_DEFERRED_MSG)

    def load_owners(self) -> str:
        raise NotImplementedError(_DEFERRED_MSG)

    def load_operating_plan(self) -> str:
        raise NotImplementedError(_DEFERRED_MSG)

    def load_handoff(self) -> str:
        raise NotImplementedError(_DEFERRED_MSG)


class _DeferredWorkflowAdapter(WorkflowAdapter):
    """Deferred WorkflowAdapter — 002B provides concrete implementation."""

    def workflow_store(self):
        raise NotImplementedError(_DEFERRED_MSG)

    def event_store(self):
        raise NotImplementedError(_DEFERRED_MSG)

    def archive_completed(self, workflow):
        raise NotImplementedError(_DEFERRED_MSG)


class _DeferredQAAdapter(QAAdapter):
    """Deferred QAAdapter — 002B provides concrete implementation."""

    def configured_command(self) -> tuple[str, ...]:
        raise NotImplementedError(_DEFERRED_MSG)

    def timeout_seconds(self) -> int:
        raise NotImplementedError(_DEFERRED_MSG)


class _DeferredFileReadAdapter(FileReadAdapter):
    """Deferred FileReadAdapter — 002B provides concrete implementation."""

    def resolve(self, relative_path: Path) -> Path:
        raise NotImplementedError(_DEFERRED_MSG)

    def exists(self, relative_path: Path) -> bool:
        raise NotImplementedError(_DEFERRED_MSG)

    def read_text(self, relative_path: Path) -> str:
        raise NotImplementedError(_DEFERRED_MSG)


class _DeferredEventAdapter(EventAdapter):
    """Deferred EventAdapter — 002B provides concrete implementation."""

    def append(self, event) -> bool:
        raise NotImplementedError(_DEFERRED_MSG)

    def list_events(self, limit: int = 100):
        raise NotImplementedError(_DEFERRED_MSG)

    def pause_state(self) -> dict[str, object]:
        raise NotImplementedError(_DEFERRED_MSG)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_project_context(config: ProjectConfig) -> ProjectContext:
    """Build a ProjectContext from a validated ProjectConfig.

    Contract (ENGPLAT-002A Option B)
    ---------------------------------
    1. Validates ``config`` internally via ``validate_project_config()``.
    2. Raises ``ValueError`` (deterministic, sanitized) on semantic failure.
       Error messages contain no secrets, raw paths, or command output.
    3. Constructs ``ProjectMetadata`` from the config.
    4. Returns a ``ProjectContext`` whose adapter fields are deferred stubs
       raising ``NotImplementedError`` on use (002A only).
       002B replaces these with concrete adapter implementations.
    5. No side effects: no file creation, network, Git mutation, QA, or
       workflow state changes.

    Parameters
    ----------
    config:
        A fully-constructed ``ProjectConfig`` instance. Must have passed
        structural parsing (``parse_project_config``) before being passed here.

    Returns
    -------
    ProjectContext
        A fully populated context. Adapter methods raise ``NotImplementedError``
        until 002B provides concrete implementations.

    Raises
    ------
    ValueError
        When semantic validation of ``config`` fails. The error message is
        deterministic and contains no secrets, raw paths, or command output.
    """
    # Step 1: semantic validation — fail closed on any semantic error
    validation_errors = validate_project_config(config)
    if validation_errors:
        # Sanitize: join errors but do not include raw config values.
        # Validation errors are already bounded strings from validate_project_config.
        raise ValueError(
            f"ProjectConfig validation failed: {'; '.join(validation_errors)}"
        )

    # Step 2: build ProjectMetadata from config (pure derivation, no side effects)
    metadata = ProjectMetadata(
        project_id=config.project_id,
        display_name=config.display_name,
        repository_root=config.repository_root,
        authoritative_base_branch=config.authoritative_base_branch,
        agents_may_merge=config.agents_may_merge,
        owner_ids=config.owner_ids,
        agent_owners=config.agent_owners,
        prohibited_operations=config.prohibited_operations,
    )

    # Step 3: return fully-populated ProjectContext
    # All adapter fields are deferred stubs; 002B replaces with real adapters
    return ProjectContext(
        config=config,
        git=_DeferredGitAdapter(),
        governance=_DeferredGovernanceAdapter(),
        workflow=_DeferredWorkflowAdapter(),
        qa=_DeferredQAAdapter(),
        files=_DeferredFileReadAdapter(),
        events=_DeferredEventAdapter(),
        metadata=metadata,
    )
