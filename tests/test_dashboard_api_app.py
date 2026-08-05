from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from dashboard_api.app import DASHBOARD_ROUTE, SNAPSHOT_ROUTE, create_app, render_dashboard
from dashboard_api.engineering_read_model import (
    ApprovalSummary,
    BacklogSummary,
    DashboardSnapshot,
    EngineeringHealthSummary,
    HealthWarning,
    PullRequestSummary,
    ReportSummary,
    RepositorySummary,
    TaskStatusSummary,
    TestingSummary as DashboardTestingSummary,
    TestSummary as DashboardTestSummary,
    WorkflowSummary,
)


class StaticProvider:
    def __init__(self, snapshot: DashboardSnapshot):
        self.snapshot_value = snapshot
        self.calls = 0

    def snapshot(self) -> DashboardSnapshot:
        self.calls += 1
        return self.snapshot_value


def populated_snapshot(**overrides) -> DashboardSnapshot:
    snapshot = DashboardSnapshot(
        project_identity="trading-bot",
        repository=RepositorySummary(
            root="/repo",
            branch="agent/engdash-002-read-only-api-ui",
            is_clean=True,
            dirty_paths=(),
            sync_state="up_to_date",
            ahead_count=0,
            behind_count=0,
            latest_commit="abc123",
            latest_commit_subject="Add read-only API UI",
        ),
        backlog=BacklogSummary(
            active_task_id="ENGDASH-002",
            active_task_title="Read-only engineering dashboard API/UI",
            status="IN_PROGRESS",
            owner="dashboard-agent",
            priority="P1",
            counts_by_status={"DONE": 1, "IN_PROGRESS": 1, "TODO": 2},
            counts_by_priority={"P1": 2, "P2": 1},
        ),
        workflow=WorkflowSummary(
            active=True,
            task_id="ENGDASH-002",
            feature_branch="agent/engdash-002-read-only-api-ui",
            stage="QA",
            owner_agent="dashboard-agent",
            blocker=None,
            execution_status="COMPLETE",
            updated_at="2026-08-04T23:20:00+00:00",
        ),
        approval=ApprovalSummary(
            pending=False,
            reason=None,
            task_id="ENGDASH-002",
            requested_at=None,
            next_action="Open PR",
        ),
        latest_execution_result="agent COMPLETE exit=0",
        latest_test_result=DashboardTestSummary(
            command=("pytest", "tests/test_dashboard_api_app.py"),
            exit_code=0,
            passed_count=20,
            failed_count=0,
            timed_out=False,
            completed_at="2026-08-04T23:21:00+00:00",
            summary="20 passed",
        ),
        latest_commit="abc123",
        pull_request=PullRequestSummary(
            url="https://github.example/pull/11",
            number=11,
            state="OPEN",
            target_branch="main",
            head_branch="agent/engdash-002-read-only-api-ui",
            mergeable=True,
            available=True,
        ),
        recent_events=tuple(
            {"event_type": "workflow.transition", "task_id": f"TASK-{index}", "occurred_at": f"2026-08-04T23:{index:02d}:00+00:00"}
            for index in range(4)
        ),
        recent_reports=tuple(
            ReportSummary(
                path=f"reports/2026-08-04_23{index:02d}_TASK-{index}.md",
                kind="repo_archive",
                task_id=f"TASK-{index}",
                generated_at=f"2026-08-04T23:{index:02d}:00+00:00",
                title=f"Report {index}",
            )
            for index in range(3)
        ),
        health_warnings=(),
        data_freshness_timestamp="2026-08-04T23:22:00+00:00",
        engineering_health=EngineeringHealthSummary(
            overall_status="HEALTHY",
            repository_safe=True,
            current_branch="agent/engdash-002-read-only-api-ui",
            current_commit="abc123",
            last_successful_regression_run="2026-08-04T23:21:00+00:00",
            degraded_sources=(),
            warning_count=0,
        ),
        current_tasks=(
            TaskStatusSummary(
                task_id="ENGDASH-002",
                title="Read-only engineering dashboard API/UI",
                status="IN_PROGRESS",
                assigned_agent="dashboard-agent",
                current_phase="QA",
                priority="P1",
                started_at="2026-08-04T23:00:00+00:00",
                last_updated="2026-08-04T23:20:00+00:00",
                blocking_reason=None,
                completion_percent=70,
            ),
        ),
        blockers=(),
        testing=DashboardTestingSummary(
            focused=None,
            regression=DashboardTestSummary(
                command=("pytest", "tests/test_dashboard_api_app.py"),
                exit_code=0,
                passed_count=20,
                failed_count=0,
                timed_out=False,
                completed_at="2026-08-04T23:21:00+00:00",
                summary="20 passed",
            ),
            full_suite=None,
            latest_status="PASS",
            warning_count=0,
        ),
    )
    return DashboardSnapshot(**{**snapshot.__dict__, **overrides})


