"""ProjectContext factory — ENGPLAT-002B concrete adapters.

Factory for building a ProjectContext from a ProjectConfig.

ENGPLAT-002B provides concrete GovernanceAdapter, WorkflowAdapter, and
EventAdapter implementations. Deferred adapters (git, qa, files) raise
CapabilityUnavailable. The factory is side-effect free: no filesystem
artifacts are created at construction time.

No side effects: no file creation, network access, Git mutation, QA execution,
or workflow state changes. All errors are deterministic and sanitized.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from engineering.adapters import (
    CapabilityUnavailable,
    EventAdapter,
    FileReadAdapter,
    GitReadAdapter,
    GovernanceAdapter,
    ProjectContext,
    ProjectMetadata,
    QAAdapter,
    WorkflowAdapter,
)
from engineering.backlog import load_backlog
from engineering.event_store import EngineeringEventStore
from engineering.models import (
    GovernanceFiles,
    ProjectConfig,
    WorkflowFiles,
    validate_project_config,
)
from engineering.workflow_store import WorkflowStore

if TYPE_CHECKING:
    from engineering.engineering_events import EngineeringEvent
    from engineering.models import BacklogTask, StoredEvent, StoredWorkflow


# ---------------------------------------------------------------------------
# Deferred adapter stubs — raise CapabilityUnavailable (002C provides concrete)
# --------------------------------------------------------------------------_
# Defined at module level so they are importable by existing tests.
# These are used ONLY for git, qa, files (deferred to 002C).


class _DeferredGitAdapter(GitReadAdapter):
    """Deferred GitReadAdapter — raises CapabilityUnavailable (002C provides concrete)."""

    def __init__(self, project_id: str):
        self._project_id = project_id

    def current_branch(self) -> str:
        raise CapabilityUnavailable(self._project_id, "git")

    def is_clean(self) -> bool:
        raise CapabilityUnavailable(self._project_id, "git")

    def repository_state(self):
        raise CapabilityUnavailable(self._project_id, "git")

    def branch_exists(self, branch: str) -> bool:
        raise CapabilityUnavailable(self._project_id, "git")

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        raise CapabilityUnavailable(self._project_id, "git")


class _DeferredQAAdapter(QAAdapter):
    """Deferred QAAdapter — raises CapabilityUnavailable (002C provides concrete)."""

    def __init__(self, project_id: str):
        self._project_id = project_id

    def configured_command(self) -> tuple[str, ...]:
        raise CapabilityUnavailable(self._project_id, "qa")

    def timeout_seconds(self) -> int:
        raise CapabilityUnavailable(self._project_id, "qa")


class _DeferredFileReadAdapter(FileReadAdapter):
    """Deferred FileReadAdapter — raises CapabilityUnavailable (002C provides concrete)."""

    def __init__(self, project_id: str):
        self._project_id = project_id

    def resolve(self, relative_path: Path) -> Path:
        raise CapabilityUnavailable(self._project_id, "files")

    def exists(self, relative_path: Path) -> bool:
        raise CapabilityUnavailable(self._project_id, "files")

    def read_text(self, relative_path: Path) -> str:
        raise CapabilityUnavailable(self._project_id, "files")


# ---------------------------------------------------------------------------
# Concrete adapters (ENGPLAT-002B)
# ---------------------------------------------------------------------------


class GovernanceAdapterImpl(GovernanceAdapter):
    """Concrete GovernanceAdapter wrapping backlog.py and governance file reads.

    Paths derive exclusively from the supplied GovernanceFiles. No hard-coded
    filenames. All file reads are bounded by repository_root containment.
    No writes.
    """

    def __init__(self, governance_files: GovernanceFiles, repository_root: Path):
        self._governance_files = governance_files
        self._repository_root = repository_root

    def _read_governance_file(self, path: Path) -> str:
        """Read a governance file, enforcing path containment."""
        try:
            resolved = path.resolve()
            repo_resolved = self._repository_root.resolve()
            resolved.relative_to(repo_resolved)
        except ValueError:
            raise ValueError(
                f"path escapes repository_root: {path}"
            )
        return path.read_text(encoding="utf-8")

    def load_backlog(self) -> tuple[BacklogTask, ...]:
        return load_backlog(self._governance_files.backlog_path)

    def load_owners(self) -> str:
        return self._read_governance_file(self._governance_files.owners_path)

    def load_operating_plan(self) -> str:
        return self._read_governance_file(self._governance_files.operating_plan_path)

    def load_handoff(self) -> str:
        return self._read_governance_file(self._governance_files.handoff_path)


class EventAdapterImpl(EventAdapter):
    """Concrete EventAdapter with lazy EngineeringEventStore construction.

    EngineeringEventStore is NOT constructed in __init__; it is lazily
    initialized on first real operation (append, list_events, pause_state).
    No filesystem artifact (directory, schema, or DB file) is created at
    construction time.
    """

    def __init__(self, event_store_path: Path):
        self._event_store_path = event_store_path
        self._store: EngineeringEventStore | None = None

    def _get_store(self) -> EngineeringEventStore:
        """Lazily construct the event store on first use."""
        if self._store is None:
            self._store = EngineeringEventStore(self._event_store_path)
        return self._store

    def append(self, event: EngineeringEvent) -> bool:
        return self._get_store().append(event)

    def list_events(self, limit: int = 100) -> tuple[StoredEvent, ...]:
        return self._get_store().list_events(limit=limit)

    def pause_state(self) -> dict[str, object]:
        return self._get_store().pause_state()


class WorkflowAdapterImpl(WorkflowAdapter):
    """Concrete WorkflowAdapter composing WorkflowStore and EventAdapterImpl.

    WorkflowStore is constructed eagerly (its __init__ has no side effects).
    EngineeringEventStore is accessed through the EventAdapterImpl lazy accessor.
    """

    def __init__(
        self,
        workflow_files: WorkflowFiles,
        event_adapter: EventAdapterImpl,
    ):
        self._workflow_files = workflow_files
        self._event_adapter = event_adapter
        self._workflow_store: WorkflowStore | None = None

    def workflow_store(self) -> WorkflowStore:
        if self._workflow_store is None:
            self._workflow_store = WorkflowStore(
                self._workflow_files.workflow_store_path,
                event_store=self._event_adapter._get_store(),
            )
        return self._workflow_store

    def event_store(self) -> EngineeringEventStore:
        return self._event_adapter._get_store()

    def archive_completed(self, workflow: StoredWorkflow) -> Path:
        return self.workflow_store().archive_completed(workflow)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_project_context(config: ProjectConfig) -> ProjectContext:
    """Build a ProjectContext from a validated ProjectConfig.

    Contract (ENGPLAT-002B)
    -----------------------
    1. Validates ``config`` internally via ``validate_project_config()``.
    2. Raises ``ValueError`` (deterministic, sanitized) on semantic failure.
       Error messages contain no secrets, raw paths, or command output.
    3. Constructs ``ProjectMetadata`` from the config.
    4. Constructs concrete GovernanceAdapterImpl, WorkflowAdapterImpl,
       EventAdapterImpl for governance, workflow, events.
       Deferred stubs for git, qa, files raise CapabilityUnavailable.
    5. No side effects at factory time: no file creation, network, Git mutation,
       QA execution, or workflow state changes.

    Parameters
    ----------
    config:
        A fully-constructed ``ProjectConfig`` instance. Must have passed
        structural parsing (``parse_project_config``) before being passed here.

    Returns
    -------
    ProjectContext
        A fully populated context with concrete adapters for governance,
        workflow, events and deferred stubs for git, qa, files.

    Raises
    ------
    ValueError
        When semantic validation of ``config`` fails. The error message is
        deterministic and contains no secrets, raw paths, or command output.
    """
    # Step 1: semantic validation — fail closed on any semantic error
    validation_errors = validate_project_config(config)
    if validation_errors:
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

    # Step 3: construct concrete adapters (no side effects at construction time)
    event_adapter = EventAdapterImpl(config.workflow_files.event_store_path)
    workflow_adapter = WorkflowAdapterImpl(
        config.workflow_files,
        event_adapter,
    )
    governance_adapter = GovernanceAdapterImpl(
        config.governance_files,
        config.repository_root,
    )

    # Step 4: return fully-populated ProjectContext
    return ProjectContext(
        config=config,
        git=_DeferredGitAdapter(config.project_id),
        governance=governance_adapter,
        workflow=workflow_adapter,
        qa=_DeferredQAAdapter(config.project_id),
        files=_DeferredFileReadAdapter(config.project_id),
        events=event_adapter,
        metadata=metadata,
    )
