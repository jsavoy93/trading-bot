from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


MAX_EVENTS = 50
MAX_REPORTS = 10
MAX_DIR_ENTRIES = 250
MAX_WARNING_DETAIL_CHARS = 1_000


@dataclass(frozen=True)
class HealthWarning:
    source: str
    severity: str
    message: str
    detail: str | None = None


@dataclass(frozen=True)
class RepositorySummary:
    root: str
    branch: str | None
    is_clean: bool | None
    dirty_paths: tuple[str, ...] = ()
    sync_state: str = "unknown"
    ahead_count: int | None = None
    behind_count: int | None = None
    latest_commit: str | None = None
    latest_commit_subject: str | None = None


@dataclass(frozen=True)
class BacklogSummary:
    active_task_id: str | None
    active_task_title: str | None
    status: str | None
    owner: str | None
    priority: str | None
    counts_by_status: Mapping[str, int]
    counts_by_priority: Mapping[str, int]


@dataclass(frozen=True)
class WorkflowSummary:
    active: bool
    task_id: str | None
    feature_branch: str | None
    stage: str | None
    owner_agent: str | None
    blocker: str | None
    execution_status: str | None
    updated_at: str | None


@dataclass(frozen=True)
class ApprovalSummary:
    pending: bool
    reason: str | None
    task_id: str | None
    requested_at: str | None
    next_action: str | None


@dataclass(frozen=True)
class TestSummary:
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    passed_count: int | None = None
    failed_count: int | None = None
    timed_out: bool | None = None
    completed_at: str | None = None
    summary: str | None = None


@dataclass(frozen=True)
class PullRequestSummary:
    url: str | None
    number: int | None
    state: str | None
    target_branch: str | None
    head_branch: str | None
    mergeable: bool | None
    available: bool = False


@dataclass(frozen=True)
class ReportSummary:
    path: str
    kind: str
    task_id: str | None
    generated_at: str | None
    title: str | None


@dataclass(frozen=True)
class DashboardSnapshot:
    project_identity: str
    repository: RepositorySummary
    backlog: BacklogSummary
    workflow: WorkflowSummary
    approval: ApprovalSummary
    latest_execution_result: str | None
    latest_test_result: TestSummary | None
    latest_commit: str | None
    pull_request: PullRequestSummary | None
    recent_events: tuple[Mapping[str, Any], ...]
    recent_reports: tuple[ReportSummary, ...]
    health_warnings: tuple[HealthWarning, ...]
    data_freshness_timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return _to_plain(self)


class QuerySnapshotReader(Protocol):
    def snapshot(self, *, timeline_limit: int = 100) -> Mapping[str, Any]: ...


class RepositorySummaryReader(Protocol):
    def summary(self) -> RepositorySummary: ...


class PullRequestMetadataReader(Protocol):
    def for_branch(self, head_branch: str | None) -> PullRequestSummary | None: ...


@dataclass(frozen=True)
class ReportIndex:
    repo_root: Path
    audit_root: Path | None = None
    max_reports: int = MAX_REPORTS

    def recent(self) -> tuple[ReportSummary, ...]:
        candidates: list[tuple[float, ReportSummary]] = []
        self._add_file(candidates, self.repo_root / "REPORT.md", "rolling")
        self._add_directory(candidates, self.repo_root / "reports", "repo_archive")
        if self.audit_root is not None:
            self._add_directory(candidates, self.audit_root, "audit_archive")
        candidates.sort(key=lambda item: item[0], reverse=True)
        return tuple(item[1] for item in candidates[: self.max_reports])

    def _add_directory(
        self, candidates: list[tuple[float, ReportSummary]], directory: Path, kind: str
    ) -> None:
        if not directory.is_dir():
            return
        for index, path in enumerate(sorted(directory.iterdir(), key=lambda item: item.name)):
            if index >= MAX_DIR_ENTRIES:
                break
            if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
                self._add_file(candidates, path, kind)

    def _add_file(
        self, candidates: list[tuple[float, ReportSummary]], path: Path, kind: str
    ) -> None:
        if not path.is_file():
            return
        stat = path.stat()
        task_id, title = _parse_report_heading(path)
        generated_at = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
        candidates.append((stat.st_mtime, ReportSummary(str(path), kind, task_id, generated_at, title)))


@dataclass(frozen=True)
class StaticRepositorySummaryReader:
    repository: RepositorySummary

    def summary(self) -> RepositorySummary:
        return self.repository


@dataclass(frozen=True)
class EmptyPullRequestMetadataReader:
    def for_branch(self, head_branch: str | None) -> PullRequestSummary | None:
        return PullRequestSummary(
            url=None,
            number=None,
            state=None,
            target_branch=None,
            head_branch=head_branch,
            mergeable=None,
            available=False,
        )


