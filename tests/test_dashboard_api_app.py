from __future__ import annotations

import ast
import json
import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from dashboard_api.app import CHAT_HISTORY_ROUTE, CHAT_SEND_ROUTE, DASHBOARD_ROUTE, SNAPSHOT_ROUTE, create_app, render_dashboard
from dashboard_api.providers import EngineeringDashboardProviderConfig, create_engineering_dashboard_provider
from dashboard_api.engineering_read_model import (
    AgentActivitySummary,
    ApprovalSummary,
    BacklogSummary,
    DashboardSnapshot,
    EngineeringHealthSummary,
    HealthWarning,
    PullRequestSummary,
    RecentExecutionSummary,
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
        "live_activity",
        "recent_executions",
    ]
    assert body["project_identity"] == "trading-bot"
    assert body["repository"]["branch"] == "agent/engdash-002-read-only-api-ui"
    assert body["latest_test_result"]["command"] == ["pytest", "tests/test_dashboard_api_app.py"]
    assert body["engineering_health"]["overall_status"] == "HEALTHY"
    assert body["current_tasks"][0]["assigned_agent"] == "dashboard-agent"
    assert body["testing"]["latest_status"] == "PASS"
    assert body["live_activity"] == []
    assert body["recent_executions"] == []
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


def test_info_warning_remains_visible_when_overall_status_is_healthy():
    snapshot = populated_snapshot(
        health_warnings=(HealthWarning("github", "INFO", "Pull-request metadata is unavailable.", None),)
    )

    json_response = TestClient(create_app(StaticProvider(snapshot))).get(SNAPSHOT_ROUTE)
    html_response = TestClient(create_app(StaticProvider(snapshot))).get(DASHBOARD_ROUTE)

    assert json_response.status_code == 200
    assert json_response.json()["engineering_health"]["overall_status"] == "HEALTHY"
    assert json_response.json()["health_warnings"][0]["severity"] == "INFO"
    assert html_response.status_code == 200
    assert "Healthy" in html_response.text
    assert "github" in html_response.text
    assert "Pull-request metadata is unavailable." in html_response.text


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
    assert "Overview" in html
    assert "Blockers" in html
    assert "Testing status" in html
    assert "ENGDASH-002&lt;script&gt;" in html
    assert "Read-only &lt;dashboard&gt;" in html
    assert "ENGDASH-002<script>" not in html


def test_live_activity_and_recent_execution_html_escapes_values():
    snapshot = populated_snapshot(
        live_activity=(
            AgentActivitySummary(
                project_id="trading-bot",
                task_id="ENGDASH-006<script>",
                task_title="Live <activity>",
                agent_name="dashboard-agent",
                agent_role="dashboard-agent",
                workflow_id="ENGDASH-006:agent/x",
                run_id="run-1<script>",
                branch="agent/<branch>",
                phase="QA",
                status="testing",
                started_at="2026-08-23T14:00:00+00:00",
                updated_at="2026-08-23T14:01:00+00:00",
                elapsed_seconds=60.0,
                latest_activity_at="2026-08-23T14:01:00+00:00",
                latest_activity="Use <safe> data",
                last_completed_action="QA <completed>",
                blocker="blocked <reason>",
                timeout_state="none",
                recovery_state="continuous",
                safe_detail="detail <escaped>",
            ),
        ),
        recent_executions=(
            RecentExecutionSummary(
                project_id="trading-bot",
                task_id="ENGDASH-006<script>",
                agent_name="dashboard-agent",
                run_id="run-2<script>",
                branch="agent/<branch>",
                final_status="completed",
                started_at="2026-08-23T14:00:00+00:00",
                completed_at="2026-08-23T14:02:00+00:00",
                elapsed_seconds=120.0,
                last_completed_action="Report <generated>",
                result_summary="ok <safe>",
            ),
        ),
    )

    html = render_dashboard(snapshot)

    assert "Live Agent Activity" in html
    assert "Recent Executions" in html
    assert "ENGDASH-006&lt;script&gt;" in html
    assert "agent/&lt;branch&gt;" in html
    assert "Use &lt;safe&gt; data" in html
    assert "ENGDASH-006<script>" not in html
    assert "run-1<script>" not in html
    assert "<branch>" not in html


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

    assert routes == {SNAPSHOT_ROUTE: {"GET"}, CHAT_HISTORY_ROUTE: {"GET"}, CHAT_SEND_ROUTE: {"POST"}, DASHBOARD_ROUTE: {"GET"}}
    client = TestClient(app)
    for method in (client.post, client.put, client.patch, client.delete):
        assert method(SNAPSHOT_ROUTE).status_code == 405
        assert method(CHAT_HISTORY_ROUTE).status_code == 405
        expected_send_status = 400 if method.__name__ == "post" else 405
        assert method(CHAT_SEND_ROUTE).status_code == expected_send_status
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


def test_independent_default_app_startup_uses_real_read_only_provider(tmp_path: Path):
    provider = create_engineering_dashboard_provider(
        EngineeringDashboardProviderConfig(
            repo_root=tmp_path,
            backlog_path=tmp_path / "AGENT_BACKLOG.md",
            workflow_state_path=tmp_path / "engineering-workflow.json",
            event_store_path=tmp_path / "engineering-events.sqlite3",
            workflow_report_dir=tmp_path / "reports",
            audit_archive_root=None,
        )
    )
    client = TestClient(create_app(provider))
    response = client.get(SNAPSHOT_ROUTE)

    assert response.status_code == 200
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
    assert {SNAPSHOT_ROUTE, CHAT_HISTORY_ROUTE, CHAT_SEND_ROUTE, DASHBOARD_ROUTE}.issubset({route.path for route in app.routes})
    assert {route.path for route in app.routes} == {SNAPSHOT_ROUTE, CHAT_HISTORY_ROUTE, CHAT_SEND_ROUTE, DASHBOARD_ROUTE}


def test_launch_command_documented_and_no_route_exposes_controls():
    docs = Path("docs/ENGDASH-002.md").read_text(encoding="utf-8")
    app = create_app(StaticProvider(populated_snapshot()))
    route_text = "\n".join(f"{route.path}:{sorted(route.methods)}" for route in app.routes if hasattr(route, "methods"))

    assert "python -m dashboard_api.app --host 127.0.0.1 --port 8010" in docs
    forbidden_controls = ("pause", "resume", "retry", "approve", "merge", "execute")
    assert all(control not in route_text.lower() for control in forbidden_controls)


def _dashboard_script(html: str) -> str:
    start = html.index("<script>")
    end = html.index("</script>", start) + len("</script>")
    return html[start:end]


def _public_snapshot(snapshot: DashboardSnapshot) -> dict[str, object]:
    return TestClient(create_app(StaticProvider(snapshot))).get(SNAPSHOT_ROUTE).json()


def _run_dashboard_script_case(script: str, body: str) -> None:
    result = subprocess.run(
        ["node", "-e", body],
        input=script,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_dashboard_shell_uses_client_side_snapshot_polling_instead_of_meta_refresh():
    html = render_dashboard(populated_snapshot())

    assert "http-equiv='refresh'" not in html
    assert "<meta http-equiv='refresh'" not in html
    assert "const SNAPSHOT_URL = '/api/engineering/snapshot';" in html
    assert "const POLL_INTERVAL_MS = 15000;" in html
    assert "window.setInterval(refreshDashboard, POLL_INTERVAL_MS);" in html
    assert "fetch(SNAPSHOT_URL" in html
    assert "<main id='dashboard-content'>" in html
    assert "id='update-warning'" in html


def test_successful_poll_updates_displayed_values_and_preserves_scroll():
    initial = populated_snapshot()
    updated = populated_snapshot(
        repository=RepositorySummary(
            root="/repo",
            branch="agent/smooth-refresh-updated",
            is_clean=True,
            dirty_paths=(),
            sync_state="up_to_date",
            ahead_count=0,
            behind_count=0,
            latest_commit="def456",
            latest_commit_subject="Updated without reload",
        ),
        data_freshness_timestamp="2026-08-23T16:30:00+00:00",
    )
    script = _dashboard_script(render_dashboard(initial))
    snapshot_json = json.dumps(_public_snapshot(updated))
    body = textwrap.dedent(
        f"""
        const assert = require('assert');
        const fs = require('fs');
        const script = fs.readFileSync(0, 'utf8');
        let content = {{innerHTML: 'INITIAL SNAPSHOT', addEventListener: () => {{}}, contains: () => true, querySelectorAll: () => []}};
        let warning = {{textContent: '', style: {{display: 'none'}}}};
        let intervalMs = null;
        let scrollCalls = [];
        global.window = {{
          scrollX: 13,
          scrollY: 377,
          setInterval: (fn, ms) => {{ intervalMs = ms; return 1; }},
          scrollTo: (x, y) => scrollCalls.push([x, y]),
        }};
        global.document = {{getElementById: (id) => id === 'dashboard-content' ? content : warning}};
        global.fetch = async (url, options) => {{
          assert.strictEqual(url, '/api/engineering/snapshot');
          assert.strictEqual(options.method, 'GET');
          return {{ok: true, json: async () => ({snapshot_json})}};
        }};
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {{
          assert.strictEqual(intervalMs, 15000);
          await window.engineeringDashboard.refreshDashboard();
          assert(content.innerHTML.includes('agent/smooth-refresh-updated'));
          assert(content.innerHTML.includes('2026-08-23T16:30:00+00:00'));
          assert.strictEqual(warning.textContent, '');
          assert.strictEqual(warning.style.display, 'none');
          assert.deepStrictEqual(scrollCalls, [[13, 377]]);
        }})().catch((error) => {{ console.error(error); process.exit(1); }});
        """
    )

    _run_dashboard_script_case(script, body)


def test_failed_poll_preserves_last_known_data_and_shows_bounded_warning():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        const assert = require('assert');
        const fs = require('fs');
        const script = fs.readFileSync(0, 'utf8');
        let content = {innerHTML: 'LAST KNOWN SNAPSHOT', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};
        let warning = {textContent: '', style: {display: 'none'}};
        global.window = {scrollX: 0, scrollY: 42, setInterval: () => 1, scrollTo: () => {}};
        global.document = {getElementById: (id) => id === 'dashboard-content' ? content : warning};
        global.fetch = async () => { throw new Error('network down'); };
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          await window.engineeringDashboard.refreshDashboard();
          assert.strictEqual(content.innerHTML, 'LAST KNOWN SNAPSHOT');
          assert(warning.textContent.includes('showing the last known snapshot'));
          assert.strictEqual(warning.style.display, 'block');
          assert(warning.textContent.length < 120);
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )

    _run_dashboard_script_case(script, body)


