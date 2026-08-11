from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from typing import Callable

from dashboard_api.engineering_read_model import (
    EngineeringDashboardReadModel,
    ReportIndex,
    RepositorySummary,
)
from engineering.engineering_events import EngineeringEvent, EventSeverity, EventType, sanitize_payload
from engineering.event_store import StoredEvent
from engineering.query_service import EngineeringQueryService
from engineering.workflow_store import StoredWorkflow, WorkflowStore
import sqlite3


DEFAULT_AUDIT_ARCHIVE_ROOT = Path("/root/.openclaw/audit-archives")
MAX_DIRTY_PATHS = 100
MAX_GIT_OUTPUT_CHARS = 2_000


@dataclass(frozen=True)
class EngineeringDashboardProviderConfig:
    """Explicit read-only construction settings for the engineering dashboard."""

    repo_root: Path
    backlog_path: Path | None = None
    workflow_state_path: Path | None = None
    event_store_path: Path | None = None
    workflow_report_dir: Path | None = None
    audit_archive_root: Path | None = DEFAULT_AUDIT_ARCHIVE_ROOT
    project_identity: str = "trading-bot"
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    @property
    def resolved_repo_root(self) -> Path:
        return self.repo_root.resolve()

    @property
    def resolved_backlog_path(self) -> Path | None:
        return self.backlog_path

    @property
    def resolved_workflow_state_path(self) -> Path | None:
        return self.workflow_state_path

    @property
    def resolved_event_store_path(self) -> Path | None:
        return self.event_store_path

    @property
    def resolved_audit_root(self) -> Path | None:
        if self.audit_archive_root is None:
            return None
        return self.audit_archive_root / self.resolved_repo_root.name


@dataclass(frozen=True)
class GitRepositorySummaryReader:
    """Read-only Git-backed repository summary for dashboard snapshots."""

    repo_root: Path

    def summary(self) -> RepositorySummary:
        root = self.repo_root.resolve()
        branch = self._git("branch", "--show-current") or None
        dirty_output = self._git("status", "--porcelain")
        dirty_paths = _parse_dirty_paths(dirty_output)
        ahead_count, behind_count = self._ahead_behind()
        latest_commit = self._git("rev-parse", "HEAD") or None
        latest_subject = self._git("log", "-1", "--pretty=%s") or None
        return RepositorySummary(
            root=str(root),
            branch=branch,
            is_clean=not dirty_paths,
            dirty_paths=tuple(dirty_paths[:MAX_DIRTY_PATHS]),
            sync_state=_sync_state(ahead_count, behind_count),
            ahead_count=ahead_count,
            behind_count=behind_count,
            latest_commit=latest_commit,
            latest_commit_subject=latest_subject,
        )

    def _ahead_behind(self) -> tuple[int | None, int | None]:
        upstream = self._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
        if not upstream:
            return None, None
        counts = self._git("rev-list", "--left-right", "--count", f"{upstream}...HEAD", check=False)
        parts = counts.split()
        if len(parts) != 2:
            return None, None
        try:
            behind, ahead = int(parts[0]), int(parts[1])
        except ValueError:
            return None, None
        return ahead, behind

    def _git(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if check and result.returncode != 0:
            stderr = (result.stderr or "").strip()[:MAX_GIT_OUTPUT_CHARS]
            raise RuntimeError(f"git {' '.join(args)} failed with exit {result.returncode}: {stderr}")
        if result.returncode != 0:
            return ""
        return result.stdout.strip()[:MAX_GIT_OUTPUT_CHARS]


@dataclass(frozen=True)
class ReadOnlyEngineeringEventStore:
    """Minimal read-only event source for EngineeringQueryService.

    It implements only the query methods used by EngineeringQueryService and never
    creates, migrates, or writes the event database.
    """

    path: Path

    def append(self, event: EngineeringEvent) -> bool:
        raise RuntimeError("read-only engineering event source cannot append")

    def list_events(self, *, limit: int = 100) -> tuple[StoredEvent, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("Event query limit must be between 1 and 500")
        if not self.path.is_file():
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM engineering_events ORDER BY sequence DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._stored_event(row) for row in reversed(rows))

    def pause_state(self) -> dict[str, object]:
        if not self.path.is_file():
            return {
                "revision": 0,
                "paused": False,
                "actor": "",
                "reason": "",
                "updated_at": "1970-01-01T00:00:00+00:00",
            }
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM control_state WHERE singleton = 1").fetchone()
        if row is None:
            return {
                "revision": 0,
                "paused": False,
                "actor": "",
                "reason": "",
                "updated_at": "1970-01-01T00:00:00+00:00",
            }
        return {
            "revision": row["revision"],
            "paused": bool(row["paused"]),
            "actor": row["actor"],
            "reason": row["reason"],
            "updated_at": row["updated_at"],
        }

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.path}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _stored_event(self, row: sqlite3.Row) -> StoredEvent:
        return StoredEvent(
            sequence=row["sequence"],
            event=EngineeringEvent(
                event_id=row["event_id"],
                schema_version=row["schema_version"],
                event_type=EventType(row["event_type"]),
                severity=EventSeverity(row["severity"]),
                occurred_at=row["occurred_at"],
                task_id=row["task_id"],
                workflow_id=row["workflow_id"],
                request_id=row["request_id"],
                run_id=row["run_id"],
                causation_id=row["causation_id"],
                correlation_id=row["correlation_id"],
                payload=sanitize_payload(json.loads(row["payload_json"])),
            ),
        )



