from __future__ import annotations

import ast
import json
import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from dashboard_api.app import CHAT_HISTORY_ROUTE, DASHBOARD_ROUTE, SNAPSHOT_ROUTE, create_app, render_dashboard
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

    assert routes == {SNAPSHOT_ROUTE: {"GET"}, CHAT_HISTORY_ROUTE: {"GET"}, DASHBOARD_ROUTE: {"GET"}}
    client = TestClient(app)
    for method in (client.post, client.put, client.patch, client.delete):
        assert method(SNAPSHOT_ROUTE).status_code == 405
        assert method(CHAT_HISTORY_ROUTE).status_code == 405
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
    assert {SNAPSHOT_ROUTE, CHAT_HISTORY_ROUTE, DASHBOARD_ROUTE}.issubset({route.path for route in app.routes})
    assert {route.path for route in app.routes} == {SNAPSHOT_ROUTE, CHAT_HISTORY_ROUTE, DASHBOARD_ROUTE}


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


def test_chat_history_endpoint_has_no_slice_one_send_or_control_routes():
    client = TestClient(create_app(StaticProvider(populated_snapshot()), StaticChatHistoryProvider({"session": {"agent": "trading-manager", "status": "available"}, "messages": []})))

    assert client.post("/api/engineering/chat/send", json={"message": "hello"}).status_code == 404
    assert client.post("/api/engineering/chat/abort", json={}).status_code == 404
    assert client.post("/api/engineering/chat/history", json={}).status_code == 405
    route_methods = {(route.path, tuple(sorted(route.methods or ()))) for route in client.app.routes}
    assert ("/api/engineering/chat/history", ("GET",)) in route_methods
    assert all("/api/engineering/chat/send" != path for path, _methods in route_methods)
    assert all("/api/engineering/chat/abort" != path for path, _methods in route_methods)


def test_dashboard_chat_tab_exists_without_send_box_and_existing_tabs_remain():
    html = render_dashboard(populated_snapshot())

    for tab in ("overview", "activity", "backlog", "timeline", "reports", "health", "chat"):
        assert f"data-tab='{tab}'" in html
        assert f"data-tab-panel='{tab}'" in html
    assert "aria-selected='true'>Overview" in html
    assert "id='tab-chat' class='tab-panel' role='tabpanel' data-tab-panel='chat' hidden" in html
    assert "Read-only view of the existing OpenClaw trading-manager conversation" in html
    assert "<textarea" not in html.lower()
    assert "type='submit'" not in html.lower()
    assert "/api/engineering/chat/send" not in html
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
          assert.strictEqual(chatHistory.innerHTML, '');
          assert(chatState.textContent.length < 80);
        })().catch((error) => { console.error(error); process.exit(1); });
        """
    )

    _run_dashboard_script_case(script, body)