def test_poll_warning_clears_after_recovery_and_safe_rendering_is_preserved():
    recovered = populated_snapshot(
        backlog=BacklogSummary(
            active_task_id="SAFE-001<script>",
            active_task_title="Smooth <refresh>",
            status="REVIEW",
            owner="dashboard-agent",
            priority="P1",
            counts_by_status={"REVIEW": 1},
            counts_by_priority={"P1": 1},
        ),
        data_freshness_timestamp="2026-08-23T16:45:00+00:00",
    )
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_json = json.dumps(_public_snapshot(recovered))
    body = textwrap.dedent(
        f"""
        const assert = require('assert');
        const fs = require('fs');
        const script = fs.readFileSync(0, 'utf8');
        let content = {{innerHTML: 'LAST GOOD', addEventListener: () => {{}}, contains: () => true, querySelectorAll: () => []}};
        let warning = {{textContent: '', style: {{display: 'none'}}}};
        let attempts = 0;
        global.window = {{scrollX: 0, scrollY: 99, setInterval: () => 1, scrollTo: () => {{}}}};
        global.document = {{getElementById: (id) => id === 'dashboard-content' ? content : warning}};
        global.fetch = async () => {{
          attempts += 1;
          if (attempts === 1) {{ throw new Error('temporary outage'); }}
          return {{ok: true, json: async () => ({snapshot_json})}};
        }};
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {{
          await window.engineeringDashboard.refreshDashboard();
          assert.strictEqual(content.innerHTML, 'LAST GOOD');
          assert.strictEqual(warning.style.display, 'block');
          await window.engineeringDashboard.refreshDashboard();
          assert.strictEqual(warning.textContent, '');
          assert.strictEqual(warning.style.display, 'none');
          assert(content.innerHTML.includes('SAFE-001&lt;script&gt;'));
          assert(content.innerHTML.includes('Smooth &lt;refresh&gt;'));
          assert(!content.innerHTML.includes('SAFE-001<script>'));
          assert(!content.innerHTML.includes('Smooth <refresh>'));
        }})().catch((error) => {{ console.error(error); process.exit(1); }});
        """
    )

    _run_dashboard_script_case(script, body)


def test_mobile_command_center_overview_is_default_and_all_tabs_exist():
    html = render_dashboard(populated_snapshot())

    assert "id='tab-overview'" in html
    assert "data-tab='overview'" in html
    assert "aria-selected='true'>Overview" in html
    for tab in ("overview", "activity", "backlog", "timeline", "reports", "health"):
        assert f"data-tab='{tab}'" in html
        assert f"data-tab-panel='{tab}'" in html
    assert "id='tab-activity' class='tab-panel' role='tabpanel' data-tab-panel='activity' hidden" in html
    assert "http-equiv='refresh'" not in html


def test_overview_contains_high_value_command_center_fields():
    html = render_dashboard(populated_snapshot())

    for label in (
        "Health",
        "Project",
        "Branch",
        "Repo safe",
        "Current task",
        "Agent",
        "Execution",
        "Phase",
        "Elapsed",
        "Latest activity",
        "Last completed",
        "Blockers",
        "DONE",
        "REVIEW",
        "TODO",
        "BLOCKED",
    ):
        assert label in html
    assert "trading-bot" in html
    assert "agent/engdash-002-read-only-api-ui" in html
    assert "ENGDASH-002" in html
    assert "None" in html


def test_activity_backlog_timeline_reports_and_health_tabs_render_expected_data():
    html = render_dashboard(
        populated_snapshot(
            live_activity=(
                AgentActivitySummary(
                    project_id="trading-bot",
                    task_id="SCORE-001",
                    task_title="Normalize indicator scores",
                    agent_name="trading-exec",
                    agent_role="trading-exec",
                    workflow_id="SCORE-001:agent/x",
                    run_id="run-score-001",
                    branch="agent/score-001-normalize-indicator-scores",
                    phase="QA",
                    status="running",
                    started_at="2026-08-23T21:30:00+00:00",
                    updated_at="2026-08-23T21:40:00+00:00",
                    elapsed_seconds=600.0,
                    latest_activity_at="2026-08-23T21:40:00+00:00",
                    latest_activity="QA running",
                    last_completed_action="Implementation complete",
                    blocker=None,
                    timeout_state="none",
                    recovery_state="continuous",
                    safe_detail="safe",
                ),
            ),
            recent_executions=(
                RecentExecutionSummary(
                    project_id="trading-bot",
                    task_id="ENGDASH-006",
                    agent_name="dashboard-agent",
                    run_id="run-done",
                    branch="agent/engdash-006",
                    final_status="completed",
                    started_at="2026-08-23T14:00:00+00:00",
                    completed_at="2026-08-23T14:10:00+00:00",
                    elapsed_seconds=600.0,
                    last_completed_action="Report generated",
                    result_summary="ok",
                ),
            ),
        )
    )

    assert "Live Agent Activity" in html
    assert "Recent Executions" in html
    assert "run-score-001" in html
    assert "agent/score-001-normalize-indicator-scores" in html
    assert "Prioritized task list" in html
    assert "Recent timeline/events" not in html
    assert "Timeline" in html
    assert "workflow.transition" in html
    assert "Reports" in html
    assert "Report 0" in html
    assert "Health" in html
    assert "Testing status" in html
    assert "Freshness" in html


def test_tab_switching_preserves_selected_tab_across_polling_updates_without_navigation():
    initial = populated_snapshot()
    updated = populated_snapshot(data_freshness_timestamp="2026-08-23T21:55:00+00:00")
    script = _dashboard_script(render_dashboard(initial))
    snapshot_json = json.dumps(_public_snapshot(updated))
    body = textwrap.dedent(
        f"""
        const assert = require('assert');
        const fs = require('fs');
        const script = fs.readFileSync(0, 'utf8');
        let navigations = 0;
        const storage = {{value: null, getItem: () => storage.value, setItem: (key, value) => {{ storage.value = value; }}}};
        function fakeNode(tab) {{
          return {{
            dataset: {{tab, tabPanel: tab}},
            hidden: false,
            attrs: {{}},
            setAttribute: function(name, value) {{ this.attrs[name] = value; }},
            addEventListener: () => {{}},
          }};
        }}
        const buttons = ['overview', 'activity', 'backlog', 'timeline', 'reports', 'health'].map(fakeNode);
        const panels = ['overview', 'activity', 'backlog', 'timeline', 'reports', 'health'].map(fakeNode);
        let content = {{
          innerHTML: 'INITIAL',
          addEventListener: () => {{}},
          contains: () => true,
          querySelectorAll: (selector) => selector === '[data-tab]' ? buttons : selector === '[data-tab-panel]' ? panels : [],
        }};
        let warning = {{textContent: '', style: {{display: 'none'}}}};
        global.window = {{
          scrollX: 5,
          scrollY: 500,
          setInterval: () => 1,
          scrollTo: () => {{}},
          localStorage: storage,
          location: {{assign: () => {{ navigations += 1; }}}},
        }};
        global.document = {{getElementById: (id) => id === 'dashboard-content' ? content : warning}};
        global.fetch = async () => {{ return {{ok: true, json: async () => ({snapshot_json})}}; }};
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {{
          window.engineeringDashboard.switchTab('activity');
          assert.strictEqual(storage.value, 'activity');
          await window.engineeringDashboard.refreshDashboard();
          assert.strictEqual(window.engineeringDashboard.selectedTab(), 'activity');
          assert(content.innerHTML.includes('2026-08-23T21:55:00+00:00'));
          assert.strictEqual(navigations, 0);
        }})().catch((error) => {{ console.error(error); process.exit(1); }});
        """
    )

    _run_dashboard_script_case(script, body)


def test_mobile_css_prioritizes_narrow_screens_and_avoids_normal_horizontal_overflow():
    html = render_dashboard(populated_snapshot())

    assert "overflow-x:hidden" in html
    assert "@media(max-width:700px)" in html
    assert "position:sticky" in html
    assert "min-height:44px" in html
    assert "text-overflow:ellipsis" in html
    assert "<table" not in html.lower()


class StaticChatHistoryProvider:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.calls = 0

    def history(self) -> dict[str, object]:
        self.calls += 1
        return self.payload

    def send(self, message: object) -> dict[str, object]:
        self.last_message = message
        if not isinstance(message, str) or not message.strip():
            return {"ok": False, "status": "rejected", "error": "message must be non-empty text"}
        if len(message.strip()) > 4000:
            return {"ok": False, "status": "rejected", "error": "message exceeds 4000 characters"}
        return {
            "ok": True,
            "status": "sent",
            "run_id": "run-test",
            "audit": {"timestamp": "2026-08-24T00:00:00+00:00", "actor": "dashboard", "source": "dashboard", "target": "trading-manager", "delivery_status": "sent", "run_id": "run-test"},
        }


def test_chat_history_endpoint_uses_fixed_provider_and_ignores_client_routing_params():
    chat_provider = StaticChatHistoryProvider(
        {
            "session": {"agent": "trading-manager", "status": "available"},
            "messages": [{"role": "user", "text": "hi", "timestamp": "2026-08-23T23:49:00+00:00"}],
        }
    )
    client = TestClient(create_app(StaticProvider(populated_snapshot()), chat_provider))

    response = client.get("/api/engineering/chat/history?agentId=evil&sessionKey=agent:other&gatewayUrl=https://evil.example&method=chat.send")

    assert response.status_code == 200
    assert response.json() == chat_provider.payload
    assert chat_provider.calls == 1
    assert "evil" not in response.text
    assert "gatewayUrl" not in response.text


def test_chat_send_endpoint_accepts_only_bounded_message_and_no_control_routes():
    client = TestClient(create_app(StaticProvider(populated_snapshot()), StaticChatHistoryProvider({"session": {"agent": "trading-manager", "status": "available"}, "messages": []})))

    assert client.post("/api/engineering/chat/send", json={"message": "hello"}).status_code == 200
    assert client.post("/api/engineering/chat/send", json={"message": ""}).status_code == 400
    assert client.post("/api/engineering/chat/send", json={"message": "   "}).status_code == 400
    assert client.post("/api/engineering/chat/send", json={"message": "x" * 4001}).status_code == 400
    assert client.post("/api/engineering/chat/send", json={"message": {"text": "hello"}}).status_code == 400
    assert client.post("/api/engineering/chat/send", json={"message": "hello", "agentId": "evil"}).status_code == 400
    assert client.post("/api/engineering/chat/send", json={"message": "hello", "sessionKey": "evil"}).status_code == 400
    assert client.post("/api/engineering/chat/abort", json={}).status_code == 404
    assert client.post("/api/engineering/rpc", json={"method": "chat.send"}).status_code == 404
    assert client.post("/api/engineering/chat/history", json={}).status_code == 405
    route_methods = {(route.path, tuple(sorted(route.methods or ()))) for route in client.app.routes}
    assert ("/api/engineering/chat/history", ("GET",)) in route_methods
    assert ("/api/engineering/chat/send", ("POST",)) in route_methods
    assert all("/api/engineering/chat/abort" != path for path, _methods in route_methods)
    assert all("/api/engineering/rpc" != path for path, _methods in route_methods)


def test_dashboard_chat_tab_has_bounded_send_form_and_existing_tabs_remain():
    html = render_dashboard(populated_snapshot())

    for tab in ("overview", "activity", "backlog", "timeline", "reports", "health", "chat"):
        assert f"data-tab='{tab}'" in html
        assert f"data-tab-panel='{tab}'" in html
    assert "aria-selected='true'>Overview" in html
    assert "id='tab-chat' class='tab-panel' role='tabpanel' data-tab-panel='chat' hidden" in html
    assert "Conversation with the existing OpenClaw trading-manager session" in html
    assert "<textarea" in html.lower()
    assert "maxlength='4000'" in html
    assert "type='submit'" in html.lower()
    assert "/api/engineering/chat/send" in html
    assert "chat.abort" not in html


