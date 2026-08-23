from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dashboard_api.engineering_read_model import (
    DashboardSnapshot,
    EmptyPullRequestMetadataReader,
    EngineeringDashboardReadModel,
    PullRequestSummary,
    ReportIndex,
    RepositorySummary,
    StaticRepositorySummaryReader,
)


class FakeQueryService:
    def __init__(self, payload):
        self.payload = payload
        self.timeline_limit = None

    def snapshot(self, *, timeline_limit=100):
        self.timeline_limit = timeline_limit
        return self.payload


class FailingQueryService:
    def snapshot(self, *, timeline_limit=100):
        raise RuntimeError("store unavailable")


class FailingRepositoryReader:
    def summary(self):
        raise RuntimeError("git unavailable")


class FailingPrReader:
    def for_branch(self, head_branch):
        raise RuntimeError("github unavailable")


class StaticPrReader:
    def for_branch(self, head_branch):
        return PullRequestSummary(
            url="https://github.example/pull/7",
            number=7,
            state="OPEN",
            target_branch="main",
            head_branch=head_branch,
            mergeable=True,
            available=True,
        )


def fixed_clock():
    return datetime(2026, 8, 4, 23, 0, tzinfo=UTC)


def base_payload(**overrides):
    payload = {
        "current_task": {
            "id": "ENGDASH-001",
            "title": "Engineering dashboard read model",
            "priority": "P1",
            "owner": "dashboard-agent",
            "state": "QA",
            "feature_branch": "agent/engdash-001-dashboard-read-model",
        },
        "timeline": [
            {"event_type": "workflow.transition", "occurred_at": "2026-08-04T22:00:00+00:00", "task_id": "ENGDASH-001"},
            {"event_type": "approval.required", "occurred_at": "2026-08-04T22:01:00+00:00", "task_id": "ENGDASH-001", "payload": {"next_action": "Josh approval"}},
        ],
        "agent_run": {
            "agent_name": "dashboard-agent",
            "run_id": "run-1",
            "status": "COMPLETE",
            "updated_at": "2026-08-04T22:02:00+00:00",
            "exit_code": 0,
            "failure_reason": "",
        },
        "backlog": [
            {"id": "ENGDASH-001", "title": "Engineering dashboard read model", "status": "TODO", "priority": "P1", "owner": "dashboard-agent"},
            {"id": "OPS-014", "title": "Events", "status": "DONE", "priority": "P0", "owner": "trading-manager"},
            {"id": "OPS-015", "title": "Telegram", "status": "REVIEW", "priority": "P1", "owner": "trading-manager"},
        ],
        "tests": {
            "command": ["pytest", "tests/test_dashboard_engineering_read_model.py"],
            "exit_code": 0,
            "passed_count": 10,
            "failed_count": 0,
            "timed_out": False,
            "completed_at": "2026-08-04T22:03:00+00:00",
            "output_summary": "10 passed",
        },
        "report": None,
        "pr_links": [],
        "remaining_gaps": [],
        "recommended_next_step": "Ask Josh to merge.",
    }
    payload.update(overrides)
    return payload


def repository(is_clean=True, dirty_paths=()):
    return RepositorySummary(
        root="/repo",
        branch="agent/engdash-001-dashboard-read-model",
        is_clean=is_clean,
        dirty_paths=tuple(dirty_paths),
        sync_state="up_to_date",
        ahead_count=0,
        behind_count=0,
        latest_commit="abc123",
        latest_commit_subject="Add dashboard read model",
    )


def build_model(payload=None, repo=None, **kwargs):
    return EngineeringDashboardReadModel(
        query_service=FakeQueryService(payload or base_payload()),
        repository_reader=StaticRepositorySummaryReader(repo or repository()),
        clock=fixed_clock,
        **kwargs,
    )


