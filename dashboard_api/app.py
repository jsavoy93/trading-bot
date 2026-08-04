from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Callable, Mapping, Protocol

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard_api.engineering_read_model import (
    ApprovalSummary,
    BacklogSummary,
    DashboardSnapshot,
    EngineeringDashboardReadModel,
    HealthWarning,
    PullRequestSummary,
    RepositorySummary,
    StaticRepositorySummaryReader,
    WorkflowSummary,
)


SNAPSHOT_ROUTE = "/api/engineering/snapshot"
DASHBOARD_ROUTE = "/engineering"
MAX_RENDERED_MAP_ITEMS = 50


class SnapshotProvider(Protocol):
    def snapshot(self) -> DashboardSnapshot: ...


@dataclass(frozen=True)
class EmptyQuerySnapshotReader:
    """Read-only empty query source for standalone degraded startup."""

    def snapshot(self, *, timeline_limit: int = 100) -> Mapping[str, object]:
        return {
            "current_task": None,
            "timeline": [],
            "agent_run": None,
            "backlog": [],
            "tests": None,
            "report": None,
            "pr_links": [],
            "remaining_gaps": ["No engineering query source is configured."],
            "recommended_next_step": "Configure an EngineeringDashboardReadModel provider.",
        }


def create_default_read_model() -> EngineeringDashboardReadModel:
    """Create a safe read-only degraded model for independent local startup.

    Production integrations should inject a fully wired EngineeringDashboardReadModel.
    This default never imports trading code, executes shell commands, or reads secrets.
    """

    return EngineeringDashboardReadModel(
        query_service=EmptyQuerySnapshotReader(),
        repository_reader=StaticRepositorySummaryReader(
            RepositorySummary(
                root="unavailable",
                branch=None,
                is_clean=None,
                sync_state="unavailable",
            )
        ),
        project_identity="trading-bot",
    )


def create_app(snapshot_provider: SnapshotProvider | None = None) -> FastAPI:
    provider = snapshot_provider or create_default_read_model()
    app = FastAPI(
        title="Engineering Dashboard",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get(SNAPSHOT_ROUTE, name="engineering_snapshot")
    def engineering_snapshot() -> JSONResponse:
        snapshot = provider.snapshot()
        return JSONResponse(_public_snapshot_payload(snapshot))

    @app.get(DASHBOARD_ROUTE, response_class=HTMLResponse, name="engineering_dashboard")
    def engineering_dashboard() -> HTMLResponse:
        snapshot = provider.snapshot()
        return HTMLResponse(render_dashboard(snapshot))

    return app


def render_dashboard(snapshot: DashboardSnapshot) -> str:
    payload = snapshot.to_dict()
    warnings = tuple(snapshot.health_warnings)
    health_class = "degraded" if warnings else "healthy"
    health_text = "Degraded" if warnings else "Healthy"
    return "".join(
        [
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
            "<meta name='viewport' content='width=device-width, initial-scale=1'>",
            "<title>Engineering Dashboard</title>",
            "<style>",
            "body{font-family:system-ui,-apple-system,sans-serif;margin:2rem;background:#0f172a;color:#e2e8f0}",
            "section{border:1px solid #334155;border-radius:12px;padding:1rem;margin:1rem 0;background:#111827}",
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}",
            ".card{border:1px solid #475569;border-radius:10px;padding:.75rem;background:#1e293b}",
            ".healthy{color:#86efac}.degraded{color:#fbbf24}.warning{border-left:4px solid #f59e0b;padding-left:.75rem}",
            "dt{font-weight:700;color:#bfdbfe}dd{margin:0 0 .5rem 0}code{color:#bae6fd}",
            "</style></head><body>",
            f"<h1>{_html(payload.get('project_identity'))} Engineering Dashboard</h1>",
            f"<p>Status: <strong class='{health_class}'>{health_text}</strong> · Freshness: <code>{_html(payload.get('data_freshness_timestamp'))}</code></p>",
            _warnings_section(warnings),
            "<div class='grid'>",
            _repository_section(snapshot.repository),
            _workflow_section(snapshot.workflow),
            _approval_section(snapshot.approval),
            _test_section(snapshot.latest_execution_result, snapshot.latest_test_result),
            _pull_request_section(snapshot.pull_request),
            "</div>",
            _backlog_section(snapshot.backlog),
            _reports_section(snapshot.recent_reports),
            _events_section(snapshot.recent_events),
            "</body></html>",
        ]
    )


def _repository_section(repository: RepositorySummary) -> str:
    return _card(
        "Repository",
        _definition_list(
            {
                "Root": repository.root,
                "Branch": repository.branch,
                "Clean": repository.is_clean,
                "Sync": repository.sync_state,
                "Ahead": repository.ahead_count,
                "Behind": repository.behind_count,
                "Latest commit": repository.latest_commit,
                "Subject": repository.latest_commit_subject,
            }
        ),
    )


def _workflow_section(workflow: WorkflowSummary) -> str:
    return _card(
        "Workflow",
        _definition_list(
            {
                "Active": workflow.active,
                "Task": workflow.task_id,
                "Stage": workflow.stage,
                "Owner/agent": workflow.owner_agent,
                "Branch": workflow.feature_branch,
                "Execution": workflow.execution_status,
                "Blocker": workflow.blocker,
                "Updated": workflow.updated_at,
            }
        ),
    )