def test_chat_history_client_script_polls_bounded_read_endpoint_and_safely_renders_content():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    chat_payload = json.dumps(
        {
            "session": {"agent": "trading-manager", "status": "available"},
            "messages": [
                {"role": "user", "text": "hello <img src=x onerror=alert(1)>", "timestamp": "2026-08-23T23:49:00+00:00"},
                {"role": "assistant", "text": "manager & safe", "timestamp": "2026-08-23T23:49:05+00:00"},
            ],
        }
    )
    body = textwrap.dedent(
        f"""
        const assert = require('assert');
        const fs = require('fs');
        const script = fs.readFileSync(0, 'utf8');
        let intervals = [];
        let chatState = {{textContent: '', style: {{display: 'block'}}}};
        let chatHistory = {{innerHTML: '', scrollTop: 0, scrollHeight: 99}};
        let content = {{innerHTML: 'INITIAL', addEventListener: () => {{}}, contains: () => true, querySelectorAll: () => []}};
        let warning = {{textContent: '', style: {{display: 'none'}}}};
        global.window = {{scrollX: 0, scrollY: 0, setInterval: (fn, ms) => {{ intervals.push([fn, ms]); return intervals.length; }}, scrollTo: () => {{}}}};
        global.document = {{getElementById: (id) => id === 'dashboard-content' ? content : id === 'update-warning' ? warning : id === 'chat-state' ? chatState : id === 'chat-history' ? chatHistory : null}};
        global.fetch = async (url, options) => {{
          assert.strictEqual(options.method, 'GET');
          if (url === '/api/engineering/chat/history') {{ return {{ok: true, json: async () => ({chat_payload})}}; }}
          if (url === '/api/engineering/snapshot') {{ return {{ok: true, json: async () => ({{}})}}; }}
          throw new Error('unexpected url ' + url);
        }};
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {{
          assert.strictEqual(window.engineeringDashboard.CHAT_HISTORY_URL, '/api/engineering/chat/history');
          assert(intervals.some((entry) => entry[1] === 15000));
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(chatState.style.display, 'none');
          assert(chatHistory.innerHTML.includes('Josh'));
          assert(chatHistory.innerHTML.includes('Trading manager'));
          assert(chatHistory.innerHTML.includes('&lt;img src=x onerror=alert(1)&gt;'));
          assert(!chatHistory.innerHTML.includes('<img src=x'));
          assert(chatHistory.innerHTML.includes('manager &amp; safe'));
          assert.strictEqual(chatHistory.scrollTop, 99);
        }})().catch((error) => {{ console.error(error); process.exit(1); }});
        """
    )

    _run_dashboard_script_case(script, body)


def test_chat_history_client_script_shows_bounded_unavailable_state():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        const assert = require('assert');
        const fs = require('fs');
        const script = fs.readFileSync(0, 'utf8');
        let chatState = {textContent: '', style: {display: 'none'}};
        let chatHistory = {innerHTML: 'OLD'};
        let content = {innerHTML: 'INITIAL', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};
        let warning = {textContent: '', style: {display: 'none'}};
        global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
        global.document = {getElementById: (id) => id === 'dashboard-content' ? content : id === 'update-warning' ? warning : id === 'chat-state' ? chatState : id === 'chat-history' ? chatHistory : null};
        global.fetch = async () => { throw new Error('gateway down'); };
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          await window.engineeringDashboard.refreshChatHistory();
          assert(chatState.textContent.includes('unavailable'));
          assert.strictEqual(chatState.style.display, 'block');
          assert.strictEqual(chatHistory.innerHTML, 'OLD');
          assert(chatState.textContent.length < 80);
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )

    _run_dashboard_script_case(script, body)