def test_clean_repository_snapshot_contains_required_fields():
    snapshot = build_model(pr_reader=StaticPrReader()).snapshot()

    assert isinstance(snapshot, DashboardSnapshot)
    assert snapshot.project_identity == "trading-bot"
    assert snapshot.repository.root == "/repo"
    assert snapshot.repository.is_clean is True
    assert snapshot.repository.sync_state == "up_to_date"
    assert snapshot.backlog.active_task_id == "ENGDASH-001"
    assert snapshot.workflow.active is True
    assert snapshot.workflow.stage == "QA"
    assert snapshot.workflow.owner_agent == "dashboard-agent"
    assert snapshot.latest_execution_result == "agent COMPLETE exit=0"
    assert snapshot.latest_test_result is not None
    assert snapshot.latest_test_result.summary == "10 passed"
    assert snapshot.latest_commit == "abc123"
    assert snapshot.pull_request is not None
    assert snapshot.pull_request.available is True
    assert snapshot.data_freshness_timestamp == "2026-08-04T23:00:00+00:00"


def test_live_engineering_status_aggregates_current_activity_and_health():
    snapshot = build_model(
        base_payload(timeline=[{"event_type": "workflow.transition", "occurred_at": "2026-08-04T22:00:00+00:00", "task_id": "ENGDASH-001"}]),
        pr_reader=StaticPrReader(),
    ).snapshot()

    assert snapshot.engineering_health is not None
    assert snapshot.engineering_health.overall_status == "HEALTHY"
    assert snapshot.engineering_health.repository_safe is True
    assert snapshot.engineering_health.last_successful_regression_run == "2026-08-04T22:03:00+00:00"
    assert snapshot.current_tasks[0].task_id == "ENGDASH-001"
    assert snapshot.current_tasks[0].assigned_agent == "dashboard-agent"
    assert snapshot.current_tasks[0].completion_percent == 70
    assert snapshot.blockers == ()
    assert snapshot.testing is not None
    assert snapshot.testing.latest_status == "PASS"


def test_live_engineering_status_exposes_blockers_and_degraded_sources():
    snapshot = build_model(
        base_payload(
            agent_run={
                "agent_name": "dashboard-agent",
                "run_id": "run-1",
                "status": "FAILED",
                "updated_at": "2026-08-04T22:02:00+00:00",
                "exit_code": 1,
                "failure_reason": "required test failed",
            },
            tests={
                "command": ["pytest", "tests"],
                "exit_code": 1,
                "passed_count": 9,
                "failed_count": 1,
                "timed_out": False,
                "completed_at": "2026-08-04T22:03:00+00:00",
                "output_summary": "9 passed, 1 failed, 3 warnings",
            },
        ),
        repo=repository(False, ("changed.py",)),
    ).snapshot()

    assert snapshot.engineering_health.overall_status == "DEGRADED"
    assert snapshot.testing.latest_status == "FAIL"
    assert snapshot.testing.warning_count == 3
    assert snapshot.testing.full_suite is not None
    assert "required test failed" in snapshot.blockers
    assert any(blocker.startswith("repository:") for blocker in snapshot.blockers)


def test_dirty_repository_snapshot_adds_health_warning():
    snapshot = build_model(repo=repository(False, ("changed.py", "new.txt"))).snapshot()

    assert snapshot.repository.is_clean is False
    assert snapshot.repository.dirty_paths == ("changed.py", "new.txt")
    assert any(warning.source == "repository" for warning in snapshot.health_warnings)


def test_no_active_workflow_degrades_cleanly():
    snapshot = build_model(
        base_payload(
            current_task=None,
            agent_run=None,
            tests=None,
            remaining_gaps=["No active workflow is recorded."],
            timeline=[],
        )
    ).snapshot()

    assert snapshot.workflow.active is False
    assert snapshot.workflow.stage is None
    assert snapshot.workflow.blocker is None
    assert snapshot.backlog.active_task_id is None
    assert snapshot.latest_test_result is None
    assert snapshot.engineering_health.overall_status == "HEALTHY"


