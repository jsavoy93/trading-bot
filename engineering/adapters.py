"""Engineering platform adapter Protocol types, ProjectMetadata, and ProjectContext.

ENGPLAT-002A contract: defines the reusable dependency contracts without
implementing concrete adapters or integrating existing services.

All Protocol types are marked @runtime_checkable so structural tests can
verify adapter conformance using isinstance() checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from engineering.models import (
        BacklogTask,
        EngineeringEvent,
        ProjectConfig,
        RepositoryState,
        StoredEvent,
        StoredWorkflow,
    )
    from engineering.event_store import EngineeringEventStore
    from engineering.workflow_store import WorkflowStore


# ---------------------------------------------------------------------------
# Adapter Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class GitReadAdapter(Protocol):
    """Read-only Git operations scoped to a repository.

    Mutation methods (e.g. prepare_feature_branch) are deferred to a future
    GitMutationAdapter. This adapter never modifies repository state.
    """

    def current_branch(self) -> str:
        """Return the currently checked-out branch name."""
        ...

    def is_clean(self) -> bool:
        """Return True if the working tree has no uncommitted changes."""
        ...

    def repository_state(self) -> "RepositoryState":
        """Return the current repository state (root, branch, cleanliness)."""
        ...

    def branch_exists(self, branch: str) -> bool:
        """Return True if the named branch exists in the repository."""
        ...

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """Return True if `ancestor` is a parent/ancestor of `descendant`."""
        ...


@runtime_checkable
class GovernanceAdapter(Protocol):
    """Bounded access to project governance documents.

    Implementations may cache documents for the adapter lifetime.
    """

    def load_backlog(self) -> tuple["BacklogTask", ...]:
        """Load and return all backlog tasks from the authoritative backlog."""
        ...

    def load_owners(self) -> str:
        """Return the raw content of the OWNERS document."""
        ...

    def load_operating_plan(self) -> str:
        """Return the raw content of the AGENT_OPERATING_PLAN document."""
        ...

    def load_handoff(self) -> str:
        """Return the raw content of the handoff document."""
        ...


@runtime_checkable
class WorkflowAdapter(Protocol):
    """Bounded access to workflow and event persistence.

    All store types are already project-scoped via ProjectConfig.workflow_files.
    This adapter scopes access to the configured project only.
    """

    def workflow_store(self) -> "WorkflowStore":
        """Return the workflow persistence store for this project."""
        ...

    def event_store(self) -> "EngineeringEventStore":
        """Return the event persistence store for this project."""
        ...

    def archive_completed(self, workflow: "StoredWorkflow") -> Path:
        """Archive a completed workflow record and return the archive path."""
        ...


@runtime_checkable
class QAAdapter(Protocol):
    """QA configuration access.

    run_qa() execution is deferred to 002C. This adapter provides only
    configuration access (command assembly and timeout).
    """

    def configured_command(self) -> tuple[str, ...]:
        """Return the configured QA command as a tuple of string segments."""
        ...

    def timeout_seconds(self) -> int:
        """Return the configured QA timeout in seconds (always positive)."""
        ...


@runtime_checkable
class FileReadAdapter(Protocol):
    """Project-root-bounded filesystem read access.

    All paths are resolved relative to the repository_root. The resolve()
    method enforces that no path can escape the repository boundary.
    Write methods are deferred to a future FileWriteAdapter.
    """

    def resolve(self, relative_path: Path) -> Path:
        """Resolve a relative path within the project.

        Raises ValueError if the resolved path escapes repository_root
        (e.g. via ".." path traversal or symlink traversal).
        """
        ...

    def exists(self, relative_path: Path) -> bool:
        """Return True if the relative path exists within the project."""
        ...

    def read_text(self, relative_path: Path) -> str:
        """Return the text content of the relative path within the project."""
        ...


@runtime_checkable
class EventAdapter(Protocol):
    """Bounded event append and query operations.

    All operations are scoped to a single project's event store.
    """

    def append(self, event: "EngineeringEvent") -> bool:
        """Append an event to the project's event store. Returns True on success."""
        ...

    def list_events(self, limit: int = 100) -> tuple["StoredEvent", ...]:
        """List the most recent events, up to the mandatory limit.

        Parameters
        ----------
        limit:
            Maximum number of events to return. Required by the contract;
            implementations must not ignore this parameter.
        """
        ...

    def pause_state(self) -> dict[str, object]:
        """Return the current pause/resume state dict for the project."""
        ...


# ---------------------------------------------------------------------------
# CapabilityUnavailable
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityUnavailable(Exception):
    """Explicit unavailable-capability error for deferred ProjectContext fields.

    Raised deterministically when a ProjectContext git, qa, or files adapter
    is accessed before ENGPLAT-002C provides a concrete implementation.

    The message is bounded and contains only project_id and capability name.
    No raw paths, secrets, or command output are included.
    """

    project_id: str
    capability: str  # one of: "git", "qa", "files"

    def __str__(self) -> str:
        return (
            f"capability {self.capability!r} is not yet available "
            f"for project {self.project_id!r} (ENGPLAT-002C pending)"
        )


# ---------------------------------------------------------------------------
# ProjectMetadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectMetadata:
    """Static identity and policy for a managed project.

    This is a pure value object derived from ProjectConfig at context-build
    time. It contains no runtime state or references to stores/adapters.
    """

    project_id: str
    display_name: str
    repository_root: Path
    authoritative_base_branch: str
    agents_may_merge: bool
    owner_ids: tuple[str, ...]
    agent_owners: tuple[str, ...]
    prohibited_operations: tuple[str, ...]


# ---------------------------------------------------------------------------
# ProjectContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectContext:
    """Read-only runtime dependency container for engineering services.

    All fields are required. There are no optional adapters.

    Propagation rule: downstream services receive only the narrow adapter
    or data dependency they require; they do not receive the full
    ProjectContext unless explicitly needed.

    Immutability: ProjectContext is a frozen dataclass. All adapters are
    also effectively immutable during a workflow run (no shared mutable
    state). This makes ProjectContext safe to pass across concurrent
    execution boundaries.

    Construction: use build_project_context() factory in engineering.context.
    """

    config: "ProjectConfig"
    git: GitReadAdapter
    governance: GovernanceAdapter
    workflow: WorkflowAdapter
    qa: QAAdapter
    files: FileReadAdapter
    events: EventAdapter
    metadata: ProjectMetadata