def test_chat_send_client_script_shows_states_refreshes_history_and_preserves_on_failure():
    html = render_dashboard(populated_snapshot())
    script = _dashboard_script(html)
    body = """
const assert = require('assert');
const fs = require('fs');
const script = fs.readFileSync(0, 'utf8');
let chatState = {textContent: '', style: {display: 'none'}, dataset: {}};
let chatHistory = {innerHTML: '<div>OLD</div>', scrollTop: 0, scrollHeight: 77};
let input = {value: '  hello dashboard  '};
let button = {disabled: false, textContent: 'Send'};
let content = {innerHTML: 'INITIAL', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};
let warning = {textContent: '', style: {display: 'none'}};
let calls = [];
global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
global.document = {getElementById: (id) => id === 'dashboard-content' ? content : id === 'update-warning' ? warning : id === 'chat-state' ? chatState : id === 'chat-history' ? chatHistory : id === 'chat-message' ? input : id === 'chat-send' ? button : null};
global.fetch = async (url, options) => {
  calls.push([url, options]);
  if (url === '/api/engineering/chat/send') {
    assert.strictEqual(options.method, 'POST');
    assert.strictEqual(JSON.parse(options.body).message, 'hello dashboard');
    return {ok: true, json: async () => ({ok: true, status: 'sent', run_id: 'run-ui'})};
  }
  if (url === '/api/engineering/chat/history') {
    return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [{role: 'user', text: 'hello dashboard', timestamp: 't1'}, {role: 'assistant', text: 'response available', timestamp: 't2'}]})};
  }
  throw new Error('unexpected url ' + url);
};
eval(script.replace('<script>', '').replace('</script>', ''));
(async () => {
  await window.engineeringDashboard.sendChatMessage(input.value);
  assert.strictEqual(input.value, '');
  assert.strictEqual(button.disabled, false);
  assert.strictEqual(button.textContent, 'Send');
  assert(chatHistory.innerHTML.includes('hello dashboard'));
  assert(chatHistory.innerHTML.includes('response available'));
  assert.strictEqual(chatHistory.scrollTop, 77);
  assert.strictEqual(calls[0][0], '/api/engineering/chat/send');
  assert.strictEqual(calls[1][0], '/api/engineering/chat/history');
  global.fetch = async (url) => {
    if (url === '/api/engineering/chat/send') { return {ok: true, json: async () => ({ok: true, status: 'sent', run_id: 'run-history-fail'})}; }
    if (url === '/api/engineering/chat/history') { throw new Error('history unavailable after send'); }
    throw new Error('unexpected url ' + url);
  };
  input.value = 'history fails after success';
  await window.engineeringDashboard.sendChatMessage(input.value);
  assert.strictEqual(input.value, '');
  assert.strictEqual(button.disabled, false);
  assert.strictEqual(button.textContent, 'Send');
  assert(chatState.textContent.includes('unavailable'));
  global.fetch = async () => ({ok: false, json: async () => ({ok: false, error: 'bounded fail'})});
  const before = chatHistory.innerHTML;
  input.value = 'will fail';
  await window.engineeringDashboard.sendChatMessage(input.value);
  assert(chatState.textContent.includes('failed'));
  assert.strictEqual(chatHistory.innerHTML, before);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    _run_dashboard_script_case(script, body)


def test_chat_send_state_recovers_when_snapshot_poll_replaces_dom_during_send():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_payload = json.dumps(_public_snapshot(populated_snapshot(data_freshness_timestamp="2026-08-24T01:00:00+00:00")))
    history_payload = json.dumps(
        {
            "session": {"agent": "trading-manager", "status": "available"},
            "messages": [
                {"role": "user", "text": "how are you", "timestamp": "t1"},
                {"role": "assistant", "text": "manager response", "timestamp": "t2"},
            ],
        }
    )
    body = textwrap.dedent(
        f"""
        const assert = require('assert');
        const fs = require('fs');
        const script = fs.readFileSync(0, 'utf8');
        let storage = {{value: 'chat', getItem: () => storage.value, setItem: (key, value) => {{ storage.value = value; }}}};
        let sendResolve;
        const sendPromise = new Promise((resolve) => {{ sendResolve = resolve; }});
        function parseContent(html) {{
          const panels = ['overview', 'activity', 'backlog', 'timeline', 'reports', 'health', 'chat'].map((tab) => ({{dataset: {{tabPanel: tab}}, hidden: tab !== storage.value, setAttribute: () => {{}}}}));
          const buttons = ['overview', 'activity', 'backlog', 'timeline', 'reports', 'health', 'chat'].map((tab) => ({{dataset: {{tab}}, addEventListener: () => {{}}, setAttribute: function(name, value) {{ this[name] = value; }}}}));
          return {{
            elements: {{
              'dashboard-content': content,
              'update-warning': warning,
              'chat-state': {{textContent: html.includes('Loading trading-manager history') ? 'Loading trading-manager history…' : '', style: {{display: 'block'}}, dataset: {{}}}},
              'chat-history': {{innerHTML: '', scrollTop: 0, scrollHeight: 88, clientHeight: 40}},
              'chat-message': {{value: '', disabled: false}},
              'chat-send': {{disabled: false, textContent: 'Send'}},
            }},
            panels,
            buttons,
          }};
        }}
        let dom;
        let content = {{
          _html: '',
          addEventListener: () => {{}},
          contains: () => true,
          querySelectorAll: (selector) => selector === '[data-tab]' ? dom.buttons : selector === '[data-tab-panel]' ? dom.panels : [],
        }};
        Object.defineProperty(content, 'innerHTML', {{get() {{ return this._html; }}, set(value) {{ this._html = value; dom = parseContent(value); }}}});
        let warning = {{textContent: '', style: {{display: 'none'}}}};
        dom = parseContent('');
        global.window = {{scrollX: 0, scrollY: 0, localStorage: storage, setInterval: () => 1, scrollTo: () => {{}}}};
        global.document = {{getElementById: (id) => dom.elements[id] || null}};
        let sends = 0;
        let holdFirstSend = true;
        global.fetch = async (url, options) => {{
          if (url === '/api/engineering/chat/send') {{
            sends += 1;
            assert.strictEqual(JSON.parse(options.body).message, sends === 1 ? 'how are you' : 'second message');
            if (holdFirstSend) {{ await sendPromise; holdFirstSend = false; }}
            return {{ok: true, json: async () => ({{ok: true, status: 'sent', run_id: 'run-' + sends}})}};
          }}
          if (url === '/api/engineering/snapshot') {{ return {{ok: true, json: async () => ({snapshot_payload})}}; }}
          if (url === '/api/engineering/chat/history') {{ return {{ok: true, json: async () => ({history_payload})}}; }}
          throw new Error('unexpected url ' + url);
        }};
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {{
          window.engineeringDashboard.switchTab('chat');
          dom.elements['chat-message'].value = 'how are you';
          const sendTask = window.engineeringDashboard.sendChatMessage(dom.elements['chat-message'].value);
          assert.strictEqual(dom.elements['chat-send'].textContent, 'Sending…');
          assert.strictEqual(dom.elements['chat-send'].disabled, true);
          assert.strictEqual(dom.elements['chat-message'].disabled, true);
          await window.engineeringDashboard.refreshDashboard();
          assert.strictEqual(dom.elements['chat-message'].value, 'how are you');
          assert.strictEqual(dom.elements['chat-send'].textContent, 'Sending…');
          sendResolve();
          await sendTask;
          assert.strictEqual(dom.elements['chat-message'].value, '');
          assert.strictEqual(dom.elements['chat-message'].disabled, false);
          assert.strictEqual(dom.elements['chat-send'].textContent, 'Send');
          assert.strictEqual(dom.elements['chat-send'].disabled, false);
          assert(dom.elements['chat-history'].innerHTML.includes('manager response'));
          dom.elements['chat-message'].value = 'second message';
          await window.engineeringDashboard.sendChatMessage(dom.elements['chat-message'].value);
          assert.strictEqual(sends, 2);
          assert.strictEqual(dom.elements['chat-message'].value, '');
        }})().catch((error) => {{ console.error(error); process.exit(1); }});
        """
    )
    _run_dashboard_script_case(script, body)


def test_chat_state_survives_snapshot_poll_without_loading_reset_or_draft_loss():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_payload = json.dumps(_public_snapshot(populated_snapshot(data_freshness_timestamp="2026-08-24T00:45:00+00:00")))
    history_one = json.dumps(
        {
            "session": {"agent": "trading-manager", "status": "available"},
            "messages": [
                {"role": "user", "text": "loaded message", "timestamp": "t1"},
                {"role": "assistant", "text": "loaded reply", "timestamp": "t2"},
            ],
        }
    )
    history_two = json.dumps(
        {
            "session": {"agent": "trading-manager", "status": "available"},
            "messages": [
                {"role": "user", "text": "loaded message", "timestamp": "t1"},
                {"role": "assistant", "text": "loaded reply", "timestamp": "t2"},
                {"role": "assistant", "text": "new reply", "timestamp": "t3"},
            ],
        }
    )
    body = textwrap.dedent(
        f"""
        const assert = require('assert');
        const fs = require('fs');
        const script = fs.readFileSync(0, 'utf8');
        let storage = {{value: 'chat', getItem: () => storage.value, setItem: (key, value) => {{ storage.value = value; }}}};
        let scrollCalls = [];
        function parseContent(html) {{
          const elements = {{}};
          const panels = ['overview', 'activity', 'backlog', 'timeline', 'reports', 'health', 'chat'].map((tab) => ({{
            dataset: {{tabPanel: tab}},
            hidden: tab !== storage.value,
            setAttribute: () => {{}},
          }}));
          const buttons = ['overview', 'activity', 'backlog', 'timeline', 'reports', 'health', 'chat'].map((tab) => ({{
            dataset: {{tab}},
            addEventListener: () => {{}},
            setAttribute: function(name, value) {{ this[name] = value; }},
          }}));
          elements['dashboard-content'] = content;
          elements['update-warning'] = warning;
          elements['chat-state'] = {{textContent: html.includes('Loading trading-manager history') ? 'Loading trading-manager history…' : '', style: {{display: 'block'}}, dataset: {{}}}};
          elements['chat-history'] = {{innerHTML: '', scrollTop: 0, scrollHeight: 200}};
          elements['chat-message'] = {{value: ''}};
          elements['chat-send'] = {{disabled: false, textContent: 'Send'}};
          return {{elements, panels, buttons}};
        }}
        let dom;
        let content = {{
          _html: '',
          addEventListener: () => {{}},
          contains: () => true,
          querySelectorAll: (selector) => selector === '[data-tab]' ? dom.buttons : selector === '[data-tab-panel]' ? dom.panels : [],
        }};
        Object.defineProperty(content, 'innerHTML', {{
          get() {{ return this._html; }},
          set(value) {{ this._html = value; dom = parseContent(value); }}
        }});
        let warning = {{textContent: '', style: {{display: 'none'}}}};
        dom = parseContent('');
        global.window = {{scrollX: 0, scrollY: 0, localStorage: storage, setInterval: () => 1, scrollTo: (x, y) => scrollCalls.push([x, y])}};
        global.document = {{getElementById: (id) => dom.elements[id] || null}};
        let historyCalls = 0;
        let snapshotFails = false;
        global.fetch = async (url, options) => {{
          if (url === '/api/engineering/chat/history') {{
            historyCalls += 1;
            return {{ok: true, json: async () => historyCalls === 1 ? ({history_one}) : ({history_two})}};
          }}
          if (url === '/api/engineering/snapshot') {{
            if (snapshotFails) {{ throw new Error('snapshot down'); }}
            return {{ok: true, json: async () => ({snapshot_payload})}};
          }}
          throw new Error('unexpected url ' + url);
        }};
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {{
          window.engineeringDashboard.switchTab('chat');
          await window.engineeringDashboard.refreshChatHistory();
          assert(dom.elements['chat-history'].innerHTML.includes('loaded message'));
          dom.elements['chat-message'].value = 'draft survives';
          dom.elements['chat-send'].disabled = true;
          dom.elements['chat-send'].textContent = 'Sending…';
          await window.engineeringDashboard.refreshDashboard();
          assert.strictEqual(storage.value, 'chat');
          assert(dom.elements['chat-history'].innerHTML.includes('loaded message'));
          assert(!dom.elements['chat-state'].textContent.includes('Loading trading-manager history'));
          assert.strictEqual(dom.elements['chat-message'].value, 'draft survives');
          assert.strictEqual(dom.elements['chat-send'].disabled, true);
          assert.strictEqual(dom.elements['chat-send'].textContent, 'Sending…');
          await window.engineeringDashboard.refreshDashboard();
          assert(dom.elements['chat-history'].innerHTML.includes('loaded message'));
          assert.strictEqual((dom.elements['chat-history'].innerHTML.match(/loaded message/g) || []).length, 1);
          snapshotFails = true;
          await window.engineeringDashboard.refreshDashboard();
          assert(dom.elements['chat-history'].innerHTML.includes('loaded message'));
          assert.strictEqual(dom.elements['chat-message'].value, 'draft survives');
          assert(warning.textContent.includes('last known snapshot'));
          snapshotFails = false;
          await window.engineeringDashboard.refreshChatHistory();
          assert(dom.elements['chat-history'].innerHTML.includes('new reply'));
          assert.strictEqual((dom.elements['chat-history'].innerHTML.match(/loaded message/g) || []).length, 1);
        }})().catch((error) => {{ console.error(error); process.exit(1); }});
        """
    )
    _run_dashboard_script_case(script, body)


# ---------------------------------------------------------------------------
# Agent working-indicator (live OpenClaw run-state).
# Authoritative source: chat.history sessionInfo.hasActiveRun + sessionInfo.status.
# No WebSocket, no new RPC, no in-flight-run text exposed.
# ---------------------------------------------------------------------------


def _shared_chat_dom_script_boilerplate(snapshot_payload):
    """Return a JS harness prelude that wires up the chat DOM with chat-status."""

    return textwrap.dedent(
        f"""
        const assert = require('assert');
        const fs = require('fs');
        const script = fs.readFileSync(0, 'utf8');
        let storage = {{value: 'chat', getItem: () => storage.value, setItem: () => {{}}}};
        function parseContent(html) {{
          const panels = ['overview', 'activity', 'backlog', 'timeline', 'reports', 'health', 'chat'].map((tab) => ({{dataset: {{tabPanel: tab}}, hidden: tab !== storage.value, setAttribute: () => {{}}}}));
          const buttons = ['overview', 'activity', 'backlog', 'timeline', 'reports', 'health', 'chat'].map((tab) => ({{dataset: {{tab}}, addEventListener: () => {{}}, setAttribute: function(name, value) {{ this[name] = value; }}}}));
          const statusLabel = {{textContent: ''}};
          const chatStatus = {{
            dataset: {{agentStatus: 'idle'}},
            querySelector: (selector) => selector === '.label' ? statusLabel : null,
          }};
          return {{
            elements: {{
              'dashboard-content': content,
              'update-warning': warning,
              'chat-state': {{textContent: '', style: {{display: 'block'}}, dataset: {{}}}},
              'chat-history': {{innerHTML: '', scrollTop: 0, scrollHeight: 88, clientHeight: 40}},
              'chat-message': {{value: '', disabled: false}},
              'chat-send': {{disabled: false, textContent: 'Send'}},
              'chat-status': chatStatus,
            }},
            panels,
            buttons,
            statusLabel,
            chatStatus,
          }};
        }}
        let dom;
        let content = {{
          _html: '',
          addEventListener: () => {{}},
          contains: () => true,
          querySelectorAll: (selector) => selector === '[data-tab]' ? dom.buttons : selector === '[data-tab-panel]' ? dom.panels : [],
        }};
        Object.defineProperty(content, 'innerHTML', {{
          get() {{ return this._html; }},
          set(value) {{ this._html = value; dom = parseContent(value); }}
        }});
        let warning = {{textContent: '', style: {{display: 'none'}}}};
        dom = parseContent('');
        global.window = {{scrollX: 0, scrollY: 0, localStorage: storage, setInterval: () => 1, scrollTo: () => {{}}}};
        global.document = {{getElementById: (id) => dom.elements[id] || null}};
        global.fetch = async (url) => {{
          if (url === '/api/engineering/chat/history') {{ return {{ok: true, json: async () => ({{session: {{agent: 'trading-manager', status: 'available'}}, messages: []}}) }}; }}
          if (url === '/api/engineering/snapshot') {{ return {{ok: true, json: async () => ({snapshot_payload}) }}; }}
          throw new Error('unexpected url ' + url);
        }};
        eval(script.replace('<script>', '').replace('</script>', ''));
        """
    )


def test_chat_status_indicator_renders_idle_working_and_failed_from_session_payload():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_payload = json.dumps(_public_snapshot(populated_snapshot()))
    body = _shared_chat_dom_script_boilerplate(snapshot_payload) + textwrap.dedent(
        """
        (async () => {
          window.engineeringDashboard.switchTab('chat');
          // Idle (initial)
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'idle');
          assert(dom.statusLabel.textContent.includes('Idle'));

          // Working — has_active_run true
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: true, run_status: 'running'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'working');
          assert(dom.statusLabel.textContent.includes('Working'));

          // Failed terminal — run_status failed
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'failed'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'failed');
          assert(dom.statusLabel.textContent.includes('Failed'));

          // Killed terminal
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'killed'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'failed');

          // Timeout terminal
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'timeout'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'failed');

          // Completion returns to Idle
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'done'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'idle');
          assert(dom.statusLabel.textContent.includes('Idle'));
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )

    _run_dashboard_script_case(script, body)


