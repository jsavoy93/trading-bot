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
class TaskStatusSummary:
    task_id: str | None
    title: str | None
    status: str | None
    assigned_agent: str | None
    current_phase: str | None
    priority: str | None
    started_at: str | None
    last_updated: str | None
    blocking_reason: str | None
    completion_percent: int


@dataclass(frozen=True)
class AgentActivitySummary:
    project_id: str
    task_id: str | None
    task_title: str | None
    agent_name: str | None
    agent_role: str | None
    workflow_id: str | None
    run_id: str | None
    branch: str | None
    phase: str | None
    status: str
    started_at: str | None
    updated_at: str | None
    elapsed_seconds: float | None
    latest_activity_at: str | None
    latest_activity: str | None
    last_completed_action: str | None
    blocker: str | None
    timeout_state: str
    recovery_state: str
    safe_detail: str | None


@dataclass(frozen=True)
class RecentExecutionSummary:
    project_id: str
    task_id: str | None
    agent_name: str | None
    run_id: str | None
    branch: str | None
    final_status: str
    started_at: str | None
    completed_at: str | None
    elapsed_seconds: float | None
    last_completed_action: str | None
    result_summary: str | None


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
    duration_seconds: float | None = None
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
    outcome: str | None = None


@dataclass(frozen=True)
class EngineeringHealthSummary:
    overall_status: str
    repository_safe: bool | None
    current_branch: str | None
    current_commit: str | None
    last_successful_regression_run: str | None
    degraded_sources: tuple[str, ...]
    warning_count: int