def test_active_workflow_with_blocker_is_exposed():
    snapshot = build_model(
        base_payload(
            agent_run={
                "agent_name": "dashboard-agent",
                "run_id": "run-1",
                "status": "FAILED",
                "updated_at": "2026-08-04T22:02:00+00:00",
                "exit_code": 1,
                "failure_reason": "required test failed",
            }
        )
    ).snapshot()

    assert snapshot.workflow.blocker == "required test failed"
    assert snapshot.latest_execution_result == "agent FAILED exit=1"


def test_pending_human_approval_from_report():
    snapshot = build_model(
        base_payload(
            report={
                "task_id": "ENGDASH-001",
                "recommendation": "ACCEPT",
                "next_action": "Josh approval required.",
                "generated_at": "2026-08-04T22:05:00+00:00",
            }
        )
    ).snapshot()

    assert snapshot.approval.pending is True
    assert snapshot.approval.reason == "Workflow report recommends acceptance."
    assert snapshot.approval.next_action == "Josh approval required."


def test_backlog_aggregation_counts_status_and_priority():
    snapshot = build_model().snapshot()

    assert snapshot.backlog.counts_by_status == {"TODO": 1, "DONE": 1, "REVIEW": 1}
    assert snapshot.backlog.counts_by_priority == {"P1": 2, "P0": 1}


def test_limited_events_and_reports(tmp_path: Path):
    reports = tmp_path / "reports"
    reports.mkdir()
    for index in range(15):
        (reports / f"2026-08-04_2300{index:02d}_TASK-{index}.md").write_text(
            f"# Report {index}\n\nTask: TASK-{index}\n", encoding="utf-8"
        )
    payload = base_payload(timeline=[{"event_type": "workflow.transition", "index": index} for index in range(75)])
    query = FakeQueryService(payload)
    model = EngineeringDashboardReadModel(
        query_service=query,
        repository_reader=StaticRepositorySummaryReader(repository()),
        report_index=ReportIndex(repo_root=tmp_path, max_reports=5),
        clock=fixed_clock,
        event_limit=7,
    )

    snapshot = model.snapshot()

    assert query.timeline_limit == 7
    assert len(snapshot.recent_events) == 7
    assert len(snapshot.recent_reports) == 5


def test_missing_github_metadata_is_info_and_does_not_degrade_health():
    snapshot = build_model(base_payload(timeline=[]), pr_reader=EmptyPullRequestMetadataReader()).snapshot()

    assert snapshot.pull_request is not None
    assert snapshot.pull_request.available is False
    assert snapshot.engineering_health.overall_status == "HEALTHY"
    assert snapshot.engineering_health.degraded_sources == ()
    assert any(
        warning.source == "github" and warning.severity == "INFO"
        for warning in snapshot.health_warnings
    )


def test_no_warnings_produces_healthy_status():
    snapshot = build_model(base_payload(timeline=[]), pr_reader=StaticPrReader()).snapshot()

    assert snapshot.health_warnings == ()
    assert snapshot.engineering_health.overall_status == "HEALTHY"
    assert snapshot.engineering_health.degraded_sources == ()


def test_warning_severity_degrades_health():
    snapshot = build_model(repo=repository(False, ("changed.py",))).snapshot()

    assert any(
        warning.source == "repository" and warning.severity == "WARNING"
        for warning in snapshot.health_warnings
    )
    assert snapshot.engineering_health.overall_status == "DEGRADED"
    assert "repository" in snapshot.engineering_health.degraded_sources


def test_repository_unsafe_still_degrades_health():
    snapshot = build_model(repo=repository(False, ("changed.py",)), pr_reader=StaticPrReader()).snapshot()

    assert snapshot.repository.is_clean is False
    assert snapshot.engineering_health.repository_safe is False
    assert snapshot.engineering_health.overall_status == "DEGRADED"


def test_query_service_failure_still_degrades_health():
    model = EngineeringDashboardReadModel(
        query_service=FailingQueryService(),
        repository_reader=StaticRepositorySummaryReader(repository()),
        pr_reader=StaticPrReader(),
        clock=fixed_clock,
    )

    snapshot = model.snapshot()

    assert any(
        warning.source == "query_service" and warning.severity == "WARNING"
        for warning in snapshot.health_warnings
    )
    assert snapshot.engineering_health.overall_status == "DEGRADED"
    assert "query_service" in snapshot.engineering_health.degraded_sources