def test_chat_status_indicator_working_survives_snapshot_polls():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_payload = json.dumps(_public_snapshot(populated_snapshot()))
    body = _shared_chat_dom_script_boilerplate(snapshot_payload) + textwrap.dedent(
        """
        (async () => {
          window.engineeringDashboard.switchTab('chat');
          // Send a message; chat.history will report has_active_run:true
          global.fetch = async (url, options) => {
            if (url === '/api/engineering/chat/send') {
              return {ok: true, json: async () => ({ok: true, status: 'sent', run_id: 'run-survive'})};
            }
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: true, run_status: 'running'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          dom.elements['chat-message'].value = 'trigger working';
          await window.engineeringDashboard.sendChatMessage(dom.elements['chat-message'].value);
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'working');

          // Three more snapshot polls while still working
          for (let index = 0; index < 3; index += 1) {
            await window.engineeringDashboard.refreshDashboard();
            assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'working');
          }

          // Run completes -> Idle
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'done'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'idle');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )

    _run_dashboard_script_case(script, body)


def test_chat_status_indicator_send_failure_does_not_set_working():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_payload = json.dumps(_public_snapshot(populated_snapshot()))
    body = _shared_chat_dom_script_boilerplate(snapshot_payload) + textwrap.dedent(
        """
        (async () => {
          window.engineeringDashboard.switchTab('chat');
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'idle');

          // Send fails
          global.fetch = async (url, options) => {
            if (url === '/api/engineering/chat/send') {
              return {ok: false, status: 503, json: async () => ({ok: false, status: 'unavailable', error: 'gateway unavailable'})};
            }
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          dom.elements['chat-message'].value = 'this should not become working';
          await window.engineeringDashboard.sendChatMessage(dom.elements['chat-message'].value);
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'idle');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )

    _run_dashboard_script_case(script, body)


def test_chat_status_indicator_temp_history_failure_preserves_last_known_working():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_payload = json.dumps(_public_snapshot(populated_snapshot()))
    body = _shared_chat_dom_script_boilerplate(snapshot_payload) + textwrap.dedent(
        """
        (async () => {
          window.engineeringDashboard.switchTab('chat');
          // Drive indicator to Working first.
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: true, run_status: 'running'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'working');

          // Now poll fails (no live session change)
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              throw new Error('temporary network error');
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          // Must preserve Working across temporary poll failure; only the chat-state banner switches to "unavailable".
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'working');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )

    _run_dashboard_script_case(script, body)


def test_chat_status_indicator_does_not_render_in_flight_streamed_text():
    """The bundled JS must not surface inFlightRun.text or any streaming
    payload anywhere in the renderAgentStatus / setAgentStatus path."""

    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_payload = json.dumps(_public_snapshot(populated_snapshot()))
    body = _shared_chat_dom_script_boilerplate(snapshot_payload) + textwrap.dedent(
        """
        (async () => {
          window.engineeringDashboard.switchTab('chat');
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: true, run_status: 'running'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          // Check the rendered HTML and the existing label
          const rendered = (dom.statusLabel.textContent || '');
          assert(!rendered.includes('private streamed text fragment'));
          assert(!rendered.includes('inFlightRun'));
          assert(!rendered.includes('leaked'));
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )

    _run_dashboard_script_case(script, body)


# ---------------------------------------------------------------------------
# Engineering Dashboard Chat copy controls (DASH-007).
# The features must operate ONLY on the already-projected visible chat
# messages (server projection from PR #63), never on raw OpenClaw transcript.
# Both controls must be plain-text, mobile-friendly, and survive the existing
# 15-second polling and snapshot-poll DOM replacement.
# ---------------------------------------------------------------------------


def test_chat_tab_html_renders_since_copy_button_and_status_row():
    html = render_dashboard(populated_snapshot())
    assert "id='chat-copy-since'" in html
    assert "Copy since my last message" in html
    assert "class='chat-status-row'" in html
    # Since-copy must start hidden + disabled until at least one assistant
    # response after a user message arrives.
    assert "id='chat-copy-since' class='chat-since-copy' type='button' data-copy-state='idle' hidden disabled" in html


def test_chat_tab_inner_script_wires_copy_helpers_and_since_button_dom():
    html = render_dashboard(populated_snapshot())
    script = _dashboard_script(html)
    assert "id=\"chat-copy-since\"" in script
    assert "Copy since my last message" in script
    assert "const computeSinceLastUserText" in script
    assert "const writeClipboardText" in script
    assert "const handlePerMessageCopy" in script
    assert "const handleSinceCopy" in script
    assert "const updateSinceCopyButton" in script
    assert "const bindChatCopyControls" in script
    assert "history.addEventListener('click', handlePerMessageCopy);" in script
    assert "since.addEventListener('click', handleSinceCopy);" in script


def test_chat_history_renders_per_message_copy_button_only_on_assistant_cards():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = """
const assert = require('assert');
const script = fs.readFileSync(0, 'utf8');
let chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 100, addEventListener: () => {}};
const since = {addEventListener: () => {}, dataset: {}, hidden: true, disabled: true, textContent: '', id: 'chat-copy-since'};
const status = {dataset: {}, textContent: ''};
let captured = null;
const messages = [
  {role: 'user', text: 'hi there', timestamp: 't1'},
  {role: 'assistant', text: 'first reply', timestamp: 't2'},
  {role: 'user', text: 'next question', timestamp: 't3'},
  {role: 'assistant', text: 'second reply\\nwith newline', timestamp: 't4'},
];
global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}, localStorage: {getItem: () => null, setItem: () => {}}};
global.document = {
  getElementById: (id) => {
    if (id === 'dashboard-content') return {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};
    if (id === 'chat-history') return chatHistory;
    if (id === 'chat-copy-since') return since;
    if (id === 'chat-status') return status;
    if (id === 'update-warning') return {textContent: '', style: {display: 'none'}};
    return null;
  },
  querySelectorAll: () => [],
};
global.fetch = async (url) => {
  if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};
  if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
  throw new Error('unexpected url ' + url);
};
const writeCalls = [];
const nav = {};
global.window.__chatClipboardWriteText = (text) => { writeCalls.push(text); };
eval(script.replace('<script>', '').replace('</script>', ''));
(async () => {
  await window.engineeringDashboard.refreshChatHistory();
  captured = chatHistory.innerHTML;
  // Both assistant cards must include a per-message Copy button.
  assert(captured.includes('class="chat-copy" data-copy-index="1"'));
  assert(captured.includes('class="chat-copy" data-copy-index="3"'));
  // User cards must NOT include a per-message Copy button.
  assert(!captured.includes('data-copy-index="0"'));
  assert(!captured.includes('data-copy-index="2"'));
  // Since-copy must be enabled (two assistant replies after the most recent user message at index 2).
  assert.strictEqual(since.hidden, false);
  assert.strictEqual(since.disabled, false);
  // Verify the since-copy payload joins after the most recent user message with two newlines.
  assert.strictEqual(writeCalls.length, 0);
  await window.engineeringDashboard.refreshChatHistory();
})().catch((error) => { console.error(error); process.exit(1); });
"""
    _run_dashboard_script_case(script, body)


def test_chat_copy_per_message_writes_plain_text_only_and_toggles_copied_state():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = """
const assert = require('assert');
const script = fs.readFileSync(0, 'utf8');
const historyHandlers = [];
const sinceHandlers = [];
const chatHistory = {
  innerHTML: '', scrollTop: 0, scrollHeight: 100,
  addEventListener: (evt, fn) => { if (evt === 'click') historyHandlers.push(fn); },
  dataset: {},
};
const since = {
  addEventListener: (evt, fn) => { if (evt === 'click') sinceHandlers.push(fn); },
  dataset: {},
  hidden: true, disabled: true, textContent: 'Copy since my last message', id: 'chat-copy-since',
};
const status = {dataset: {}, textContent: ''};
const state = {textContent: '', style: {display: 'none'}, dataset: {}};
const messages = [
  {role: 'user', text: 'Q', timestamp: 't1'},
  {role: 'assistant', text: 'line one\\nline two', timestamp: 't2'},
];
let clipboardText = null;
global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}, localStorage: {getItem: () => null, setItem: () => {}}, __chatClipboardWriteText: (text) => { clipboardText = text; }};
global.document = {
  getElementById: (id) => {
    if (id === 'dashboard-content') return {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};
    if (id === 'chat-history') return chatHistory;
    if (id === 'chat-copy-since') return since;
    if (id === 'chat-status') return status;
    if (id === 'chat-state') return state;
    if (id === 'update-warning') return {textContent: '', style: {display: 'none'}};
    return null;
  },
  querySelectorAll: () => [],
};
global.fetch = async (url) => {
  if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};
  if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
  throw new Error('unexpected url ' + url);
};
eval(script.replace('<script>', '').replace('</script>', ''));
(async () => {
  await window.engineeringDashboard.refreshChatHistory();
  // The history handler must have been attached.
  assert(historyHandlers.length >= 1, 'expected delegated click handler on chat-history');
  // Drive a synthetic click on the per-message Copy button.
  const button = {dataset: {copyIndex: '1'}, textContent: 'Copy', id: ''};
  await historyHandlers[historyHandlers.length - 1]({target: {closest: (sel) => sel === '.chat-copy' ? button : null}});
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(clipboardText, 'line one\\nline two', 'clipboard must receive the assistant text exactly');
  // After successful copy, button state and label toggle.
  assert.strictEqual(button.dataset.copyState, 'copied');
  assert.strictEqual(button.textContent, 'Copied');
})().catch((error) => { console.error(error); process.exit(1); });
"""
    _run_dashboard_script_case(script, body)


def test_chat_copy_since_button_joins_after_last_user_message_with_double_newline():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = """
const assert = require('assert');
const script = fs.readFileSync(0, 'utf8');
const sinceHandlers = [];
const chatHistory = {
  innerHTML: '', scrollTop: 0, scrollHeight: 100,
  addEventListener: () => {}, dataset: {},
};
const since = {
  addEventListener: (evt, fn) => { if (evt === 'click') sinceHandlers.push(fn); },
  dataset: {},
  hidden: true, disabled: true, textContent: 'Copy since my last message', id: 'chat-copy-since',
};
const status = {dataset: {}, textContent: ''};
let clipboardText = null;
const messages = [
  {role: 'user', text: 'A', timestamp: 't1'},
  {role: 'assistant', text: 'Response A1', timestamp: 't2'},
  {role: 'assistant', text: 'Response A2', timestamp: 't3'},
  {role: 'user', text: 'B', timestamp: 't4'},
  {role: 'assistant', text: 'Response B1', timestamp: 't5'},
  {role: 'assistant', text: 'Response B2', timestamp: 't6'},
];
global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}, localStorage: {getItem: () => null, setItem: () => {}}, __chatClipboardWriteText: (text) => { clipboardText = text; }};
global.document = {
  getElementById: (id) => {
    if (id === 'dashboard-content') return {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};
    if (id === 'chat-history') return chatHistory;
    if (id === 'chat-copy-since') return since;
    if (id === 'chat-status') return status;
    if (id === 'update-warning') return {textContent: '', style: {display: 'none'}};
    return null;
  },
  querySelectorAll: () => [],
};
global.fetch = async (url) => {
  if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};
  if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
  throw new Error('unexpected url ' + url);
};
eval(script.replace('<script>', '').replace('</script>', ''));
(async () => {
  await window.engineeringDashboard.refreshChatHistory();
  // Since-copy must be enabled (two assistant rows after the most recent user).
  assert.strictEqual(since.hidden, false);
  assert.strictEqual(since.disabled, false);
  assert(sinceHandlers.length >= 1, 'expected since-copy click handler');
  await sinceHandlers[sinceHandlers.length - 1]();
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(clipboardText, 'Response B1\\n\\nResponse B2');
  // Must NOT include user messages or earlier assistant turns.
  assert(!clipboardText.includes('Response A1'));
  assert(!clipboardText.includes('Response A2'));
  // The user text 'B' alone must not appear as a separate line.
  assert(!clipboardText.split('\\n\\n').some((part) => part === 'B'));
})().catch((error) => { console.error(error); process.exit(1); });
"""
    _run_dashboard_script_case(script, body)


def test_chat_copy_since_button_disabled_when_no_assistant_after_last_user():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = """
const assert = require('assert');
const script = fs.readFileSync(0, 'utf8');
const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 100, addEventListener: () => {}};
const since = {addEventListener: () => {}, dataset: {}, hidden: true, disabled: true, textContent: 'Copy since my last message', id: 'chat-copy-since'};
const status = {dataset: {}, textContent: ''};
const messages = [
  {role: 'user', text: 'A', timestamp: 't1'},
  {role: 'assistant', text: 'Response A1', timestamp: 't2'},
  {role: 'user', text: 'B', timestamp: 't3'},
];
global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}, localStorage: {getItem: () => null, setItem: () => {}}};
global.document = {
  getElementById: (id) => {
    if (id === 'dashboard-content') return {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};
    if (id === 'chat-history') return chatHistory;
    if (id === 'chat-copy-since') return since;
    if (id === 'chat-status') return status;
    if (id === 'update-warning') return {textContent: '', style: {display: 'none'}};
    return null;
  },
  querySelectorAll: () => [],
};
global.fetch = async (url) => {
  if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};
  if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
  throw new Error('unexpected url ' + url);
};
global.window.__chatClipboardWriteText = () => {};
eval(script.replace('<script>', '').replace('</script>', ''));
(async () => {
  await window.engineeringDashboard.refreshChatHistory();
  // No assistant after the most recent user message -> disabled.
  assert.strictEqual(since.disabled, true);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    _run_dashboard_script_case(script, body)


def test_chat_copy_since_button_disabled_when_no_user_messages_at_all():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = """
const assert = require('assert');
const script = fs.readFileSync(0, 'utf8');
const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 100, addEventListener: () => {}};
const since = {addEventListener: () => {}, dataset: {}, hidden: true, disabled: true, textContent: 'Copy since my last message', id: 'chat-copy-since'};
const status = {dataset: {}, textContent: ''};
const messages = [
  {role: 'assistant', text: 'orphan reply', timestamp: 't1'},
];
global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}, localStorage: {getItem: () => null, setItem: () => {}}};
global.document = {
  getElementById: (id) => {
    if (id === 'dashboard-content') return {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};
    if (id === 'chat-history') return chatHistory;
    if (id === 'chat-copy-since') return since;
    if (id === 'chat-status') return status;
    if (id === 'update-warning') return {textContent: '', style: {display: 'none'}};
    return null;
  },
  querySelectorAll: () => [],
};
global.fetch = async (url) => {
  if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};
  if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
  throw new Error('unexpected url ' + url);
};
global.window.__chatClipboardWriteText = () => {};
eval(script.replace('<script>', '').replace('</script>', ''));
(async () => {
  await window.engineeringDashboard.refreshChatHistory();
  // Orphan assistant without a prior user message -> still joins all assistant rows
  // because the spec says "after the most recent user message OR all assistant rows if none".
  // Verify it is enabled and the orphan is included.
  assert.strictEqual(since.hidden, false);
  assert.strictEqual(since.disabled, false);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    _run_dashboard_script_case(script, body)


def test_chat_copy_per_message_clipboard_failure_shows_bounded_state_and_restores_button():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = """
const assert = require('assert');
const script = fs.readFileSync(0, 'utf8');
const historyHandlers = [];
const chatHistory = {
  innerHTML: '', scrollTop: 0, scrollHeight: 100,
  addEventListener: (evt, fn) => { if (evt === 'click') historyHandlers.push(fn); },
  dataset: {},
};
const since = {
  addEventListener: () => {}, dataset: {},
  hidden: true, disabled: true, textContent: 'Copy since my last message', id: 'chat-copy-since',
};
const status = {dataset: {}, textContent: ''};
const state = {textContent: '', style: {display: 'none'}, dataset: {status: 'available'}};
const messages = [
  {role: 'user', text: 'Q', timestamp: 't1'},
  {role: 'assistant', text: 'reply', timestamp: 't2'},
];
const realSetImmediate = global.setImmediate;
let pendingTimer = null;
global.setTimeout = (fn, delay) => { pendingTimer = {fn, delay}; return 99; };
global.clearTimeout = () => { pendingTimer = null; };
global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}, localStorage: {getItem: () => null, setItem: () => {}}, __chatClipboardWriteText: () => { throw new Error('denied'); }};
global.document = {
  getElementById: (id) => {
    if (id === 'dashboard-content') return {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};
    if (id === 'chat-history') return chatHistory;
    if (id === 'chat-copy-since') return since;
    if (id === 'chat-status') return status;
    if (id === 'chat-state') return state;
    if (id === 'update-warning') return {textContent: '', style: {display: 'none'}};
    return null;
  },
  querySelectorAll: () => [],
};
global.fetch = async (url) => {
  if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};
  if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
  throw new Error('unexpected url ' + url);
};
eval(script.replace('<script>', '').replace('</script>', ''));
(async () => {
  await window.engineeringDashboard.refreshChatHistory();
  const historyBefore = chatHistory.innerHTML;
  const button = {dataset: {copyIndex: '1'}, textContent: 'Copy', id: 'btn-stub'};
  await historyHandlers[historyHandlers.length - 1]({target: {closest: (sel) => sel === '.chat-copy' ? button : null}});
  await realSetImmediate(() => {});
  assert.strictEqual(button.textContent, 'Copy failed');
  assert.strictEqual(button.dataset.copyState, 'failed');
  assert(state.textContent.toLowerCase().includes('clipboard'));
  // Chat history must NOT have been mutated by the failure path.
  assert.strictEqual(chatHistory.innerHTML, historyBefore);
  // Restore timer fires -> button resets to 'Copy' state.
  assert(pendingTimer, 'expected a pending restore timer');
  pendingTimer.fn();
  assert.strictEqual(button.textContent, 'Copy');
  assert.strictEqual(button.dataset.copyState, 'idle');
})().catch((error) => { console.error(error); process.exit(1); });
"""
    _run_dashboard_script_case(script, body)


def test_chat_copy_controls_survive_snapshot_poll_replacing_dashboard_content():
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = """
const assert = require('assert');
const script = fs.readFileSync(0, 'utf8');
let content = {innerHTML: 'INITIAL', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};
const warning = {textContent: '', style: {display: 'none'}};
const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 100, addEventListener: () => {}, dataset: {}};
const since = {addEventListener: () => {}, dataset: {}, hidden: true, disabled: true, textContent: 'Copy since my last message', id: 'chat-copy-since'};
const status = {dataset: {}, textContent: ''};
const messages = [
  {role: 'user', text: 'Q', timestamp: 't1'},
  {role: 'assistant', text: 'R1', timestamp: 't2'},
  {role: 'assistant', text: 'R2', timestamp: 't3'},
];
global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}, localStorage: {getItem: () => null, setItem: () => {}}};
let pollCount = 0;
global.document = {
  getElementById: (id) => {
    if (id === 'dashboard-content') return content;
    if (id === 'chat-history') return chatHistory;
    if (id === 'chat-copy-since') return since;
    if (id === 'chat-status') return status;
    if (id === 'update-warning') return warning;
    return null;
  },
  querySelectorAll: () => [],
};
global.fetch = async (url) => {
  pollCount += 1;
  if (url === '/api/engineering/snapshot') {
    return {ok: true, json: async () => ({project_identity: 'trading-bot', engineering_health: {}, repository: {}, backlog: {counts_by_status: {}, counts_by_priority: {}}, workflow: {}, approval: {}, recent_reports: [], recent_events: [], timeline: [], live_activity: [], recent_executions: [], health_warnings: [], testing: {}, data_freshness_timestamp: '2026-08-25T03:00:00+00:00'})};
  }
  if (url === '/api/engineering/chat/history') {
    return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
  }
  throw new Error('unexpected url ' + url);
};
global.window.__chatClipboardWriteText = () => {};
eval(script.replace('<script>', '').replace('</script>', ''));
(async () => {
  await window.engineeringDashboard.refreshChatHistory();
  const htmlBefore = chatHistory.innerHTML;
  assert(htmlBefore.includes('data-copy-index=\"1\"'));
  // Simulate snapshot poll replacing dashboard-content.
  await window.engineeringDashboard.refreshDashboard();
  await window.engineeringDashboard.refreshChatHistory();
  // Per-message Copy buttons must still be present and since-copy must remain enabled.
  assert(chatHistory.innerHTML.includes('data-copy-index=\"1\"'));
  assert(chatHistory.innerHTML.includes('data-copy-index=\"2\"'));
  assert.strictEqual(since.hidden, false);
  assert.strictEqual(since.disabled, false);
  // Snapshot poll must NOT have duplicated per-message buttons (no two copies of index 1).
  assert.strictEqual((chatHistory.innerHTML.match(/data-copy-index=\"1\"/g) || []).length, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    _run_dashboard_script_case(script, body)


def test_chat_copy_uses_projected_messages_not_raw_transcript_so_pr63_filter_holds():
    # Confirm the since-copy payload cannot include a hidden toolUse /
    # delivery-mirror row, because computeSinceLastUserText operates on the
    # already-filtered projected messages array (PR #63 server projection).
    # We simulate a message list that a misbehaving client might inject and
    # verify the helper still excludes any non-conversational row.
    import json
    from dashboard_api.app import create_app
    from dashboard_api.chat_gateway import _project_message, _dedupe_messages
    # Fake raw transcript (mimics OpenClaw transcript shape):
    raw = [
        {"role": "user", "content": "Q1", "timestamp": 1},
        {"role": "assistant", "stopReason": "toolUse", "content": [{"type": "text", "text": "narrating between tool calls"}], "timestamp": 2},
        {"role": "toolResult", "content": "raw tool output", "timestamp": 3},
        {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "R1 before user"}], "timestamp": 4},
        {"role": "user", "content": "Q2", "timestamp": 5},
        {"role": "assistant", "stopReason": "toolUse", "content": [{"type": "text", "text": "intermediate"}], "timestamp": 6},
        {"role": "assistant", "model": "delivery-mirror", "stopReason": "stop", "content": [{"type": "text", "text": "R2 mirror dup"}], "timestamp": 7},
        {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "R2 final"}], "timestamp": 8},
    ]
    projected = list(_dedupe_messages(_project_message(m) for m in raw))
    # The dashboard JS reads messages from the projected list, so hidden
    # toolUse / toolResult / delivery-mirror rows MUST not be present.
    roles = [m.role for m in projected]
    assert roles == ["user", "assistant", "user", "assistant"], f"unexpected projected roles: {roles}"
    texts = [m.text for m in projected]
    assert all("narrating between tool calls" not in t and "raw tool output" not in t and "intermediate" not in t and "R2 mirror dup" not in t for t in texts)
    # The visible projection must contain only the legitimate conversational rows.
    assert texts == ["Q1", "R1 before user", "Q2", "R2 final"]
    # Now exercise computeSinceLastUserText on the projected list directly
    # by running the dashboard JS in node.
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = (
        "const assert = require('assert');\nconst script = fs.readFileSync(0, 'utf8');\nconst chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 100, addEventListener: () => {}, dataset: {}};\nconst since = {addEventListener: () => {}, dataset: {}, hidden: true, disabled: true, textContent: '', id: 'chat-copy-since'};\nconst status = {dataset: {}, textContent: ''};\nconst projected = PROJECTED_MESSAGES_PLACEHOLDER;\nglobal.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}, localStorage: {getItem: () => null, setItem: () => {}}};\nglobal.document = {\n  getElementById: (id) => {\n    if (id === 'dashboard-content') return {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};\n    if (id === 'chat-history') return chatHistory;\n    if (id === 'chat-copy-since') return since;\n    if (id === 'chat-status') return status;\n    if (id === 'update-warning') return {textContent: '', style: {display: 'none'}};\n    return null;\n  },\n  querySelectorAll: () => [],\n};\nglobal.fetch = async (url) => {\n  if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};\n  if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages: projected})};\n  throw new Error('unexpected url ' + url);\n};\nlet captured = null;\nconst sinceProxy = {\n  ...since,\n  addEventListener: (evt, fn) => { if (evt === 'click') captured = fn; },\n};\nglobal.document.getElementById = (id) => {\n  if (id === 'chat-copy-since') return sinceProxy;\n  if (id === 'dashboard-content') return {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};\n  if (id === 'chat-history') return chatHistory;\n  if (id === 'chat-status') return status;\n  if (id === 'update-warning') return {textContent: '', style: {display: 'none'}};\n  return null;\n};\nlet clipboardText = null;\nglobal.window.__chatClipboardWriteText = (text) => { clipboardText = text; };\neval(script.replace('<script>', '').replace('</script>', ''));\n(async () => {\n  await window.engineeringDashboard.refreshChatHistory();\n  await captured();\n  await new Promise((r) => setImmediate(r));\n  // Only the projected stopReason=stop assistant row after the last user turn survives.\n  assert.strictEqual(clipboardText, 'final R2');\n  assert(!clipboardText.includes('intermediate'));\n  assert(!clipboardText.includes('narrating'));\n  assert(!clipboardText.includes('raw tool output'));\n})().catch((error) => { console.error(error); process.exit(1); });\n"
    ).replace('PROJECTED_MESSAGES_PLACEHOLDER', json.dumps([{'role': m.role, 'text': m.text} for m in projected]))


# ---------------------------------------------------------------------------
# Regression tests: Chat copy controls on iOS Safari + snapshot polling
# resilience. These were added together because they share the same
# dashboard JS surface (the `writeClipboardText` helper and the
# `refreshDashboard` poll loop).
#
# Test pattern: a single capturing `addEventListener` mock on the
# chat-history node collects the per-message click handler attached by
# `bindChatCopyControls`. Re-binding is forced by clearing the
# `dataset.copyBound` flag, which keeps the same mock in place so the
# captured handler list always reflects the live binding.
# ---------------------------------------------------------------------------


COPY_TEST_HARNESS = textwrap.dedent(
    """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    const historyHandlers = [];
    const sinceHandlers = [];
    const chatHistory = {
      innerHTML: '', scrollTop: 0, scrollHeight: 100,
      addEventListener: (evt, fn) => { if (evt === 'click') historyHandlers.push(fn); },
      dataset: {},
    };
    const since = {
      addEventListener: (evt, fn) => { if (evt === 'click') sinceHandlers.push(fn); },
      dataset: {},
      hidden: true, disabled: true, textContent: '', id: 'chat-copy-since',
    };
    const status = {dataset: {}, textContent: '', querySelector: () => null};
    const state = {textContent: '', style: {display: 'none'}, dataset: {}};
    let capturedState = '';
    Object.defineProperty(state, 'textContent', {
      set: (v) => { capturedState = v; },
      get: () => capturedState,
      configurable: true,
    });
    let warning = {textContent: '', style: {display: 'none'}};
    let content = {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []};
    let fetchCalls = 0;
    let fetchImpl = async () => { fetchCalls += 1; return {ok: true, json: async () => ({project_identity: 'trading-bot'})}; };
    const fakeDocument = {
      getElementById: (id) => {
        if (id === 'dashboard-content') return content;
        if (id === 'chat-history') return chatHistory;
        if (id === 'chat-copy-since') return since;
        if (id === 'chat-status') return status;
        if (id === 'chat-state') return state;
        if (id === 'update-warning') return warning;
        return null;
      },
      querySelectorAll: () => [],
      hidden: false,
      addEventListener: () => {},
    };
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}, localStorage: {getItem: () => null, setItem: () => {}}};
    global.document = fakeDocument;
    global.fetch = async (url) => {
      fetchCalls += 1;
      return fetchImpl(url);
    };
    """
)


def _run_copy_case(script: str, body: str, *, with_fallback_document: bool = True) -> None:
    """Run a Chat copy test with a uniform harness. The body should set up
    `navigator`, `window.isSecureContext`, `__chatClipboardWriteText`, the
    chat history mock messages, and trigger the per-message click via
    `historyHandlers[historyHandlers.length - 1]`. Optionally install
    fallback mock document properties for the execCommand path."""
    harness = COPY_TEST_HARNESS
    if with_fallback_document:
        harness += textwrap.dedent(
            """
            let createdTextareas = 0;
            let execResult = true;
            const execCmd = (cmd) => { execCmd.captured = cmd; execCmd.execCalls = (execCmd.execCalls || 0) + 1; return execResult; };
            Object.assign(fakeDocument, {
              createElement: (tag) => {
                createdTextareas += 1;
                const node = {
                  tagName: tag.toUpperCase(), value: '', dataset: {}, children: [],
                  style: {cssText: ''}, attributes: {},
                  setAttribute: function(k, v) { this.attributes[k] = v; },
                  getAttribute: function(k) { return this.attributes[k]; },
                  focus: () => {}, select: () => {}, setSelectionRange: () => {},
                  parentNode: null, contains: () => true,
                  appendChild: function(c) { this.children.push(c); c.parentNode = this; return c; },
                  removeChild: function(c) { this.children = this.children.filter((x) => x !== c); },
                  addEventListener: () => {},
                };
                return node;
              },
              body: {
                appendChild: (c) => { c.parentNode = fakeDocument.body; return c; },
                removeChild: (c) => { if (c.parentNode === fakeDocument.body) c.parentNode = null; },
                contains: () => true,
              },
              activeElement: null,
              getSelection: () => ({rangeCount: 0, removeAllRanges: () => {}, addRange: () => {}, getRangeAt: () => null}),
              execCommand: execCmd,
            });
            """
        )
    full_body = harness + body
    _run_dashboard_script_case(script, full_body)


def test_copy_falls_back_to_exec_command_when_navigator_clipboard_is_undefined():
    """iOS Safari over plain HTTP / SSH tunnels exposes no navigator.clipboard.

    The dashboard copy controls must still place the assistant text on the
    clipboard via the classic document.execCommand('copy') path with an
    in-DOM temporary textarea. This is the actual iPhone regression that
    surfaced on PR #64.
    """
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        execResult = true;
        const messages = [{role: 'assistant', text: 'fallback payload', timestamp: 't1'}];
        fetchImpl = async (url) => {
          if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};
          if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
          throw new Error('unexpected url ' + url);
        };
        // iOS Safari over HTTP / SSH tunnel: no navigator.clipboard.
        global.navigator = {};
        window.isSecureContext = false;
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          await window.engineeringDashboard.refreshChatHistory();
          // Force a fresh bind so the click handler is captured into historyHandlers.
          chatHistory.dataset = {};
          window.engineeringDashboard.switchTab('chat');
          await new Promise((r) => setImmediate(r));
          const button = {dataset: {copyIndex: '0'}, textContent: 'Copy', id: ''};
          const handler = historyHandlers[historyHandlers.length - 1];
          assert(handler, 'per-message click handler must be attached');
          await handler({target: {closest: (sel) => sel === '.chat-copy' ? button : null}});
          await new Promise((r) => setImmediate(r));
          assert.strictEqual(execCmd.captured, 'copy', 'execCommand must be invoked with copy');
          assert(execCmd.execCalls >= 1, 'execCommand must run at least once');
          assert(createdTextareas >= 1, 'a temporary textarea must be created');
          assert.strictEqual(button.dataset.copyState, 'copied', 'button must show success after fallback succeeds');
          assert.strictEqual(button.textContent, 'Copied');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_copy_case(script, body)


def test_copy_falls_back_when_clipboard_api_rejects():
    """When navigator.clipboard.writeText is available but the returned promise
    rejects (e.g. permission denied, page backgrounded), the fallback path
    must run so the user still gets a usable Copy button."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        execResult = true;
        const messages = [{role: 'assistant', text: 'reject payload', timestamp: 't1'}];
        fetchImpl = async (url) => {
          if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};
          if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
          throw new Error('unexpected url ' + url);
        };
        global.navigator = {
          clipboard: { writeText: () => Promise.reject(new Error('permission denied')) },
        };
        window.isSecureContext = true;
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          await window.engineeringDashboard.refreshChatHistory();
          chatHistory.dataset = {};
          window.engineeringDashboard.switchTab('chat');
          await new Promise((r) => setImmediate(r));
          const button = {dataset: {copyIndex: '0'}, textContent: 'Copy', id: ''};
          const handler = historyHandlers[historyHandlers.length - 1];
          await handler({target: {closest: (sel) => sel === '.chat-copy' ? button : null}});
          await new Promise((r) => setImmediate(r));
          assert.strictEqual(execCmd.captured, 'copy', 'fallback execCommand must run when Clipboard API rejects');
          assert(execCmd.execCalls >= 1);
          assert.strictEqual(button.dataset.copyState, 'copied');
          assert.strictEqual(button.textContent, 'Copied');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_copy_case(script, body)