def empty_snapshot() -> DashboardSnapshot:
    return populated_snapshot(
        repository=RepositorySummary("unavailable", None, None, sync_state="unavailable"),
        backlog=BacklogSummary(None, None, None, None, None, {}, {}),
        workflow=WorkflowSummary(False, None, None, None, None, None, None, None),
        approval=ApprovalSummary(False, None, None, None, None),
        latest_execution_result=None,
        latest_test_result=None,
        latest_commit=None,
        pull_request=PullRequestSummary(None, None, None, None, None, None, available=False),
        recent_events=(),
        recent_reports=(),
        health_warnings=(HealthWarning("query_service", "WARNING", "Unavailable", "RuntimeError: secret-token-123"),),
    )


def test_snapshot_endpoint_returns_stable_typed_json_shape():
    provider = StaticProvider(populated_snapshot())
    response = TestClient(create_app(provider)).get(SNAPSHOT_ROUTE)

    assert response.status_code == 200
    body = response.json()
    assert list(body) == [
        "project_identity",
        "repository",
        "backlog",
        "workflow",
        "approval",
        "latest_execution_result",
        "latest_test_result",
        "latest_commit",
        "pull_request",
        "recent_events",
        "recent_reports",
        "health_warnings",
        "data_freshness_timestamp",
        "engineering_health",
        "current_tasks",
        "blockers",
        "testing",
    ]
    assert body["project_identity"] == "trading-bot"
    assert body["repository"]["branch"] == "agent/engdash-002-read-only-api-ui"
    assert body["latest_test_result"]["command"] == ["pytest", "tests/test_dashboard_api_app.py"]
    assert body["engineering_health"]["overall_status"] == "HEALTHY"
    assert body["current_tasks"][0]["assigned_agent"] == "dashboard-agent"
    assert body["testing"]["latest_status"] == "PASS"
    assert provider.calls == 1


def test_degraded_snapshot_response_sanitizes_warning_details():
    response = TestClient(create_app(StaticProvider(empty_snapshot()))).get(SNAPSHOT_ROUTE)

    assert response.status_code == 200
    body = response.json()
    assert body["health_warnings"][0]["message"] == "Unavailable"
    assert body["health_warnings"][0]["detail"] is None
    assert "secret-token-123" not in response.text
    assert "Traceback" not in response.text


def test_missing_pr_metadata_is_rendered_as_unavailable():
    response = TestClient(create_app(StaticProvider(empty_snapshot()))).get(DASHBOARD_ROUTE)

    assert response.status_code == 200
    assert "Pull request" in response.text
    assert "Available" in response.text
    assert "No" in response.text


def test_html_rendering_with_populated_snapshot_escapes_values():
    snapshot = populated_snapshot(
        backlog=BacklogSummary(
            active_task_id="ENGDASH-002<script>",
            active_task_title="Read-only <dashboard>",
            status="IN_PROGRESS",
            owner="dashboard-agent",
            priority="P1",
            counts_by_status={"TODO": 1},
            counts_by_priority={"P1": 1},
        )
    )

    html = render_dashboard(snapshot)

    assert "Engineering Dashboard" in html
    assert "Engineering health" in html
    assert "Current agent activity" in html
    assert "Blockers and approvals needed" in html
    assert "Testing status" in html
    assert "ENGDASH-002&lt;script&gt;" in html
    assert "Read-only &lt;dashboard&gt;" in html
    assert "<script>" not in html