def test_partial_source_failure_converts_to_warning():
    model = EngineeringDashboardReadModel(
        query_service=FailingQueryService(),
        repository_reader=FailingRepositoryReader(),
        pr_reader=FailingPrReader(),
        clock=fixed_clock,
    )

    snapshot = model.snapshot()

    assert snapshot.repository.is_clean is None
    assert snapshot.backlog.counts_by_status == {}
    assert {warning.source for warning in snapshot.health_warnings} >= {"query_service", "repository", "github"}


def test_no_trading_imports_in_read_model_source():
    source = Path("dashboard_api/engineering_read_model.py").read_text(encoding="utf-8")

    forbidden = ("alpaca", "brokerage", "trading_bot.db", "from src", "import src", "import dashboard.py")
    assert all(token not in source for token in forbidden)


def test_dashboard_api_package_imports_normally_and_old_path_is_removed():
    import dashboard_api

    assert dashboard_api.DashboardSnapshot is DashboardSnapshot
    assert not Path("dashboard-api").exists()


def test_deterministic_typed_output():
    first = build_model().snapshot().to_dict()
    second = build_model().snapshot().to_dict()

    assert first == second
    assert asdict(build_model().snapshot())["project_identity"] == "trading-bot"


def test_active_delegated_run_renders_live_activity_without_raw_outputs():
    snapshot = build_model(
        base_payload(
            current_task={
                "id": "ENGDASH-006",
                "title": "Live Agent Activity",
                "priority": "P1",
                "owner": "dashboard-agent",
                "state": "WAIT_FOR_AGENT",
                "feature_branch": "agent/engdash-006",
            },
            agent_run={
                "agent_name": "dashboard-agent",
                "run_id": "run-006",
                "request_id": "delegation-006",
                "status": "ACTIVE",
                "started_at": "2026-08-04T22:00:00+00:00",
                "updated_at": "2026-08-04T22:10:00+00:00",
                "deadline_at": "2026-08-04T23:30:00+00:00",
                "exit_code": None,
                "failure_reason": "",
                "stdout_path": "/secret/stdout.log",
                "stderr_path": "/secret/stderr.log",
                "prompt": "private prompt",
            },
        )
    ).snapshot()

    activity = snapshot.live_activity[0]
    assert activity.project_id == "trading-bot"
    assert activity.task_id == "ENGDASH-006"
    assert activity.agent_name == "dashboard-agent"
    assert activity.workflow_id == "ENGDASH-006:agent/engdash-006"
    assert activity.run_id == "run-006"
    assert activity.phase == "WAIT_FOR_AGENT"
    assert activity.status == "running"
    assert activity.timeout_state == "none"
    assert activity.recovery_state == "continuous"
    payload = snapshot.to_dict()
    assert "stdout_path" not in str(payload)
    assert "stderr_path" not in str(payload)
    assert "private prompt" not in str(payload)


def test_idle_state_produces_safe_empty_activity():
    snapshot = build_model(base_payload(current_task=None, agent_run=None, tests=None)).snapshot()

    assert snapshot.live_activity == ()
    assert snapshot.recent_executions == ()


@pytest.mark.parametrize(
    ("phase", "expected"),
    (
        ("QA", "testing"),
        ("REVIEW", "reviewing"),
        ("REPORT", "reporting"),
    ),
)
def test_workflow_phase_maps_to_activity_status(phase: str, expected: str):
    snapshot = build_model(
        base_payload(
            current_task={
                "id": "ENGDASH-006",
                "title": "Live Agent Activity",
                "priority": "P1",
                "owner": "dashboard-agent",
                "state": phase,
                "feature_branch": "agent/engdash-006",
            },
            agent_run=None,
            remaining_gaps=[],
            timeline=[{"event_type": "workflow.transition", "occurred_at": "2026-08-04T22:00:00+00:00", "task_id": "ENGDASH-006"}],
        )
    ).snapshot()

    assert snapshot.live_activity[0].status == expected


