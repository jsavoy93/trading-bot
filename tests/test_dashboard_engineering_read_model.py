from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

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
        )
    ).snapshot()

    assert snapshot.workflow.active is False
    assert snapshot.workflow.stage is None
    assert snapshot.backlog.active_task_id is None
    assert snapshot.latest_test_result is None


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


def test_missing_github_metadata_is_warning_not_failure():
    snapshot = build_model(pr_reader=EmptyPullRequestMetadataReader()).snapshot()

    assert snapshot.pull_request is not None
    assert snapshot.pull_request.available is False
    assert any(warning.source == "github" for warning in snapshot.health_warnings)


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