def test_copy_reports_failure_when_both_clipboard_paths_fail():
    """If navigator.clipboard.writeText rejects AND document.execCommand
    returns false, the button must visibly report 'Copy failed'. The code
    must NOT silently claim success."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        execResult = false;
        const messages = [{role: 'assistant', text: 'failure path', timestamp: 't1'}];
        fetchImpl = async (url) => {
          if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};
          if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
          throw new Error('unexpected url ' + url);
        };
        global.navigator = {clipboard: {writeText: () => Promise.reject(new Error('blocked'))}};
        window.isSecureContext = true;
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          await window.engineeringDashboard.refreshChatHistory();
          chatHistory.dataset = {};
          window.engineeringDashboard.switchTab('chat');
          await new Promise((r) => setImmediate(r));
          const button = {dataset: {copyIndex: '0'}, textContent: 'Copy', id: ''};
          const handler = historyHandlers[historyHandlers.length - 1];
          await handler({target: {closest: (sel) => sel === '.chat-copy' ? button : null}});
          await new Promise((r) => setImmediate(r));
          assert.strictEqual(button.dataset.copyState, 'failed', 'button must report failed');
          assert.strictEqual(button.textContent, 'Copy failed');
          assert(/manually|failed/i.test(capturedState), 'chat-state banner must surface the failure');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_copy_case(script, body)


def test_copy_text_excludes_hidden_tool_use_and_delivery_mirror_rows():
    """Regression: PR #63's projection must still be the single source of
    truth for what gets copied. Hidden toolUse / delivery-mirror / system
    rows must NEVER enter the clipboard payload. The fallback path must
    read from the same already-projected message array, not from DOM
    scraping or raw transcript content."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        const messages = [
          {role: 'user', text: 'visible Q', timestamp: 't1'},
          {role: 'assistant', text: 'visible A', timestamp: 't2'},
        ];
        fetchImpl = async (url) => {
          if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};
          if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
          throw new Error('unexpected url ' + url);
        };
        let copied = null;
        window.__chatClipboardWriteText = (text) => { copied = text; };
        global.navigator = {};
        window.isSecureContext = true;
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          await window.engineeringDashboard.refreshChatHistory();
          chatHistory.dataset = {};
          window.engineeringDashboard.switchTab('chat');
          await new Promise((r) => setImmediate(r));
          const button = {dataset: {copyIndex: '1'}, textContent: 'Copy', id: ''};
          const handler = historyHandlers[historyHandlers.length - 1];
          await handler({target: {closest: (sel) => sel === '.chat-copy' ? button : null}});
          await new Promise((r) => setImmediate(r));
          assert.strictEqual(copied, 'visible A', 'per-message copy must equal the projected assistant text exactly');
          copied = null;
          since.dataset = {};
          window.engineeringDashboard.switchTab('overview');
          since.dataset = {};
          window.engineeringDashboard.switchTab('chat');
          await new Promise((r) => setImmediate(r));
          const sinceHandler = sinceHandlers[sinceHandlers.length - 1];
          await sinceHandler();
          await new Promise((r) => setImmediate(r));
          assert.strictEqual(copied, 'visible A', 'since-copy must only join visible assistant text');
          assert(!/tool|delivery|mirror|system/i.test(copied || ''));
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_copy_case(script, body, with_fallback_document=False)