def test_failed_delegation_shows_bounded_blocker_and_recent_execution():
    reason = "x" * 2500
    snapshot = build_model(
        base_payload(
            agent_run={
                "agent_name": "dashboard-agent",
                "run_id": "run-failed",
                "status": "FAILED",
                "started_at": "2026-08-04T22:00:00+00:00",
                "updated_at": "2026-08-04T22:02:00+00:00",
                "completed_at": "2026-08-04T22:02:00+00:00",
                "deadline_at": "2026-08-04T23:30:00+00:00",
                "exit_code": 1,
                "failure_reason": reason,
            }
        )
    ).snapshot()

    assert snapshot.live_activity[0].status == "failed"
    assert snapshot.live_activity[0].blocker == reason[:2000]
    assert snapshot.recent_executions[0].final_status == "failed"
    assert snapshot.recent_executions[0].result_summary == reason[:2000]


def test_timed_out_delegation_shows_timeout_state():
    snapshot = build_model(
        base_payload(
            agent_run={
                "agent_name": "dashboard-agent",
                "run_id": "run-timeout",
                "status": "TIMED_OUT",
                "started_at": "2026-08-04T21:00:00+00:00",
                "updated_at": "2026-08-04T22:00:00+00:00",
                "completed_at": "2026-08-04T22:00:00+00:00",
                "deadline_at": "2026-08-04T22:00:00+00:00",
                "exit_code": 124,
                "failure_reason": "deadline elapsed",
            }
        )
    ).snapshot()

    assert snapshot.live_activity[0].status == "timed_out"
    assert snapshot.live_activity[0].timeout_state == "timed_out"
    assert snapshot.recent_executions[0].final_status == "timed_out"


def test_stale_and_resumed_driver_state_appears():
    snapshot = build_model(
        base_payload(
            agent_run=None,
            driver={
                "started_at": "2026-08-01T00:00:00+00:00",
                "updated_at": "2026-08-04T22:00:00+00:00",
                "accumulated_elapsed_seconds": 300.0,
                "total_steps": 3,
                "wait_polls": 2,
                "continuity": "RESUMED",
                "last_stop_reason": "Workflow is stale after more than 48 hours; Josh review required.",
                "blocked": True,
                "stale": True,
                "resume_explanation": "Explicit drive invocation resumed after stop.",
            },
        )
    ).snapshot()

    activity = snapshot.live_activity[0]
    assert activity.status == "blocked"
    assert activity.recovery_state == "stale"
    assert "stale" in (activity.safe_detail or "")


def test_recent_executions_are_bounded_and_sorted_newest_first():
    events = tuple(
        {
            "type": "task.completed",
            "occurred_at": f"2026-08-04T22:{index:02d}:00+00:00",
            "task_id": f"TASK-{index}",
            "payload": {"feature_branch": f"agent/task-{index}"},
        }
        for index in range(15)
    )
    snapshot = build_model(base_payload(agent_run=None, timeline=events)).snapshot()

    assert len(snapshot.recent_executions) == 10
    assert snapshot.recent_executions[0].task_id == "TASK-14"
    assert snapshot.recent_executions[-1].task_id == "TASK-5"


def test_completed_execution_appears_in_recent_executions():
    snapshot = build_model(
        base_payload(
            agent_run={
                "agent_name": "dashboard-agent",
                "run_id": "run-complete",
                "status": "COMPLETE",
                "started_at": "2026-08-04T22:00:00+00:00",
                "updated_at": "2026-08-04T22:05:00+00:00",
                "completed_at": "2026-08-04T22:05:00+00:00",
                "deadline_at": "2026-08-04T23:30:00+00:00",
                "exit_code": 0,
                "failure_reason": "",
            }
        )
    ).snapshot()

    assert snapshot.live_activity[0].status == "completed"
    assert snapshot.recent_executions[0].final_status == "completed"
    assert snapshot.recent_executions[0].elapsed_seconds == 300.0
