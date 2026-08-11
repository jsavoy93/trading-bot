from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import subprocess

from fastapi.testclient import TestClient

from dashboard_api.app import DASHBOARD_ROUTE, SNAPSHOT_ROUTE, create_app, create_default_read_model
from dashboard_api.providers import (
    EngineeringDashboardProviderConfig,
    GitRepositorySummaryReader,
    ReadOnlyEngineeringEventStore,
    create_engineering_dashboard_provider,
    create_engineering_query_service,
)
from engineering.event_store import EngineeringEventStore
from engineering.models import WorkflowState
from engineering.query_service import EngineeringQueryService
from engineering.workflow_store import StoredWorkflow, WorkflowStore


BACKLOG = """### ENGDASH-003 — Query service provider

Status: IN_PROGRESS
Owner: dashboard-agent
Priority: P1

Acceptance criteria:

- Provider is wired.
- Routes stay read-only.

### DASH-001 — Existing settings inventory

Status: TODO
Owner: dashboard-agent
Priority: P1
"""


def fixed_clock() -> datetime:
    return datetime(2026, 8, 4, 23, 45, tzinfo=UTC)


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=path, check=True, capture_output=True, text=True)


def write_backlog(path: Path) -> Path:
    backlog = path / "AGENT_BACKLOG.md"
    backlog.write_text(BACKLOG, encoding="utf-8")
    return backlog


def build_config(tmp_path: Path) -> EngineeringDashboardProviderConfig:
    init_git_repo(tmp_path)
    backlog = write_backlog(tmp_path)
    state_path = tmp_path / ".git" / "engineering-workflow.json"
    event_path = tmp_path / ".agent-state" / "engineering-events.sqlite3"
    events = EngineeringEventStore(event_path)
    workflow_store = WorkflowStore(state_path, event_store=events)
    workflow_store.save(StoredWorkflow("ENGDASH-003", "agent/engdash-003-query-service-provider", WorkflowState.QA))
    with sqlite3.connect(event_path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "2026-08-04_234500_ENGDASH-003.md").write_text(
        "# ENGDASH-003 Report\n\nTask: ENGDASH-003\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Add engineering dashboard fixtures"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return EngineeringDashboardProviderConfig(
        repo_root=tmp_path,
        backlog_path=backlog,
        workflow_state_path=state_path,
        event_store_path=event_path,
        audit_archive_root=None,
        project_identity="trading-bot",
        clock=fixed_clock,
    )


def test_engineering_query_service_backed_provider_construction(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    query_service = create_engineering_query_service(config)
    provider = create_engineering_dashboard_provider(config)

    assert isinstance(query_service, EngineeringQueryService)
    assert hasattr(query_service.event_store, "list_events")
    assert hasattr(query_service.event_store, "pause_state")
    snapshot = provider.snapshot()

    assert snapshot.project_identity == "trading-bot"
    assert snapshot.repository.root == str(tmp_path.resolve())
    assert snapshot.repository.branch == "main"
    assert snapshot.repository.is_clean is True
    assert snapshot.repository.latest_commit_subject == "Add engineering dashboard fixtures"
    assert snapshot.backlog.active_task_id == "ENGDASH-003"
    assert snapshot.backlog.status == "IN_PROGRESS"
    assert snapshot.workflow.active is True
    assert snapshot.workflow.stage == "QA"
    assert snapshot.workflow.feature_branch == "agent/engdash-003-query-service-provider"
    assert snapshot.recent_events
    assert snapshot.recent_reports[0].task_id == "ENGDASH-003"
    assert snapshot.data_freshness_timestamp == "2026-08-04T23:45:00+00:00"


def test_provider_missing_sources_degrade_to_bounded_warnings(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    config = EngineeringDashboardProviderConfig(
        repo_root=tmp_path,
        backlog_path=tmp_path / "missing-backlog.md",
        workflow_state_path=tmp_path / ".git" / "missing-workflow.json",
        event_store_path=tmp_path / ".agent-state" / "missing-events.sqlite3",
        audit_archive_root=None,
        clock=fixed_clock,
    )
    provider = create_engineering_dashboard_provider(config)

    snapshot = provider.snapshot()

    assert snapshot.repository.root == str(tmp_path.resolve())
    assert snapshot.workflow.active is False
    assert any(warning.source == "query_service" for warning in snapshot.health_warnings)
    public = TestClient(create_app(provider)).get(SNAPSHOT_ROUTE).json()
    assert all(warning["detail"] is None for warning in public["health_warnings"])


def test_source_exceptions_are_bounded_and_not_publicly_leaked(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    secret = "SECRET_TOKEN_FROM_EXCEPTION"
    config = EngineeringDashboardProviderConfig(
        repo_root=tmp_path,
        backlog_path=tmp_path / secret / "AGENT_BACKLOG.md",
        workflow_state_path=tmp_path / ".git" / "missing-workflow.json",
        event_store_path=tmp_path / ".agent-state" / "missing-events.sqlite3",
        audit_archive_root=None,
        clock=fixed_clock,
    )
    provider = create_engineering_dashboard_provider(config)
    client = TestClient(create_app(provider))

    json_response = client.get(SNAPSHOT_ROUTE)
    html_response = client.get(DASHBOARD_ROUTE)

    assert json_response.status_code == 200
    assert html_response.status_code == 200
    assert secret not in json_response.text
    assert secret not in html_response.text
    assert "Traceback" not in json_response.text
    assert "Traceback" not in html_response.text


def test_default_app_uses_real_provider_and_preserves_exact_route_surface() -> None:
    provider = create_default_read_model()
    app = create_app(provider)
    routes = {route.path: route.methods for route in app.routes if hasattr(route, "methods")}
    client = TestClient(app)

    assert routes == {SNAPSHOT_ROUTE: {"GET"}, DASHBOARD_ROUTE: {"GET"}}
    body = client.get(SNAPSHOT_ROUTE).json()
    assert body["repository"]["root"] != "unavailable"
    assert client.get(DASHBOARD_ROUTE).status_code == 200
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    for method in (client.post, client.put, client.patch, client.delete):
        assert method(SNAPSHOT_ROUTE).status_code == 405
        assert method(DASHBOARD_ROUTE).status_code == 405


def test_read_only_event_store_does_not_create_missing_database(tmp_path: Path) -> None:
    event_path = tmp_path / ".agent-state" / "missing.sqlite3"
    store = ReadOnlyEngineeringEventStore(event_path)

    assert store.list_events() == ()
    assert store.pause_state()["paused"] is False
    assert not event_path.exists()


def test_git_repository_summary_reader_is_deterministic_and_bounded(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    first = GitRepositorySummaryReader(tmp_path).summary()
    second = GitRepositorySummaryReader(tmp_path).summary()

    assert first == second
    assert first.is_clean is False
    assert first.dirty_paths == ("dirty.txt",)
    assert first.sync_state == "unknown"


def test_real_provider_sources_use_no_trading_or_brokerage_imports() -> None:
    forbidden_roots = {"alpaca", "brokerage", "src", "dashboard"}
    for path in Path("dashboard_api").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".", 1)[0] for alias in node.names}
                assert imported.isdisjoint(forbidden_roots)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_roots