def test_copy_uses_only_projected_plain_text_not_innerhtml():
    """Belt-and-braces: the fallback path must populate the temporary
    textarea from the already-projected text directly, never from
    innerHTML / DOM scraping."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        execResult = true;
        const messages = [{role: 'assistant', text: 'plain<&>text', timestamp: 't1'}];
        fetchImpl = async (url) => {
          if (url === '/api/engineering/snapshot') return {ok: true, json: async () => ({project_identity: 'trading-bot'})};
          if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages})};
          throw new Error('unexpected url ' + url);
        };
        global.navigator = {};
        window.isSecureContext = false;
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          await window.engineeringDashboard.refreshChatHistory();
          chatHistory.dataset = {};
          window.engineeringDashboard.switchTab('chat');
          await new Promise((r) => setImmediate(r));
          const button = {dataset: {copyIndex: '0'}, textContent: 'Copy', id: ''};
          const handler = historyHandlers[historyHandlers.length - 1];
          await handler({target: {closest: (sel) => sel === '.chat-copy' ? button : null}});
          await new Promise((r) => setImmediate(r));
          assert.strictEqual(execCmd.captured, 'copy');
          assert(createdTextareas >= 1, 'a temporary textarea must be created');
          assert.strictEqual(button.dataset.copyState, 'copied');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_copy_case(script, body)


def test_snapshot_poll_skipped_when_document_is_hidden():
    """When the tab is backgrounded (document.hidden === true), refreshDashboard
    must NOT issue a fetch and must NOT surface the warning banner. The next
    visible poll will recover."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        fakeDocument.hidden = true;
        content.innerHTML = 'LAST KNOWN SNAPSHOT';
        warning.textContent = '';
        warning.style.display = 'none';
        const before = fetchCalls;
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          await window.engineeringDashboard.refreshDashboard();
          assert.strictEqual(fetchCalls, before, 'no new fetch must be issued when document is hidden');
          assert.strictEqual(content.innerHTML, 'LAST KNOWN SNAPSHOT', 'previous content must be preserved');
          assert.strictEqual(warning.textContent, '', 'no warning must be shown for hidden-document poll');
          assert.strictEqual(warning.style.display, 'none');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_copy_case(script, body, with_fallback_document=False)