def _approval_section(approval: ApprovalSummary) -> str:
    return _card(
        "Approval",
        _definition_list(
            {
                "Pending": approval.pending,
                "Reason": approval.reason,
                "Task": approval.task_id,
                "Requested": approval.requested_at,
                "Next action": approval.next_action,
            }
        ),
    )


def _test_section(latest_execution: str | None, latest_test: object | None) -> str:
    test = latest_test.to_dict() if hasattr(latest_test, "to_dict") else None
    if test is None and hasattr(latest_test, "__dict__"):
        test = latest_test.__dict__
    return _card(
        "Execution and tests",
        _definition_list(
            {
                "Latest execution": latest_execution,
                "Command": " ".join(test.get("command", ())) if isinstance(test, Mapping) else None,
                "Exit code": test.get("exit_code") if isinstance(test, Mapping) else None,
                "Passed": test.get("passed_count") if isinstance(test, Mapping) else None,
                "Failed": test.get("failed_count") if isinstance(test, Mapping) else None,
                "Summary": test.get("summary") if isinstance(test, Mapping) else None,
            }
        ),
    )


def _pull_request_section(pr: PullRequestSummary | None) -> str:
    if pr is None:
        values = {"Available": False}
    else:
        values = {
            "Available": pr.available,
            "URL": pr.url,
            "Number": pr.number,
            "State": pr.state,
            "Target": pr.target_branch,
            "Head": pr.head_branch,
            "Mergeable": pr.mergeable,
        }
    return _card("Pull request", _definition_list(values))


def _backlog_section(backlog: BacklogSummary) -> str:
    return "".join(
        [
            "<section><h2>Backlog</h2>",
            _definition_list(
                {
                    "Active task": backlog.active_task_id,
                    "Title": backlog.active_task_title,
                    "Status": backlog.status,
                    "Owner": backlog.owner,
                    "Priority": backlog.priority,
                }
            ),
            "<h3>Counts by status</h3>",
            _mapping_list(backlog.counts_by_status),
            "<h3>Counts by priority</h3>",
            _mapping_list(backlog.counts_by_priority),
            "</section>",
        ]
    )


def _reports_section(reports: tuple[object, ...]) -> str:
    items = []
    for report in reports:
        data = report.__dict__ if hasattr(report, "__dict__") else report
        if isinstance(data, Mapping):
            items.append(
                "<li>"
                f"<strong>{_html(data.get('kind'))}</strong>: {_html(data.get('title'))} "
                f"<code>{_html(data.get('path'))}</code> {_html(data.get('generated_at'))}"
                "</li>"
            )
    return f"<section><h2>Recent reports</h2><ul>{''.join(items) or '<li>None</li>'}</ul></section>"


def _events_section(events: tuple[Mapping[str, object], ...]) -> str:
    items = []
    for event in events:
        label = event.get("event_type") or event.get("message") or "event"
        task = event.get("task_id")
        occurred = event.get("occurred_at")
        items.append(f"<li><strong>{_html(label)}</strong> {_html(task)} <code>{_html(occurred)}</code></li>")
    return f"<section><h2>Recent timeline/events</h2><ul>{''.join(items) or '<li>None</li>'}</ul></section>"


def _warnings_section(warnings: tuple[HealthWarning, ...]) -> str:
    if not warnings:
        return "<section><h2>Health</h2><p class='healthy'>All configured sources are healthy.</p></section>"
    items = "".join(
        f"<li class='warning'><strong>{_html(w.source)}</strong> {_html(w.severity)} — {_html(w.message)}</li>"
        for w in warnings
    )
    return f"<section><h2>Degradation warnings</h2><ul>{items}</ul></section>"


def _card(title: str, body: str) -> str:
    return f"<section class='card'><h2>{_html(title)}</h2>{body}</section>"


def _definition_list(values: Mapping[str, object]) -> str:
    entries = []
    for key, value in values.items():
        entries.append(f"<dt>{_html(key)}</dt><dd>{_html(_display(value))}</dd>")
    return f"<dl>{''.join(entries)}</dl>"


def _mapping_list(values: Mapping[str, int]) -> str:
    entries = []
    for index, key in enumerate(sorted(values)):
        if index >= MAX_RENDERED_MAP_ITEMS:
            break
        entries.append(f"<li>{_html(key)}: {_html(values[key])}</li>")
    return f"<ul>{''.join(entries) or '<li>None</li>'}</ul>"


def _public_snapshot_payload(snapshot: DashboardSnapshot) -> dict[str, object]:
    payload = _safe_payload(snapshot.to_dict())
    if isinstance(payload, dict):
        warnings = payload.get("health_warnings")
        if isinstance(warnings, list):
            for warning in warnings:
                if isinstance(warning, dict):
                    warning["detail"] = None
    return payload if isinstance(payload, dict) else {}


def _safe_payload(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _safe_payload(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_safe_payload(item) for item in value]
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    return value


def _display(value: object) -> str:
    if value is None:
        return "Unavailable"
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return str(value)


def _html(value: object) -> str:
    return escape(_display(value), quote=True)


app = create_app()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the read-only engineering dashboard app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run("dashboard_api.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