@dataclass(frozen=True)
class TestingSummary:
    focused: TestSummary | None
    regression: TestSummary | None
    full_suite: TestSummary | None
    latest_status: str | None
    warning_count: int | None


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
    engineering_health: EngineeringHealthSummary | None = None
    current_tasks: tuple[TaskStatusSummary, ...] = ()
    blockers: tuple[str, ...] = ()
    testing: TestingSummary | None = None
    live_activity: tuple[AgentActivitySummary, ...] = ()
    recent_executions: tuple[RecentExecutionSummary, ...] = ()

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
        outcome = _parse_report_outcome(path)
        generated_at = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
        candidates.append((stat.st_mtime, ReportSummary(str(path), kind, task_id, generated_at, title, outcome)))


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
        blockers = _blockers(query_data, workflow, approval, warnings)
        current_tasks = _current_tasks(query_data, backlog, workflow, blockers)
        testing = _testing_summary(latest_test, recent_reports)
        engineering_health = _engineering_health(repository, latest_test, warnings, blockers)
        live_activity = _live_activity(
            query_data, backlog, workflow, blockers, recent_events,
            project_id=self.project_identity, now=self.clock(),
        )
        recent_executions = _recent_executions(
            query_data, workflow, recent_events, project_id=self.project_identity, now=self.clock()
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
            engineering_health=engineering_health,
            current_tasks=current_tasks,
            blockers=blockers,
            testing=testing,
            live_activity=live_activity,
            recent_executions=recent_executions,
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
        (
            event
            for event in reversed(events)
            if (_text(event.get("event_type")) or _text(event.get("type"))) == "approval.required"
        ),
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
        duration_seconds=_float_or_none(raw.get("duration_seconds")),
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


def _live_activity(
    query_data: Mapping[str, Any],
    backlog: BacklogSummary,
    workflow: WorkflowSummary,
    blockers: tuple[str, ...],
    events: tuple[Mapping[str, Any], ...],
    *,
    project_id: str,
    now: datetime,
) -> tuple[AgentActivitySummary, ...]:
    task = query_data.get("current_task")
    if not isinstance(task, Mapping):
        return ()
    agent = query_data.get("agent_run")
    driver = query_data.get("driver")
    phase = workflow.stage or _text(task.get("state"))
    status = _activity_status(phase, agent, driver, workflow.blocker, blockers)
    started_at = (
        _text(agent.get("started_at")) if isinstance(agent, Mapping) else
        _text(driver.get("started_at")) if isinstance(driver, Mapping) else None
    )
    updated_at = (
        _text(agent.get("updated_at")) if isinstance(agent, Mapping) and _text(agent.get("updated_at")) else
        _text(driver.get("updated_at")) if isinstance(driver, Mapping) else workflow.updated_at
    )
    latest_event = _latest_event(events)
    latest_activity_at = _text(latest_event.get("occurred_at")) if latest_event else updated_at
    latest_activity = _event_label(latest_event) if latest_event else _phase_activity(phase, status)
    last_completed = _last_completed_action(query_data, events, phase, status)
    blocker = blockers[0] if blockers else _driver_stop_reason(driver)
    deadline_at = _text(agent.get("deadline_at")) if isinstance(agent, Mapping) else None
    safe_detail = _safe_activity_detail(agent, driver, workflow, latest_activity)
    return (
        AgentActivitySummary(
            project_id=project_id,
            task_id=_text(task.get("id")) or backlog.active_task_id,
            task_title=_text(task.get("title")) or backlog.active_task_title,
            agent_name=_text(agent.get("agent_name")) if isinstance(agent, Mapping) else workflow.owner_agent,
            agent_role=workflow.owner_agent or backlog.owner,
            workflow_id=_workflow_id(_text(task.get("id")) or backlog.active_task_id, workflow.feature_branch),
            run_id=_text(agent.get("run_id")) if isinstance(agent, Mapping) else None,
            branch=workflow.feature_branch,
            phase=phase,
            status=status,
            started_at=started_at,
            updated_at=updated_at,
            elapsed_seconds=_elapsed_seconds(started_at, updated_at, now, agent),
            latest_activity_at=latest_activity_at,
            latest_activity=latest_activity,
            last_completed_action=last_completed,
            blocker=_bounded_text(blocker),
            timeout_state=_timeout_state(agent, deadline_at, now),
            recovery_state=_recovery_state(driver, agent),
            safe_detail=safe_detail,
        ),
    )


def _recent_executions(
    query_data: Mapping[str, Any],
    workflow: WorkflowSummary,
    events: tuple[Mapping[str, Any], ...],
    *,
    project_id: str,
    now: datetime,
    limit: int = 10,
) -> tuple[RecentExecutionSummary, ...]:
    executions: list[RecentExecutionSummary] = []
    agent = query_data.get("agent_run")
    task = query_data.get("current_task")
    if isinstance(agent, Mapping):
        status = _text(agent.get("status"))
        if status in {"COMPLETE", "FAILED", "TIMED_OUT"}:
            started_at = _text(agent.get("started_at"))
            completed_at = _text(agent.get("completed_at")) or _text(agent.get("updated_at"))
            executions.append(
                RecentExecutionSummary(
                    project_id=project_id,
                    task_id=workflow.task_id or (_text(task.get("id")) if isinstance(task, Mapping) else None),
                    agent_name=_text(agent.get("agent_name")),
                    run_id=_text(agent.get("run_id")),
                    branch=workflow.feature_branch,
                    final_status=_status_to_activity(status),
                    started_at=started_at,
                    completed_at=completed_at,
                    elapsed_seconds=_elapsed_seconds(started_at, completed_at, now, agent),
                    last_completed_action=_last_completed_action(query_data, events, workflow.stage, _status_to_activity(status)),
                    result_summary=_execution_result_summary(agent),
                )
            )
    seen = {item.run_id for item in executions if item.run_id}
    for event in sorted(events, key=lambda item: str(item.get("occurred_at") or ""), reverse=True):
        event_type = _text(event.get("type")) or _text(event.get("event_type"))
        if event_type not in {"task.completed", "task.failed", "delegation.status"}:
            continue
        run_id = _text(event.get("run_id"))
        if run_id and run_id in seen:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        delegation_status = _text(payload.get("delegation_status")) if isinstance(payload, Mapping) else None
        if event_type == "delegation.status" and delegation_status not in {"COMPLETE", "FAILED", "TIMED_OUT"}:
            continue
        final_status = _status_to_activity(delegation_status or ("COMPLETE" if event_type == "task.completed" else "FAILED"))
        executions.append(
            RecentExecutionSummary(
                project_id=project_id,
                task_id=_text(event.get("task_id")),
                agent_name=_text(payload.get("agent_name")) if isinstance(payload, Mapping) else None,
                run_id=run_id,
                branch=_text(payload.get("feature_branch")) if isinstance(payload, Mapping) else None,
                final_status=final_status,
                started_at=None,
                completed_at=_text(event.get("occurred_at")),
                elapsed_seconds=None,
                last_completed_action=_event_label(event),
                result_summary=_bounded_text(_text(payload.get("failure_reason")) if isinstance(payload, Mapping) else None) or final_status,
            )
        )
        if len(executions) >= limit:
            break
    executions.sort(key=lambda item: item.completed_at or item.started_at or "", reverse=True)
    return tuple(executions[:limit])


def _activity_status(
    phase: str | None,
    agent: object,
    driver: object,
    workflow_blocker: str | None,
    blockers: tuple[str, ...],
) -> str:
    if isinstance(agent, Mapping):
        status = _text(agent.get("status"))
        if status in {"FAILED", "TIMED_OUT", "COMPLETE"}:
            return _status_to_activity(status)
        if status in {"ACTIVE", "RUNNING"}:
            return "running"
        if status in {"PENDING", "CLAIMED"}:
            return "delegated"
    if isinstance(driver, Mapping) and (driver.get("blocked") is True or driver.get("stale") is True):
        return "blocked"
    if workflow_blocker or blockers:
        return "blocked"
    return {
        "PLAN": "planning",
        "PREPARE_BRANCH": "preparing_branch",
        "DELEGATE": "delegated",
        "WAIT_FOR_AGENT": "waiting",
        "QA": "testing",
        "REVIEW": "reviewing",
        "REPORT": "reporting",
        "COMPLETE": "completed",
    }.get(phase, "idle")


def _status_to_activity(status: str | None) -> str:
    return {"COMPLETE": "completed", "FAILED": "failed", "TIMED_OUT": "timed_out"}.get(status or "", (status or "idle").lower())


def _latest_event(events: tuple[Mapping[str, Any], ...]) -> Mapping[str, Any] | None:
    if not events:
        return None
    return max(events, key=lambda item: str(item.get("occurred_at") or ""))


def _event_label(event: Mapping[str, Any] | None) -> str | None:
    if event is None:
        return None
    event_type = _text(event.get("type")) or _text(event.get("event_type")) or _text(event.get("message"))
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    state = _text(payload.get("state")) if isinstance(payload, Mapping) else None
    status = _text(payload.get("delegation_status")) if isinstance(payload, Mapping) else None
    if status:
        return f"{event_type}: {status}"
    if state:
        return f"{event_type}: {state}"
    return event_type


def _phase_activity(phase: str | None, status: str) -> str | None:
    if phase:
        return f"Workflow phase {phase} is {status}."
    return "No active workflow is recorded." if status == "idle" else status


def _last_completed_action(query_data: Mapping[str, Any], events: tuple[Mapping[str, Any], ...], phase: str | None, status: str) -> str | None:
    if query_data.get("report") is not None:
        return "Workflow report generated."
    if query_data.get("tests") is not None:
        return "QA completed."
    for event in sorted(events, key=lambda item: str(item.get("occurred_at") or ""), reverse=True):
        event_type = _text(event.get("type")) or _text(event.get("event_type"))
        if event_type in {"task.completed", "task.failed", "qa.result", "report.generated", "workflow.transition", "delegation.status"}:
            return _event_label(event)
    if status == "completed":
        return "Workflow completed."
    if phase:
        return f"Entered {phase}."
    return None


def _driver_stop_reason(driver: object) -> str | None:
    return _text(driver.get("last_stop_reason")) if isinstance(driver, Mapping) else None


def _timeout_state(agent: object, deadline_at: str | None, now: datetime) -> str:
    if isinstance(agent, Mapping) and _text(agent.get("status")) == "TIMED_OUT":
        return "timed_out"
    deadline = _parse_iso(deadline_at)
    if deadline is not None and now.astimezone(UTC) > deadline and (not isinstance(agent, Mapping) or _text(agent.get("completed_at")) is None):
        return "deadline_elapsed"
    return "none"


def _recovery_state(driver: object, agent: object) -> str:
    if isinstance(driver, Mapping):
        if driver.get("stale") is True:
            return "stale"
        if _text(driver.get("continuity")) == "RESUMED":
            return "resumed"
    if isinstance(agent, Mapping) and _text(agent.get("failure_reason")) == "Recorded worker is no longer running.":
        return "interrupted_worker"
    return "continuous"


def _safe_activity_detail(agent: object, driver: object, workflow: WorkflowSummary, latest_activity: str | None) -> str | None:
    for value in (
        _text(agent.get("failure_reason")) if isinstance(agent, Mapping) else None,
        _text(driver.get("last_stop_reason")) if isinstance(driver, Mapping) else None,
        workflow.blocker,
        latest_activity,
    ):
        bounded = _bounded_text(value)
        if bounded:
            return bounded
    return None


def _execution_result_summary(agent: Mapping[str, Any]) -> str | None:
    failure = _bounded_text(_text(agent.get("failure_reason")))
    if failure:
        return failure
    exit_code = _int_or_none(agent.get("exit_code"))
    status = _text(agent.get("status"))
    return f"{status} exit={exit_code}" if exit_code is not None else status


def _workflow_id(task_id: str | None, branch: str | None) -> str | None:
    return f"{task_id}:{branch}" if task_id and branch else None


def _elapsed_seconds(started_at: str | None, ended_at: str | None, now: datetime, agent: object) -> float | None:
    started = _parse_iso(started_at)
    if started is None:
        return None
    ended = _parse_iso(ended_at)
    if ended is None or (isinstance(agent, Mapping) and _text(agent.get("completed_at")) is None):
        ended = now.astimezone(UTC)
    return max(0.0, round((ended - started).total_seconds(), 3))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _bounded_text(value: str | None, limit: int = 2_000) -> str | None:
    if not value:
        return None
    return value[:limit]


def _current_tasks(
    query_data: Mapping[str, Any],
    backlog: BacklogSummary,
    workflow: WorkflowSummary,
    blockers: tuple[str, ...],
) -> tuple[TaskStatusSummary, ...]:
    task = query_data.get("current_task")
    agent = query_data.get("agent_run")
    if not isinstance(task, Mapping):
        return ()
    return (
        TaskStatusSummary(
            task_id=_text(task.get("id")) or backlog.active_task_id,
            title=_text(task.get("title")) or backlog.active_task_title,
            status=backlog.status or _text(task.get("status")),
            assigned_agent=(
                _text(agent.get("agent_name")) if isinstance(agent, Mapping) else workflow.owner_agent or backlog.owner
            ),
            current_phase=workflow.stage or _text(task.get("state")),
            priority=_text(task.get("priority")) or backlog.priority,
            started_at=_text(agent.get("started_at")) if isinstance(agent, Mapping) else None,
            last_updated=workflow.updated_at,
            blocking_reason=blockers[0] if blockers else None,
            completion_percent=_completion_percent(workflow.stage),
        ),
    )


def _blockers(
    query_data: Mapping[str, Any],
    workflow: WorkflowSummary,
    approval: ApprovalSummary,
    warnings: list[HealthWarning],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if workflow.blocker:
        blockers.append(workflow.blocker)
    for gap in _as_sequence(query_data.get("remaining_gaps")):
        text = _text(gap)
        if text and ("blocked" in text.lower() or "gap" in text.lower() or "missing" in text.lower()):
            blockers.append(text)
    if approval.pending:
        blockers.append(approval.next_action or approval.reason or "Approval pending.")
    blockers.extend(f"{warning.source}: {warning.message}" for warning in warnings if warning.severity == "WARNING")
    return tuple(dict.fromkeys(blockers))


def _testing_summary(latest_test: TestSummary | None, reports: tuple[ReportSummary, ...]) -> TestingSummary:
    warning_count = _warning_count(latest_test.summary) if latest_test else None
    full_suite = latest_test if latest_test and _looks_like_full_suite(latest_test) else None
    regression = latest_test if latest_test and not full_suite else None
    return TestingSummary(
        focused=None,
        regression=regression,
        full_suite=full_suite,
        latest_status=_test_status(latest_test),
        warning_count=warning_count,
    )


def _engineering_health(
    repository: RepositorySummary,
    latest_test: TestSummary | None,
    warnings: list[HealthWarning],
    blockers: tuple[str, ...],
) -> EngineeringHealthSummary:
    degraded_sources = tuple(sorted({warning.source for warning in warnings}))
    if any(warning.severity == "ERROR" for warning in warnings):
        status = "ERROR"
    elif blockers or warnings or repository.is_clean is False or (latest_test and latest_test.exit_code not in (None, 0)):
        status = "DEGRADED"
    else:
        status = "HEALTHY"
    return EngineeringHealthSummary(
        overall_status=status,
        repository_safe=repository.is_clean,
        current_branch=repository.branch,
        current_commit=repository.latest_commit,
        last_successful_regression_run=(latest_test.completed_at if latest_test and latest_test.exit_code == 0 else None),
        degraded_sources=degraded_sources,
        warning_count=len(warnings),
    )


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


def _parse_report_outcome(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4_000]
    except OSError:
        return None
    for marker in ("Overall acceptance result", "Decision:", "Result:", "Status:"):
        index = text.find(marker)
        if index >= 0:
            line = text[index:].splitlines()[0]
            return line.strip("# :-")[:100] or None
    if " PASS" in text or "PASS" in text:
        return "PASS"
    if " FAIL" in text or "FAIL" in text:
        return "FAIL"
    return None


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


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _completion_percent(stage: str | None) -> int:
    order = {
        None: 0,
        "DISCOVER": 5,
        "PLAN": 15,
        "PREPARE_BRANCH": 25,
        "DELEGATE": 35,
        "WAIT_FOR_AGENT": 50,
        "QA": 70,
        "REVIEW": 82,
        "REPORT": 92,
        "COMPLETE": 100,
    }
    return order.get(stage, 50)


def _test_status(test: TestSummary | None) -> str | None:
    if test is None:
        return None
    if test.timed_out:
        return "TIMED_OUT"
    if test.exit_code == 0:
        return "PASS"
    if test.exit_code is not None:
        return "FAIL"
    return None


def _warning_count(summary: str | None) -> int | None:
    if not summary:
        return None
    lower = summary.lower()
    marker = " warning"
    index = lower.find(marker)
    if index <= 0:
        return None
    prefix = lower[:index].split()[-1]
    try:
        return int(prefix)
    except ValueError:
        return None


def _looks_like_full_suite(test: TestSummary) -> bool:
    return test.command == ("pytest", "tests") or test.command == (".venv/bin/python", "-m", "pytest", "tests")


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