@dataclass
class EngineeringDashboardReadModel:
    query_service: QuerySnapshotReader
    repository_reader: RepositorySummaryReader
    report_index: ReportIndex | None = None
    pr_reader: PullRequestMetadataReader | None = None
    clock: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(UTC))
    project_identity: str = "trading-bot"
    event_limit: int = MAX_EVENTS

    def snapshot(self) -> DashboardSnapshot:
        warnings: list[HealthWarning] = []
        freshness = _utc_iso(self.clock())
        query_data: Mapping[str, Any] = {}
        try:
            query_data = self.query_service.snapshot(timeline_limit=self.event_limit)
        except Exception as exc:  # pragma: no cover - exercised by tests with generic failure
            warnings.append(_warning("query_service", "Engineering query snapshot is unavailable.", exc))

        repository = self._repository_summary(warnings)
        recent_reports = self._recent_reports(warnings)
        recent_events = _bounded_events(query_data.get("timeline"), self.event_limit)
        backlog = _backlog_summary(query_data)
        workflow = _workflow_summary(query_data, backlog)
        approval = _approval_summary(query_data, recent_events, workflow)
        latest_test = _test_summary(query_data.get("tests"))
        latest_execution = _latest_execution_result(query_data, workflow, latest_test)
        pull_request = self._pull_request_summary(workflow, repository, warnings)

        if repository.is_clean is False:
            warnings.append(
                HealthWarning(
                    source="repository",
                    severity="WARNING",
                    message="Repository has uncommitted or untracked changes.",
                    detail=", ".join(repository.dirty_paths[:20]) or None,
                )
            )
        if pull_request is not None and not pull_request.available:
            warnings.append(
                HealthWarning(
                    source="github",
                    severity="INFO",
                    message="Pull-request metadata is unavailable.",
                    detail=None,
                )
            )

        return DashboardSnapshot(
            project_identity=self.project_identity,
            repository=repository,
            backlog=backlog,
            workflow=workflow,
            approval=approval,
            latest_execution_result=latest_execution,
            latest_test_result=latest_test,
            latest_commit=repository.latest_commit,
            pull_request=pull_request,
            recent_events=recent_events,
            recent_reports=recent_reports,
            health_warnings=tuple(warnings),
            data_freshness_timestamp=freshness,
        )

    def _repository_summary(self, warnings: list[HealthWarning]) -> RepositorySummary:
        try:
            return self.repository_reader.summary()
        except Exception as exc:
            warnings.append(_warning("repository", "Repository summary is unavailable.", exc))
            return RepositorySummary(root="", branch=None, is_clean=None, sync_state="unavailable")

    def _recent_reports(self, warnings: list[HealthWarning]) -> tuple[ReportSummary, ...]:
        if self.report_index is None:
            return ()
        try:
            return self.report_index.recent()
        except Exception as exc:
            warnings.append(_warning("reports", "Recent reports are unavailable.", exc))
            return ()

    def _pull_request_summary(
        self,
        workflow: WorkflowSummary,
        repository: RepositorySummary,
        warnings: list[HealthWarning],
    ) -> PullRequestSummary | None:
        reader = self.pr_reader or EmptyPullRequestMetadataReader()
        branch = workflow.feature_branch or repository.branch
        try:
            return reader.for_branch(branch)
        except Exception as exc:
            warnings.append(_warning("github", "Pull-request metadata lookup failed.", exc))
            return PullRequestSummary(None, None, None, None, branch, None, available=False)


def _backlog_summary(query_data: Mapping[str, Any]) -> BacklogSummary:
    backlog_items = _as_sequence(query_data.get("backlog"))
    counts_by_status: dict[str, int] = {}
    counts_by_priority: dict[str, int] = {}
    for item in backlog_items:
        if not isinstance(item, Mapping):
            continue
        status = _text(item.get("status")) or "UNKNOWN"
        priority = _text(item.get("priority")) or "UNKNOWN"
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        counts_by_priority[priority] = counts_by_priority.get(priority, 0) + 1

    task = query_data.get("current_task")
    if isinstance(task, Mapping):
        return BacklogSummary(
            active_task_id=_text(task.get("id")),
            active_task_title=_text(task.get("title")),
            status=_status_for_task(backlog_items, _text(task.get("id"))),
            owner=_text(task.get("owner")),
            priority=_text(task.get("priority")),
            counts_by_status=counts_by_status,
            counts_by_priority=counts_by_priority,
        )
    return BacklogSummary(None, None, None, None, None, counts_by_status, counts_by_priority)