@dataclass(frozen=True)
class ReadOnlyWorkflowAdapter:
    """WorkflowAdapter-compatible wrapper that avoids event-store writes."""

    workflow_state_path: Path
    event_source: object

    def workflow_store(self) -> WorkflowStore:
        return WorkflowStore(self.workflow_state_path, event_store=self.event_source)

    def event_store(self) -> object:
        return self.event_source

    def archive_completed(self, workflow: StoredWorkflow) -> Path:
        raise RuntimeError("read-only workflow adapter cannot archive workflows")


def create_engineering_query_service(
    config: EngineeringDashboardProviderConfig,
) -> EngineeringQueryService:
    """Wire EngineeringQueryService using explicit adapter-backed config paths."""
    event_store_path = config.resolved_event_store_path
    workflow_state_path = config.resolved_workflow_state_path
    backlog_path = config.resolved_backlog_path

    if event_store_path is None or workflow_state_path is None or backlog_path is None:
        raise ValueError(
            "create_engineering_query_service requires explicit non-None "
            "event_store_path, workflow_state_path, and backlog_path in config"
        )

    event_adapter = ReadOnlyEngineeringEventStore(event_store_path)
    workflow_adapter = ReadOnlyWorkflowAdapter(workflow_state_path, event_adapter)
    return EngineeringQueryService(
        event_source=event_adapter,
        workflow_source=workflow_adapter,
        backlog_path=backlog_path,
    )


def create_engineering_dashboard_provider(
    config: EngineeringDashboardProviderConfig | None,
) -> EngineeringDashboardReadModel:
    """Create dashboard read model with explicit config."""
    if config is None:
        raise TypeError("create_engineering_dashboard_provider requires explicit config")
    return EngineeringDashboardReadModel(
        query_service=create_engineering_query_service(config),
        repository_reader=GitRepositorySummaryReader(config.resolved_repo_root),
        report_index=ReportIndex(
            repo_root=config.resolved_repo_root,
            audit_root=config.resolved_audit_root,
        ),
        clock=config.clock,
        project_identity=config.project_identity,
    )


def _parse_dirty_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path[:MAX_GIT_OUTPUT_CHARS])
    return paths


def _sync_state(ahead: int | None, behind: int | None) -> str:
    if ahead is None or behind is None:
        return "unknown"
    if ahead and behind:
        return "diverged"
    if ahead:
        return "ahead"
    if behind:
        return "behind"
    return "up_to_date"
