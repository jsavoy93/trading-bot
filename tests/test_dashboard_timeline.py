from __future__ import annotations

import ast
from pathlib import Path
import sqlite3
from typing import Mapping

from fastapi.testclient import TestClient

from dashboard_api.app import DASHBOARD_ROUTE, SNAPSHOT_ROUTE, create_app, render_dashboard
from dashboard_api.engineering_read_model import (
    ApprovalSummary,
    BacklogSummary,
    DashboardSnapshot,
    PullRequestSummary,
    RepositorySummary,
    WorkflowSummary,
)
from dashboard_api.providers import (
    EngineeringDashboardProviderConfig,
    create_engineering_dashboard_provider,
    create_engineering_query_service,
)
from engineering.adapters import WorkflowAdapter
from engineering.engineering_events import EngineeringEvent, EventSeverity, EventType, build_event
from engineering.event_store import EngineeringEventStore, StoredEvent
from engineering.query_service import EngineeringQueryService
from engineering.workflow_store import StoredWorkflow, WorkflowStore


BACKLOG = """### ENGDASH-005 — Engineering Timeline and Historical Activity

Status: IN_PROGRESS
Owner: dashboard-agent
Priority: P1

Acceptance criteria:

- Timeline works.

### NEXT-001 — Next task

Status: TODO
Owner: dashboard-agent
Priority: P2
"""


class FakeEventSource:
    def __init__(self, events: tuple[StoredEvent, ...] = ()):
        self.events = events

    def list_events(self, *, limit: int = 100) -> tuple[StoredEvent, ...]:
        return self.events[-limit:]

    def pause_state(self) -> dict[str, object]:
        return {
            "revision": 0,
            "paused": False,
            "actor": "",
            "reason": "",
            "updated_at": "1970-01-01T00:00:00+00:00",
        }


class FakeWorkflowAdapter(WorkflowAdapter):
    def __init__(self, store: WorkflowStore):
        self.store = store
        self.calls = 0

    def workflow_store(self) -> WorkflowStore:
        self.calls += 1
        return self.store

    def event_store(self) -> EngineeringEventStore:  # pragma: no cover - not used by query service
        raise AssertionError("query service should not request event store from workflow adapter")

    def archive_completed(self, workflow: StoredWorkflow) -> Path:  # pragma: no cover - not used
        raise AssertionError("query service should not archive workflows")


class StaticProvider:
    def __init__(self, snapshot: DashboardSnapshot):
        self.snapshot_value = snapshot

    def snapshot(self) -> DashboardSnapshot:
        return self.snapshot_value