def _workflow_summary(query_data: Mapping[str, Any], backlog: BacklogSummary) -> WorkflowSummary:
    task = query_data.get("current_task")
    agent = query_data.get("agent_run")
    gaps = tuple(str(value) for value in _as_sequence(query_data.get("remaining_gaps")) if value)
    blocker = next((gap for gap in gaps if "blocked" in gap.lower() or "gap" in gap.lower()), None)
    if isinstance(agent, Mapping) and _text(agent.get("failure_reason")):
        blocker = _text(agent.get("failure_reason"))
    if isinstance(task, Mapping):
        return WorkflowSummary(
            active=True,
            task_id=_text(task.get("id")),
            feature_branch=_text(task.get("feature_branch")),
            stage=_text(task.get("state")),
            owner_agent=_text(agent.get("agent_name")) if isinstance(agent, Mapping) else backlog.owner,
            blocker=blocker,
            execution_status=_text(agent.get("status")) if isinstance(agent, Mapping) else None,
            updated_at=_text(agent.get("updated_at")) if isinstance(agent, Mapping) else None,
        )
    return WorkflowSummary(False, None, None, None, None, gaps[0] if gaps else None, None, None)


def _approval_summary(
    query_data: Mapping[str, Any],
    events: tuple[Mapping[str, Any], ...],
    workflow: WorkflowSummary,
) -> ApprovalSummary:
    report = query_data.get("report")
    if isinstance(report, Mapping) and _text(report.get("recommendation")) == "ACCEPT":
        return ApprovalSummary(
            pending=True,
            reason="Workflow report recommends acceptance.",
            task_id=_text(report.get("task_id")) or workflow.task_id,
            requested_at=_text(report.get("generated_at")),
            next_action=_text(report.get("next_action")),
        )
    approval_event = next(
        (event for event in reversed(events) if _text(event.get("event_type")) == "approval.required"),
        None,
    )
    if approval_event is not None:
        payload = approval_event.get("payload") if isinstance(approval_event.get("payload"), Mapping) else {}
        return ApprovalSummary(
            pending=True,
            reason="Approval-required event recorded.",
            task_id=_text(approval_event.get("task_id")) or workflow.task_id,
            requested_at=_text(approval_event.get("occurred_at")),
            next_action=_text(payload.get("next_action")) if isinstance(payload, Mapping) else None,
        )
    return ApprovalSummary(False, None, workflow.task_id, None, _text(query_data.get("recommended_next_step")))


def _test_summary(raw: object) -> TestSummary | None:
    if not isinstance(raw, Mapping):
        return None
    command = raw.get("command")
    command_tuple = tuple(str(item) for item in command) if isinstance(command, (tuple, list)) else ()
    return TestSummary(
        command=command_tuple,
        exit_code=_int_or_none(raw.get("exit_code")),
        passed_count=_int_or_none(raw.get("passed_count")),
        failed_count=_int_or_none(raw.get("failed_count")),
        timed_out=raw.get("timed_out") if isinstance(raw.get("timed_out"), bool) else None,
        completed_at=_text(raw.get("completed_at")),
        summary=_text(raw.get("output_summary")),
    )


def _latest_execution_result(
    query_data: Mapping[str, Any], workflow: WorkflowSummary, latest_test: TestSummary | None
) -> str | None:
    agent = query_data.get("agent_run")
    if isinstance(agent, Mapping) and _text(agent.get("status")):
        status = _text(agent.get("status"))
        exit_code = _int_or_none(agent.get("exit_code"))
        return f"agent {status}" + (f" exit={exit_code}" if exit_code is not None else "")
    if latest_test is not None and latest_test.exit_code is not None:
        return f"tests exit={latest_test.exit_code}"
    return workflow.execution_status


def _bounded_events(raw: object, limit: int) -> tuple[Mapping[str, Any], ...]:
    events: list[Mapping[str, Any]] = []
    for item in _as_sequence(raw)[:limit]:
        if isinstance(item, Mapping):
            events.append(_to_plain(item))
        else:
            events.append({"message": str(item)})
    return tuple(events)


def _status_for_task(items: tuple[object, ...], task_id: str | None) -> str | None:
    if task_id is None:
        return None
    for item in items:
        if isinstance(item, Mapping) and _text(item.get("id")) == task_id:
            return _text(item.get("status"))
    return None


def _parse_report_heading(path: Path) -> tuple[str | None, str | None]:
    task_id = None
    title = None
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:40]:
            stripped = line.strip("# ").strip()
            if not title and stripped:
                title = stripped
            if "Task:" in line:
                task_id = line.split("Task:", 1)[1].strip()[:100]
            elif "Backlog item" in line and ":" in line:
                task_id = line.split(":", 1)[1].strip()[:100]
    except OSError:
        return None, None
    return task_id, title


def _warning(source: str, message: str, exc: Exception) -> HealthWarning:
    return HealthWarning(source, "WARNING", message, f"{type(exc).__name__}: {exc}"[:MAX_WARNING_DETAIL_CHARS])


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _as_sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return ()


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_to_plain(item) for item in value)
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value