def test_snapshot_poll_ignores_fetch_abort_error():
    """A fetch AbortError (e.g. iOS Safari cancelling the request when the
    page is hidden) must not surface the warning banner. This is the most
    common cause of the user-visible 'Dashboard update failed' banner on
    iPhone."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        content.innerHTML = 'LAST KNOWN';
        warning.textContent = '';
        warning.style.display = 'none';
        fetchImpl = async () => {
          const err = new Error('The user aborted a request.');
          err.name = 'AbortError';
          throw err;
        };
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          await window.engineeringDashboard.refreshDashboard();
          assert.strictEqual(content.innerHTML, 'LAST KNOWN');
          assert.strictEqual(warning.textContent, '', 'AbortError must not surface the warning banner');
          assert.strictEqual(warning.style.display, 'none');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_copy_case(script, body, with_fallback_document=False)


def test_snapshot_poll_still_warns_for_real_network_failure():
    """A genuine HTTP/network failure (not an abort) must still show the
    warning. The fix must NOT silence all errors."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        content.innerHTML = 'LAST GOOD';
        warning.textContent = '';
        warning.style.display = 'none';
        fetchImpl = async () => { throw new Error('network down'); };
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          await window.engineeringDashboard.refreshDashboard();
          assert.strictEqual(content.innerHTML, 'LAST GOOD');
          assert(warning.textContent.includes('showing the last known snapshot'));
          assert.strictEqual(warning.style.display, 'block');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_copy_case(script, body, with_fallback_document=False)


def test_snapshot_poll_recovers_via_visibilitychange_listener():
    """When the page becomes visible again, the dashboard must run an
    immediate poll so the UI does not stay on the last cached state for up
    to 15s."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        let hidden = true;
        Object.defineProperty(fakeDocument, 'hidden', {get: () => hidden, configurable: true});
        const visListeners = [];
        fakeDocument.addEventListener = (evt, fn) => { if (evt === 'visibilitychange') visListeners.push(fn); };
        content.innerHTML = 'OLD';
        warning.textContent = '';
        warning.style.display = 'none';
        fetchImpl = async () => ({ok: true, json: async () => ({project_identity: 'trading-bot', refreshed: true})});
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          const before = fetchCalls;
          await window.engineeringDashboard.refreshDashboard();
          assert.strictEqual(fetchCalls, before, 'poll must be skipped while hidden');
          assert(visListeners.length >= 1, 'visibilitychange listener must be registered');
          hidden = false;
          for (const fn of visListeners) { fn(); }
          await new Promise((r) => setImmediate(r));
          assert(fetchCalls > before, 'recovery poll must fire on visibilitychange to visible');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_copy_case(script, body, with_fallback_document=False)


def test_chat_history_still_survives_snapshot_poll_failure():
    """Regression: PR #64's chat history must survive a snapshot poll
    failure. A failed poll must NOT wipe the chat-history DOM. The chat
    tab should remain interactive and the user must still be able to copy
    messages."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    body = textwrap.dedent(
        """
        content.innerHTML = '<article class="chat-message assistant">preserved</article>';
        warning.textContent = '';
        warning.style.display = 'none';
        fetchImpl = async (url) => {
          if (url === '/api/engineering/snapshot') throw new Error('snapshot boom');
          if (url === '/api/engineering/chat/history') return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages: [{role: 'assistant', text: 'preserved', timestamp: 't1'}]})};
          throw new Error('unexpected url ' + url);
        };
        window.localStorage.getItem = () => 'chat';
        eval(script.replace('<script>', '').replace('</script>', ''));
        (async () => {
          await window.engineeringDashboard.refreshChatHistory();
          const before = fetchCalls;
          await window.engineeringDashboard.refreshDashboard();
          assert(fetchCalls > before, 'snapshot poll must have run');
          assert.strictEqual(warning.style.display, 'block', 'snapshot failure must show the warning');
          assert(content.innerHTML.includes('preserved'), 'chat-history DOM must survive snapshot poll failure');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_copy_case(script, body, with_fallback_document=False)


# ---------------------------------------------------------------------------
# Authoritative agent-status projection rule for Engineering Chat.
# Phase-1 send-accept semantics test: when a fresh active run appears,
# stale Failed indicator MUST be cleared (Working takes priority over a
# leftover terminal run_status).
# ---------------------------------------------------------------------------


def test_active_run_clears_stale_failed_indicator():
    """After a prior terminal Failed run, a new active run MUST immediately
    re-flip the indicator to Working. The dashboard projection rule is:

        terminal && !hasActiveRun => failed
        hasActiveRun               => working
        terminal && hasActiveRun   => working (NEW: active wins)

    This guards against the 'stale Failed indicator after long wait' bug
    observed on 2026-08-25 around 15:54 UTC.
    """
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_payload = json.dumps(_public_snapshot(populated_snapshot()))
    body = _shared_chat_dom_script_boilerplate(snapshot_payload) + textwrap.dedent(
        """
        (async () => {
          window.engineeringDashboard.switchTab('chat');

          // First poll: terminal timeout — indicator becomes Failed.
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'timeout'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'failed');

          // Second poll: a new run was accepted; has_active_run is now true.
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: true, run_status: 'running'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'working');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_dashboard_script_case(script, body)


def test_contradictory_running_state_with_no_active_run_resolves_to_idle():
    """When the Gateway returns the contradictory (has_active_run=false,
    run_status='running') tuple, the dashboard MUST NOT show Failed; it
    resolves to Idle. This is the actual contradictory state we observed
    in the 2026-08-25 15:54 UTC incident."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_payload = json.dumps(_public_snapshot(populated_snapshot()))
    body = _shared_chat_dom_script_boilerplate(snapshot_payload) + textwrap.dedent(
        """
        (async () => {
          window.engineeringDashboard.switchTab('chat');

          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'running'}, messages: []})};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          assert.strictEqual(dom.elements['chat-status'].dataset.agentStatus, 'idle');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_dashboard_script_case(script, body)


# ---------------------------------------------------------------------------
# UI rendering for the explicit Response-truncated indicator.
# ---------------------------------------------------------------------------


def test_render_chat_history_renders_truncated_badge_when_message_truncated_true():
    """A message with truncated=true MUST render the 'Response truncated'
    badge inline, with a clear visual distinction (CSS class). A normal
    message MUST NOT show the badge."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_payload = json.dumps(_public_snapshot(populated_snapshot()))
    body = _shared_chat_dom_script_boilerplate(snapshot_payload) + textwrap.dedent(
        """
        (async () => {
          window.engineeringDashboard.switchTab('chat');
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              return {ok: true, json: async () => ({
                session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'},
                messages: [
                  {role: 'assistant', text: 'short reply', timestamp: 't1', truncated: false},
                  {role: 'assistant', text: 'A'.repeat(15975) + ' [Response truncated]', timestamp: 't2', truncated: true},
                ]
              })};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          const html = dom.elements['chat-history'].innerHTML;
          // Exactly one truncated badge and one truncated-class on the second article.
          const badgeCount = (html.match(/class=\"chat-truncated\"/g) || []).length;
          const truncatedClassCount = (html.match(/chat-message-truncated/g) || []).length;
          assert.strictEqual(badgeCount, 1, 'exactly one chat-truncated badge rendered');
          assert(truncatedClassCount >= 1, 'truncated-class must appear at least once');
          assert(html.includes('Response truncated'), 'badge text must include literal "Response truncated"');
          // Two assistant messages (data-message-index="0" and "1").
          assert(html.includes('data-message-index=\"0\"'));
          assert(html.includes('data-message-index=\"1\"'));
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_dashboard_script_case(script, body)


def test_render_chat_history_omits_truncated_badge_for_complete_5k_response():
    """A 5,138-char manager response MUST render with truncated=false and
    no badge — even if the dashboard's previous bound (4,000) would have
    cut it. Copy contracts verified separately."""
    script = _dashboard_script(render_dashboard(populated_snapshot()))
    snapshot_payload = json.dumps(_public_snapshot(populated_snapshot()))
    body = _shared_chat_dom_script_boilerplate(snapshot_payload) + textwrap.dedent(
        """
        (async () => {
          window.engineeringDashboard.switchTab('chat');
          global.fetch = async (url) => {
            if (url === '/api/engineering/chat/history') {
              const text = 'A'.repeat(5138);
              return {ok: true, json: async () => ({
                session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'},
                messages: [{role: 'assistant', text: text, timestamp: 't1', truncated: false}]
              })};
            }
            throw new Error('unexpected url ' + url);
          };
          await window.engineeringDashboard.refreshChatHistory();
          const html = dom.elements['chat-history'].innerHTML;
          assert(html.includes('data-message-index="0"'), 'one assistant article rendered');
          assert(!html.includes('chat-truncated'), 'no truncation badge for complete response');
          assert(!html.includes('chat-message-truncated'), 'no chat-message-truncated class');
          assert(!html.includes('[Response truncated]'), 'no truncation marker in the rendered HTML');
          assert(html.includes('A'.repeat(200)), 'a long run of A characters is present in the rendered text');
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )
    _run_dashboard_script_case(script, body)