def write_backlog(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "AGENT_BACKLOG.md"
    path.write_text(BACKLOG, encoding="utf-8")
    return path


def config(tmp_path: Path) -> EngineeringDashboardProviderConfig:
    return EngineeringDashboardProviderConfig(
        repo_root=tmp_path,
        backlog_path=write_backlog(tmp_path),
        workflow_state_path=tmp_path / "engineering-workflow.json",
        event_store_path=tmp_path / "engineering-events.sqlite3",
        workflow_report_dir=tmp_path / "reports",
        audit_archive_root=None,
    )


def event(
    sequence: int,
    occurred_at: object,
    event_id: str | None = None,
    payload: Mapping[str, object] | None = None,
) -> StoredEvent:
    return StoredEvent(
        sequence=sequence,
        event=EngineeringEvent(
            event_id=event_id or f"event-{sequence:03d}",
            event_type=EventType.WORKFLOW_TRANSITION,
            severity=EventSeverity.INFO,
            occurred_at=occurred_at,  # type: ignore[arg-type]
            task_id="ENGDASH-005",
            payload=dict(payload or {}),
        ),
    )


def service(tmp_path: Path, events: tuple[StoredEvent, ...]) -> EngineeringQueryService:
    workflow_store = WorkflowStore(tmp_path / "workflow.json")
    return EngineeringQueryService(
        event_source=FakeEventSource(events),
        workflow_source=workflow_store,
        backlog_path=write_backlog(tmp_path),
    )


def snapshot_with_events(events: tuple[Mapping[str, object], ...]) -> DashboardSnapshot:
    return DashboardSnapshot(
        project_identity="trading-bot",
        repository=RepositorySummary(root="/repo", branch="main", is_clean=True, sync_state="up_to_date"),
        backlog=BacklogSummary(None, None, None, None, None, {}, {}),
        workflow=WorkflowSummary(False, None, None, None, None, None, None, None),
        approval=ApprovalSummary(False, None, None, None, None),
        latest_execution_result=None,
        latest_test_result=None,
        latest_commit=None,
        pull_request=PullRequestSummary(None, None, None, None, None, None, available=False),
        recent_events=events,
        recent_reports=(),
        health_warnings=(),
        data_freshness_timestamp="2026-08-10T00:00:00+00:00",
    )


def test_provider_requires_explicit_config() -> None:
    try:
        create_engineering_dashboard_provider(None)
    except (TypeError, ValueError):
        return
    raise AssertionError("provider construction must require explicit config")


def test_no_cwd_fallback(tmp_path: Path) -> None:
    cfg = EngineeringDashboardProviderConfig(repo_root=tmp_path)
    try:
        create_engineering_query_service(cfg)
    except ValueError as exc:
        assert "explicit non-None" in str(exc)
    else:
        raise AssertionError("missing explicit paths should fail")


def test_no_discover_repo_root_in_path() -> None:
    source = Path("dashboard_api/providers.py").read_text(encoding="utf-8")
    assert "def _discover_repo_root" not in source
    assert "Path.cwd" not in source


def test_event_store_path_from_config(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    query_service = create_engineering_query_service(cfg)

    assert getattr(query_service.event_store, "path") == cfg.event_store_path


def test_no_trading_runtime_imports() -> None:
    forbidden_roots = {"src", "trading_bot", "alpaca", "brokerage", "dashboard"}
    for path in (Path("dashboard_api/providers.py"), Path("dashboard_api/app.py"), Path("engineering/query_service.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".", 1)[0] for alias in node.names}
                assert imported.isdisjoint(forbidden_roots)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_roots


def test_timeline_bounded(tmp_path: Path) -> None:
    timeline = service(tmp_path, tuple(event(index, f"2026-08-10T00:{index:02d}:00+00:00") for index in range(6))).snapshot(
        timeline_limit=3
    )["timeline"]

    assert len(timeline) == 3


def test_timeline_deterministic_order_ascending_occurred_at(tmp_path: Path) -> None:
    timeline = service(
        tmp_path,
        (
            event(1, "2026-08-10T00:03:00+00:00"),
            event(2, "2026-08-10T00:01:00+00:00"),
            event(3, "2026-08-10T00:02:00+00:00"),
        ),
    ).snapshot()["timeline"]

    assert [item["sequence"] for item in timeline] == [2, 3, 1]


def test_timeline_empty_source_graceful(tmp_path: Path) -> None:
    assert service(tmp_path, ()).snapshot()["timeline"] == []


def test_timeline_multiple_events(tmp_path: Path) -> None:
    timeline = service(
        tmp_path,
        (event(1, "2026-08-10T00:01:00+00:00"), event(2, "2026-08-10T00:02:00+00:00")),
    ).snapshot()["timeline"]

    assert [item["event_id"] for item in timeline] == ["event-001", "event-002"]


def test_timeline_html_escaping() -> None:
    html = render_dashboard(
        snapshot_with_events(
            (
                {
                    "type": "workflow.transition<script>",
                    "task_id": "ENGDASH-005&bad",
                    "occurred_at": "2026-08-10T00:00:00+00:00",
                    "payload": {"next_action": "Use <safe> & escaped text"},
                },
            )
        )
    )

    assert "workflow.transition&lt;script&gt;" in html
    assert "ENGDASH-005&amp;bad" in html
    assert "&lt;safe&gt; &amp; escaped text" in html
    assert "<safe>" not in html
    assert "<script>" not in html


def test_timeline_out_of_order_occurred_at(tmp_path: Path) -> None:
    timeline = service(
        tmp_path,
        (event(10, "2026-08-10T00:10:00+00:00"), event(1, "2026-08-10T00:01:00+00:00")),
    ).snapshot()["timeline"]

    assert [item["sequence"] for item in timeline] == [1, 10]


def test_timeline_identical_timestamps_tiebreaker(tmp_path: Path) -> None:
    timeline = service(
        tmp_path,
        (
            event(1, "2026-08-10T00:00:00+00:00", "b"),
            event(3, "2026-08-10T00:00:00+00:00", "a"),
            event(2, "2026-08-10T00:00:00+00:00", "c"),
        ),
    ).snapshot()["timeline"]

    assert [item["sequence"] for item in timeline] == [3, 2, 1]


def test_existing_snapshot_route_works() -> None:
    response = TestClient(create_app(StaticProvider(snapshot_with_events(())))).get(SNAPSHOT_ROUTE)

    assert response.status_code == 200
    assert response.json()["recent_events"] == []


def test_existing_dashboard_route_works() -> None:
    response = TestClient(create_app(StaticProvider(snapshot_with_events(())))).get(DASHBOARD_ROUTE)

    assert response.status_code == 200
    assert "Engineering Dashboard" in response.text


def test_no_mutation_methods() -> None:
    client = TestClient(create_app(StaticProvider(snapshot_with_events(()))))

    for method in (client.post, client.put, client.patch, client.delete):
        assert method(SNAPSHOT_ROUTE).status_code == 405
        assert method(DASHBOARD_ROUTE).status_code == 405


def test_timeline_missing_occurred_at(tmp_path: Path) -> None:
    timeline = service(
        tmp_path,
        (event(1, "2026-08-10T00:00:00+00:00"), event(2, None, "missing")),
    ).snapshot()["timeline"]

    assert [item["event_id"] for item in timeline] == ["missing", "event-001"]


def test_timeline_malformed_occurred_at(tmp_path: Path) -> None:
    timeline = service(
        tmp_path,
        (event(1, "2026-08-10T00:00:00+00:00"), event(2, {"bad": "timestamp"}, "bad")),
    ).snapshot()["timeline"]

    assert [item["event_id"] for item in timeline] == ["event-001", "bad"]


def test_timeline_limit_still_enforced(tmp_path: Path) -> None:
    timeline = service(tmp_path, tuple(event(index, f"2026-08-10T00:{index:02d}:00+00:00") for index in range(10))).snapshot(
        timeline_limit=4
    )["timeline"]

    assert len(timeline) == 4
    assert [item["sequence"] for item in timeline] == [6, 7, 8, 9]


def test_snapshot_identical_outside_ordering(tmp_path: Path) -> None:
    ordered_events = (event(1, "2026-08-10T00:01:00+00:00"), event(2, "2026-08-10T00:02:00+00:00"))
    scrambled_events = tuple(reversed(ordered_events))

    first = service(tmp_path / "a", ordered_events).snapshot()
    second = service(tmp_path / "b", scrambled_events).snapshot()

    first_without_timeline = {key: value for key, value in first.items() if key != "timeline"}
    second_without_timeline = {key: value for key, value in second.items() if key != "timeline"}
    assert first_without_timeline == second_without_timeline
    assert first["timeline"] == second["timeline"]


def test_query_service_event_source_interface(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    store = EngineeringEventStore(cfg.event_store_path)  # type: ignore[arg-type]
    store.append(
        build_event(
            EventType.WORKFLOW_TRANSITION,
            occurred_at="2026-08-10T00:00:00+00:00",
            task_id="ENGDASH-005",
            identity="test",
            payload={"state": "PLAN"},
        )
    )
    with sqlite3.connect(cfg.event_store_path) as connection:  # type: ignore[arg-type]
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    with_adapter = create_engineering_query_service(cfg).snapshot()["timeline"]
    with_store = EngineeringQueryService(
        event_source=store,
        workflow_source=WorkflowStore(cfg.workflow_state_path),  # type: ignore[arg-type]
        backlog_path=cfg.backlog_path,  # type: ignore[arg-type]
    ).snapshot()["timeline"]

    assert with_adapter == with_store


def test_query_service_workflow_adapter_normalization(tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.json"
    workflow_store = WorkflowStore(workflow_path)
    adapter = FakeWorkflowAdapter(workflow_store)
    query_service = EngineeringQueryService(
        event_source=FakeEventSource(()), workflow_source=adapter, backlog_path=write_backlog(tmp_path)
    )

    assert adapter.calls == 1
    assert query_service.snapshot()["timeline"] == []


def test_no_project_context_in_query_service() -> None:
    source = Path("engineering/query_service.py").read_text(encoding="utf-8")

    assert "ProjectContext" not in source
    assert "build_project_context" not in source