def test_html_rendering_with_empty_snapshot_has_degradation_warning_without_detail():
    response = TestClient(create_app(StaticProvider(empty_snapshot()))).get(DASHBOARD_ROUTE)

    assert response.status_code == 200
    assert "Degradation warnings" in response.text
    assert "query_service" in response.text
    assert "secret-token-123" not in response.text
    assert "Traceback" not in response.text


def test_no_mutation_http_methods_or_routes():
    app = create_app(StaticProvider(populated_snapshot()))
    routes = {route.path: route.methods for route in app.routes if hasattr(route, "methods")}

    assert routes == {SNAPSHOT_ROUTE: {"GET"}, DASHBOARD_ROUTE: {"GET"}}
    client = TestClient(app)
    for method in (client.post, client.put, client.patch, client.delete):
        assert method(SNAPSHOT_ROUTE).status_code == 405
        assert method(DASHBOARD_ROUTE).status_code == 405


def test_bounded_lists_and_deterministic_ordering_in_response():
    snapshot = populated_snapshot(
        recent_events=tuple({"event_type": "e", "task_id": str(index)} for index in range(6)),
        recent_reports=tuple(
            ReportSummary(str(index), "repo_archive", str(index), f"2026-08-04T23:{index:02d}:00+00:00", str(index))
            for index in range(5)
        ),
        backlog=BacklogSummary("A", "B", "TODO", "owner", "P1", {"Z": 1, "A": 2}, {"P2": 1, "P1": 3}),
    )

    first = TestClient(create_app(StaticProvider(snapshot))).get(SNAPSHOT_ROUTE).json()
    second = TestClient(create_app(StaticProvider(snapshot))).get(SNAPSHOT_ROUTE).json()

    assert first == second
    assert [event["task_id"] for event in first["recent_events"]] == [str(index) for index in range(6)]
    assert [report["path"] for report in first["recent_reports"]] == [str(index) for index in range(5)]


def test_independent_default_app_startup_uses_real_read_only_provider():
    client = TestClient(create_app())
    response = client.get(SNAPSHOT_ROUTE)

    assert response.status_code == 200
    body = response.json()
    assert body["repository"]["root"] != "unavailable"
    assert "No engineering query source is configured." not in response.text
    assert client.get("/openapi.json").status_code == 404


def test_no_trading_or_brokerage_imports_in_dashboard_api_sources():
    forbidden_roots = {"alpaca", "brokerage", "src", "dashboard"}
    for path in Path("dashboard_api").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".", 1)[0] for alias in node.names}
                assert imported.isdisjoint(forbidden_roots)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_roots


def test_no_import_filename_or_route_collision_with_legacy_dashboard_py():
    import dashboard
    import dashboard_api

    assert Path(dashboard.__file__).name == "dashboard.py"
    assert Path(dashboard_api.__file__).parent.name == "dashboard_api"
    assert not Path("dashboard-api").exists()
    app = create_app(StaticProvider(populated_snapshot()))
    assert {SNAPSHOT_ROUTE, DASHBOARD_ROUTE}.issubset({route.path for route in app.routes})
    assert {route.path for route in app.routes} == {SNAPSHOT_ROUTE, DASHBOARD_ROUTE}


def test_launch_command_documented_and_no_route_exposes_controls():
    docs = Path("docs/ENGDASH-002.md").read_text(encoding="utf-8")
    app = create_app(StaticProvider(populated_snapshot()))
    route_text = "\n".join(f"{route.path}:{sorted(route.methods)}" for route in app.routes if hasattr(route, "methods"))

    assert "python -m dashboard_api.app --host 127.0.0.1 --port 8010" in docs
    forbidden_controls = ("pause", "resume", "retry", "approve", "merge", "execute")
    assert all(control not in route_text.lower() for control in forbidden_controls)
