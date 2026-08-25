from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Mapping, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard_api.chat_gateway import GatewayChatHistoryClient
from dashboard_api.engineering_read_model import (
    AgentActivitySummary,
    ApprovalSummary,
    BacklogSummary,
    DashboardSnapshot,
    EngineeringHealthSummary,
    HealthWarning,
    PullRequestSummary,
    RecentExecutionSummary,
    RepositorySummary,
    TaskStatusSummary,
    TestingSummary,
    WorkflowSummary,
)
from engineering.context import build_project_context
from engineering.models import TRADING_BOT_PROJECT
from dashboard_api.providers import (
    EngineeringDashboardProviderConfig,
    create_engineering_dashboard_provider,
)


SNAPSHOT_ROUTE = "/api/engineering/snapshot"
CHAT_HISTORY_ROUTE = "/api/engineering/chat/history"
CHAT_SEND_ROUTE = "/api/engineering/chat/send"
DASHBOARD_ROUTE = "/engineering"
MAX_RENDERED_MAP_ITEMS = 50


class SnapshotProvider(Protocol):
    def snapshot(self) -> DashboardSnapshot: ...


class ChatHistoryProvider(Protocol):
    def history(self) -> object: ...

    def send(self, message: object) -> object: ...


def create_default_read_model() -> SnapshotProvider:
    """Create the default read-only EngineeringQueryService-backed provider.

    Uses TRADING_BOT_PROJECT + build_project_context to build an explicit
    EngineeringDashboardProviderConfig without repo discovery, hard-coded repo
    paths, or dashboard-specific path env vars.
    """
    context = build_project_context(TRADING_BOT_PROJECT)
    project = context.config
    wf = project.workflow_files
    gov = project.governance_files

    config = EngineeringDashboardProviderConfig(
        repo_root=context.metadata.repository_root,
        backlog_path=gov.backlog_path,
        workflow_state_path=wf.workflow_store_path,
        event_store_path=wf.event_store_path,
        workflow_report_dir=wf.report_dir,
        project_identity=context.metadata.project_id,
        clock=lambda: datetime.now(UTC),
    )
    return create_engineering_dashboard_provider(config)


def create_app(snapshot_provider: SnapshotProvider | None = None, chat_history_provider: ChatHistoryProvider | None = None) -> FastAPI:
    provider = snapshot_provider or create_default_read_model()
    chat_provider = chat_history_provider or GatewayChatHistoryClient()
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

    @app.get(CHAT_HISTORY_ROUTE, name="engineering_chat_history")
    def engineering_chat_history() -> JSONResponse:
        history = chat_provider.history()
        if hasattr(history, "to_public_dict"):
            return JSONResponse(history.to_public_dict())
        return JSONResponse(history)

    @app.post(CHAT_SEND_ROUTE, name="engineering_chat_send")
    async def engineering_chat_send(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - return bounded public error only.
            return JSONResponse({"ok": False, "status": "rejected", "error": "invalid JSON"}, status_code=400)
        if not isinstance(payload, Mapping):
            return JSONResponse({"ok": False, "status": "rejected", "error": "JSON object required"}, status_code=400)
        if set(payload) != {"message"}:
            return JSONResponse({"ok": False, "status": "rejected", "error": "only message is allowed"}, status_code=400)
        result = chat_provider.send(payload.get("message"))
        body = result.to_public_dict() if hasattr(result, "to_public_dict") else result
        if isinstance(body, Mapping) and body.get("ok") is True:
            return JSONResponse(body)
        status = body.get("status") if isinstance(body, Mapping) else None
        return JSONResponse(body, status_code=400 if status == "rejected" else 503)

    @app.get(DASHBOARD_ROUTE, response_class=HTMLResponse, name="engineering_dashboard")
    def engineering_dashboard() -> HTMLResponse:
        snapshot = provider.snapshot()
        return HTMLResponse(render_dashboard(snapshot))

    return app


def render_dashboard(snapshot: DashboardSnapshot) -> str:
    payload = snapshot.to_dict()
    return "".join(
        [
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
            "<meta name='viewport' content='width=device-width, initial-scale=1'>",
            "<title>Engineering Dashboard</title>",
            "<style>",
            "*{box-sizing:border-box}html{overflow-x:hidden}body{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#0f172a;color:#e2e8f0;overflow-x:hidden}",
            ".shell{width:min(100%,1120px);margin:0 auto;padding:1rem}.dashboard-title{font-size:1.25rem;margin:.25rem 0 .75rem}",
            "section{border:1px solid #334155;border-radius:14px;padding:1rem;margin:.75rem 0;background:#111827;max-width:100%}",
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.75rem}.overview-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.6rem}",
            ".card{border:1px solid #475569;border-radius:12px;padding:.75rem;background:#1e293b;min-width:0}.mini-card{border:1px solid #334155;border-radius:12px;padding:.65rem;background:#172033;min-width:0}",
            ".label{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:#93c5fd}.value{font-weight:700;margin-top:.15rem;overflow-wrap:anywhere}.muted{color:#94a3b8}.truncate{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
            ".badge{display:inline-flex;align-items:center;min-height:1.75rem;padding:.2rem .55rem;border-radius:999px;background:#334155;color:#e2e8f0;font-weight:700;font-size:.82rem}.healthy{color:#86efac}.degraded{color:#fbbf24}.error{color:#fca5a5}.warning{border-left:4px solid #f59e0b;padding-left:.75rem}",
            ".tabs{position:sticky;top:0;z-index:2;display:flex;gap:.35rem;overflow-x:auto;padding:.5rem 0;margin:0 0 .5rem;background:#0f172a}.tab-button{appearance:none;border:1px solid #334155;border-radius:999px;background:#1e293b;color:#e2e8f0;padding:.65rem .8rem;min-height:44px;font-weight:700;white-space:nowrap}.tab-button[aria-selected='true']{background:#2563eb;border-color:#60a5fa;color:#fff}.tab-panel[hidden]{display:none}",
            ".list{display:grid;gap:.65rem}.activity-card,.report-card,.event-card,.task-card{border:1px solid #334155;border-radius:12px;padding:.75rem;background:#172033;min-width:0}.kv{display:grid;grid-template-columns:minmax(6rem,.45fr) minmax(0,1fr);gap:.25rem .6rem;margin-top:.5rem}.kv dt{font-weight:700;color:#bfdbfe}.kv dd{margin:0;min-width:0;overflow-wrap:anywhere}",
            ".chat-history{display:flex;flex-direction:column;gap:.65rem;max-height:62vh;overflow-y:auto;padding:.35rem}.chat-message{border:1px solid #334155;border-radius:14px;padding:.7rem;max-width:92%;overflow-wrap:anywhere;white-space:pre-wrap}.chat-message.user{align-self:flex-end;background:#1d4ed8;border-color:#60a5fa}.chat-message.assistant{align-self:flex-start;background:#172033;border-color:#475569}.chat-meta{display:block;margin-bottom:.25rem;font-size:.72rem;color:#bfdbfe;text-transform:uppercase;letter-spacing:.04em}.chat-message-actions{display:flex;justify-content:flex-end;margin-top:.4rem}.chat-copy{appearance:none;border:1px solid #475569;border-radius:999px;background:#0f172a;color:#bfdbfe;font-size:.72rem;padding:.25rem .65rem;min-height:28px;line-height:1.1;cursor:pointer;font-weight:600;letter-spacing:.02em}.chat-copy:hover{background:#1e293b;border-color:#60a5fa;color:#e2e8f0}.chat-copy:focus-visible{outline:2px solid #60a5fa;outline-offset:2px}.chat-copy[data-copy-state='copied']{background:#14532d;border-color:#22c55e;color:#bbf7d0}.chat-copy[data-copy-state='failed']{background:#7f1d1d;border-color:#fca5a5;color:#fecaca}.chat-copy[disabled]{opacity:.6;cursor:not-allowed}.chat-state{border:1px dashed #475569;border-radius:12px;padding:.75rem;color:#cbd5e1;background:#111827}.chat-status{display:flex;align-items:center;gap:.5rem;border:1px solid #334155;border-radius:999px;padding:.4rem .75rem;background:#172033;font-size:.85rem;min-height:36px;margin:0 0 .65rem;width:fit-content;max-width:100%}.chat-status .dot{display:inline-block;width:.65rem;height:.65rem;border-radius:50%;background:#94a3b8;flex:none}.chat-status[data-agent-status='working'] .dot{background:#fbbf24;animation:chat-status-pulse 1.05s ease-in-out infinite}@keyframes chat-status-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.45;transform:scale(.8)}}.chat-status[data-agent-status='failed'] .dot{background:#fca5a5}.chat-status[data-agent-status='stale'] .dot{background:#f59e0b}.chat-status[data-agent-status='unavailable'] .dot{background:#94a3b8}.chat-status[data-agent-status='loading'] .dot{background:#94a3b8}.chat-status .label{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:60vw}.chat-status-row{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin:0 0 .65rem}.chat-status-row .chat-status{margin:0}.chat-since-copy{appearance:none;border:1px solid #475569;border-radius:999px;background:#1e293b;color:#e2e8f0;font-size:.78rem;padding:.4rem .85rem;min-height:36px;font-weight:600;cursor:pointer}.chat-since-copy:hover{background:#334155;border-color:#60a5fa}.chat-since-copy:focus-visible{outline:2px solid #60a5fa;outline-offset:2px}.chat-since-copy[data-copy-state='copied']{background:#14532d;border-color:#22c55e;color:#bbf7d0}.chat-since-copy[data-copy-state='failed']{background:#7f1d1d;border-color:#fca5a5;color:#fecaca}.chat-since-copy[disabled]{opacity:.55;cursor:not-allowed}.chat-since-copy[hidden]{display:none}.chat-form{display:grid;gap:.5rem;margin-top:.75rem}.chat-input{width:100%;min-height:5.5rem;border:1px solid #475569;border-radius:12px;background:#0f172a;color:#e2e8f0;padding:.75rem;font:inherit;resize:vertical}.chat-send{justify-self:end;min-height:44px;border:1px solid #60a5fa;border-radius:999px;background:#2563eb;color:#fff;font-weight:700;padding:.65rem 1rem}.chat-send:disabled{opacity:.6;cursor:not-allowed}",
            "#update-warning{display:none;border-left:4px solid #f59e0b;padding:.75rem;margin:.75rem 0;background:#292524;color:#fde68a;border-radius:10px}",
            "dl{margin:.5rem 0 0}dt{font-weight:700;color:#bfdbfe}dd{margin:0 0 .5rem 0;overflow-wrap:anywhere}code{color:#bae6fd;white-space:normal;overflow-wrap:anywhere}ul{padding-left:1.1rem;margin:.5rem 0}li{margin:.3rem 0}",
            "@media(max-width:700px){.shell{padding:.75rem}.overview-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:.5rem}.grid{grid-template-columns:1fr}.dashboard-title{font-size:1.1rem}.card,.mini-card,section{padding:.65rem}.kv{grid-template-columns:1fr}.tabs{margin-left:-.75rem;margin-right:-.75rem;padding:.45rem .75rem}.tab-button{font-size:.9rem;padding:.6rem .75rem}}",
            "</style></head><body><div class='shell'>",
            f"<h1 class='dashboard-title'>{_html(payload.get('project_identity'))} Engineering Dashboard</h1>",
            "<div id='update-warning' role='status' aria-live='polite'></div>",
            f"<main id='dashboard-content'>{_dashboard_content(snapshot)}</main>",
            _refresh_script(),
            "</div></body></html>",
        ]
    )


TAB_ORDER = ("overview", "activity", "backlog", "timeline", "reports", "health", "chat")
TAB_LABELS = {
    "overview": "Overview",
    "activity": "Activity",
    "backlog": "Backlog",
    "timeline": "Timeline",
    "reports": "Reports",
    "health": "Health",
    "chat": "Chat",
}


def _dashboard_content(snapshot: DashboardSnapshot) -> str:
    return "".join(
        [
            _tab_nav(),
            _tab_panel("overview", _overview_tab(snapshot), selected=True),
            _tab_panel("activity", _activity_tab(snapshot)),
            _tab_panel("backlog", _backlog_tab(snapshot)),
            _tab_panel("timeline", _timeline_tab(snapshot)),
            _tab_panel("reports", _reports_tab(snapshot)),
            _tab_panel("health", _health_tab(snapshot)),
            _tab_panel("chat", _chat_tab()),
        ]
    )


def _tab_nav() -> str:
    buttons = "".join(
        f"<button class='tab-button' type='button' role='tab' data-tab='{_html(tab)}' aria-controls='tab-{_html(tab)}' aria-selected='{'true' if tab == 'overview' else 'false'}'>{_html(TAB_LABELS[tab])}</button>"
        for tab in TAB_ORDER
    )
    return f"<nav class='tabs' role='tablist' aria-label='Engineering dashboard sections'>{buttons}</nav>"


def _tab_panel(tab: str, body: str, *, selected: bool = False) -> str:
    hidden = "" if selected else " hidden"
    return f"<section id='tab-{_html(tab)}' class='tab-panel' role='tabpanel' data-tab-panel='{_html(tab)}'{hidden}>{body}</section>"


def _overview_tab(snapshot: DashboardSnapshot) -> str:
    health = snapshot.engineering_health
    warnings = tuple(snapshot.health_warnings)
    status = health.overall_status if health else ("DEGRADED" if warnings else "HEALTHY")
    status_class = _status_class(status)
    workflow = snapshot.workflow
    activity = snapshot.live_activity[0] if snapshot.live_activity else None
    blockers = tuple(snapshot.blockers)
    backlog_counts = snapshot.backlog.counts_by_status
    latest_activity = activity.latest_activity if activity else snapshot.latest_execution_result
    last_completed = activity.last_completed_action if activity else None
    elapsed = activity.elapsed_seconds if activity else None
    overview_values = [
        ("Health", f"<span class='badge {status_class}'>{_html(status.title())}</span>"),
        ("Project", _html(snapshot.project_identity)),
        ("Branch", f"<code class='truncate'>{_html(snapshot.repository.branch)}</code>"),
        ("Repo safe", _html(health.repository_safe if health else snapshot.repository.is_clean)),
        ("Current task", _html(workflow.task_id or snapshot.backlog.active_task_id)),
        ("Agent", _html(workflow.owner_agent or (activity.agent_name if activity else None))),
        ("Execution", _html(workflow.execution_status or (activity.status if activity else None))),
        ("Phase", _html(workflow.stage or (activity.phase if activity else None))),
        ("Elapsed", _html(elapsed)),
        ("Latest activity", _html(latest_activity)),
        ("Last completed", _html(last_completed)),
        ("Blockers", _html("None" if not blockers else f"{len(blockers)} recorded")),
        ("DONE", _html(backlog_counts.get("DONE", 0))),
        ("REVIEW", _html(backlog_counts.get("REVIEW", 0))),
        ("TODO", _html(backlog_counts.get("TODO", 0))),
        ("BLOCKED", _html(backlog_counts.get("BLOCKED", 0))),
    ]
    cards = "".join(
        f"<article class='mini-card'><div class='label'>{label}</div><div class='value'>{value}</div></article>"
        for label, value in overview_values
    )
    return f"<h2>Overview</h2><div class='overview-grid'>{cards}</div><p class='muted'>Freshness: <code>{_html(snapshot.data_freshness_timestamp)}</code></p>"


def _activity_tab(snapshot: DashboardSnapshot) -> str:
    live = "".join(_activity_card(item) for item in snapshot.live_activity) or "<p class='muted'>No active engineering-agent activity.</p>"
    recent = "".join(_recent_execution_card(item) for item in snapshot.recent_executions) or "<p class='muted'>No recent executions.</p>"
    return f"<h2>Activity</h2><h3>Live Agent Activity</h3><div class='list'>{live}</div><h3>Recent Executions</h3><div class='list'>{recent}</div>"


def _activity_card(item: AgentActivitySummary) -> str:
    return _detail_card(
        "activity-card",
        f"{_html(item.status)} · {_html(item.agent_name)}",
        {
            "Task": f"{item.task_id} {item.task_title}",
            "Phase": item.phase,
            "Branch": item.branch,
            "Run ID": item.run_id,
            "Started": item.started_at,
            "Elapsed": item.elapsed_seconds,
            "Latest activity": item.latest_activity,
            "Last completed": item.last_completed_action,
            "Blocker": item.blocker,
            "Timeout / recovery": f"{item.timeout_state} / {item.recovery_state}",
        },
    )


def _recent_execution_card(item: RecentExecutionSummary) -> str:
    return _detail_card(
        "activity-card",
        f"{_html(item.final_status)} · {_html(item.agent_name)}",
        {
            "Task": item.task_id,
            "Branch": item.branch,
            "Run ID": item.run_id,
            "Completed": item.completed_at,
            "Duration": item.elapsed_seconds,
            "Result": item.result_summary,
        },
    )


def _backlog_tab(snapshot: DashboardSnapshot) -> str:
    counts = _mapping_list(snapshot.backlog.counts_by_status)
    active = _definition_list(
        {
            "Active task": snapshot.backlog.active_task_id,
            "Title": snapshot.backlog.active_task_title,
            "Status": snapshot.backlog.status,
            "Owner": snapshot.backlog.owner,
            "Priority": snapshot.backlog.priority,
        }
    )
    task_cards = "".join(_task_card(task) for task in snapshot.current_tasks) or "<p class='muted'>No active prioritized task is currently recorded.</p>"
    return f"<h2>Backlog</h2><h3>Counts</h3>{counts}<h3>Active/current task</h3>{active}<h3>Prioritized task list</h3><div class='list'>{task_cards}</div>"


def _task_card(task: TaskStatusSummary) -> str:
    return _detail_card(
        "task-card",
        f"{_html(task.task_id)} · {_html(task.status)}",
        {
            "Title": task.title,
            "Priority": task.priority,
            "Owner": task.assigned_agent,
            "Phase": task.current_phase,
            "Updated": task.last_updated,
            "Blocker": task.blocking_reason,
        },
    )


def _timeline_tab(snapshot: DashboardSnapshot) -> str:
    events = "".join(_event_card(event) for event in snapshot.recent_events) or "<p class='muted'>No recent structured events.</p>"
    return f"<h2>Timeline</h2><div class='list'>{events}</div>"


def _event_card(event: Mapping[str, object]) -> str:
    label = event.get("event_type") or event.get("type") or event.get("message") or "event"
    return _detail_card(
        "event-card",
        _html(label),
        {
            "Task": event.get("task_id"),
            "Occurred": event.get("occurred_at"),
            "Payload": event.get("payload"),
        },
    )


def _reports_tab(snapshot: DashboardSnapshot) -> str:
    reports = "".join(_report_card(report) for report in snapshot.recent_reports) or "<p class='muted'>No recent reports.</p>"
    return f"<h2>Reports</h2><div class='list'>{reports}</div>"


def _report_card(report: object) -> str:
    data = report.__dict__ if hasattr(report, "__dict__") else report
    if not isinstance(data, Mapping):
        return ""
    return _detail_card(
        "report-card",
        _html(data.get("title")),
        {
            "Task": data.get("task_id"),
            "Outcome": data.get("outcome") or data.get("kind"),
            "Generated": data.get("generated_at"),
            "Path": data.get("path"),
        },
    )


def _chat_tab() -> str:
    return "".join(
        [
            "<h2>Chat</h2>",
            "<p class='muted'>Conversation with the existing OpenClaw trading-manager session. Messages are text-only and bounded.</p>",
            "<div id='chat-state' class='chat-state' role='status' aria-live='polite'>Loading trading-manager history…</div>",
            "<div class='chat-status-row'>",
            "<div id='chat-status' class='chat-status' data-agent-status='loading' role='status' aria-live='polite' aria-label='Trading manager agent status'><span class='dot' aria-hidden='true'></span><span class='label'>Trading manager · Loading…</span></div>",
            "<button id='chat-copy-since' class='chat-since-copy' type='button' data-copy-state='idle' hidden disabled aria-label='Copy every trading-manager response since your last message'>Copy since my last message</button>",
            "</div>",
            "<div id='chat-history' class='chat-history' aria-label='Trading manager conversation history'></div>",
            "<form id='chat-form' class='chat-form'>",
            "<label class='label' for='chat-message'>Message trading-manager</label>",
            "<textarea id='chat-message' class='chat-input' name='message' maxlength='4000' required placeholder='Send a bounded text message to trading-manager…'></textarea>",
            "<button id='chat-send' class='chat-send' type='submit'>Send</button>",
            "</form>",
        ]
    )


def _health_tab(snapshot: DashboardSnapshot) -> str:
    return "".join(
        [
            "<h2>Health</h2>",
            _engineering_health_section(snapshot.engineering_health, tuple(snapshot.health_warnings)),
            _warnings_section(tuple(snapshot.health_warnings)),
            "<div class='grid'>",
            _repository_section(snapshot.repository),
            _test_section(snapshot.latest_execution_result, snapshot.latest_test_result),
            _testing_section(snapshot.testing),
            _pull_request_section(snapshot.pull_request),
            "</div>",
            f"<p class='muted'>Freshness: <code>{_html(snapshot.data_freshness_timestamp)}</code></p>",
        ]
    )


def _detail_card(class_name: str, title: str, values: Mapping[str, object]) -> str:
    return f"<article class='{class_name}'><strong class='truncate'>{title}</strong>{_compact_definition_list(values)}</article>"


def _compact_definition_list(values: Mapping[str, object]) -> str:
    entries = []
    for key, value in values.items():
        entries.append(f"<dt>{_html(key)}</dt><dd><span class='truncate' title='{_html(value)}'>{_html(_display(value))}</span></dd>")
    return f"<dl class='kv'>{''.join(entries)}</dl>"


def _status_class(status: object) -> str:
    normalized = str(status or "").upper()
    if normalized == "HEALTHY":
        return "healthy"
    if normalized == "ERROR":
        return "error"
    return "degraded"


def _refresh_script() -> str:
    script = r'''
<script>
(function() {
  'use strict';
  const SNAPSHOT_URL = '__SNAPSHOT_ROUTE__';
  const CHAT_HISTORY_URL = '__CHAT_HISTORY_ROUTE__';
  const CHAT_SEND_URL = '__CHAT_SEND_ROUTE__';
  const POLL_INTERVAL_MS = 15000;
  const CHAT_POLL_INTERVAL_MS = 15000;
  const TAB_KEY = 'engineeringDashboard.selectedTab';
  const TABS = ['overview', 'activity', 'backlog', 'timeline', 'reports', 'health', 'chat'];
  const content = document.getElementById('dashboard-content');
  const warning = document.getElementById('update-warning');
  const display = (value) => value === null || value === undefined || value === '' ? 'Unavailable' : value === true ? 'Yes' : value === false ? 'No' : String(value);
  const esc = (value) => display(value).replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#x27;'}[char]));
  const statusClass = (status) => String(status || '').toUpperCase() === 'HEALTHY' ? 'healthy' : String(status || '').toUpperCase() === 'ERROR' ? 'error' : 'degraded';
  const dl = (values) => '<dl>' + Object.entries(values).map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('') + '</dl>';
  const kv = (values) => '<dl class="kv">' + Object.entries(values).map(([key, value]) => `<dt>${esc(key)}</dt><dd><span class="truncate" title="${esc(value)}">${esc(value)}</span></dd>`).join('') + '</dl>';
  const card = (title, body) => `<section class="card"><h2>${esc(title)}</h2>${body}</section>`;
  const mappingList = (values) => '<ul>' + Object.keys(values || {}).sort().slice(0, 50).map((key) => `<li>${esc(key)}: ${esc(values[key])}</li>`).join('') + '</ul>';
  const detailCard = (className, title, values) => `<article class="${className}"><strong class="truncate">${title}</strong>${kv(values)}</article>`;
  const tabNav = () => `<nav class="tabs" role="tablist" aria-label="Engineering dashboard sections">${TABS.map((tab) => `<button class="tab-button" type="button" role="tab" data-tab="${tab}" aria-controls="tab-${tab}" aria-selected="${tab === 'overview'}">${esc(tab.charAt(0).toUpperCase() + tab.slice(1))}</button>`).join('')}</nav>`;
  const panel = (tab, body, selected) => `<section id="tab-${tab}" class="tab-panel" role="tabpanel" data-tab-panel="${tab}"${selected ? '' : ' hidden'}>${body}</section>`;
  const healthSection = (health) => health ? card('Engineering health', dl({'Overall status': health.overall_status, 'Repository safe': health.repository_safe, 'Branch': health.current_branch, 'Commit': health.current_commit, 'Last successful regression': health.last_successful_regression_run, 'Degraded sources': (health.degraded_sources || []).join(', '), 'Warnings': health.warning_count})) : card('Engineering health', dl({'Overall status': 'Unavailable', 'Warnings': 0}));
  const warningsSection = (warnings) => (warnings || []).length ? `<section><h2>Degradation warnings</h2><ul>${warnings.map((w) => `<li class="warning"><strong>${esc(w.source)}</strong> ${esc(w.severity)} — ${esc(w.message)}</li>`).join('')}</ul></section>` : "<section><h2>Health</h2><p class='healthy'>All configured sources are healthy.</p></section>";
  const repositorySection = (repo) => card('Repository', dl({'Root': repo.root, 'Branch': repo.branch, 'Clean': repo.is_clean, 'Sync': repo.sync_state, 'Ahead': repo.ahead_count, 'Behind': repo.behind_count, 'Latest commit': repo.latest_commit, 'Subject': repo.latest_commit_subject}));
  const testSection = (snapshot) => { const test = snapshot.latest_test_result || {}; return card('Execution and tests', dl({'Latest execution': snapshot.latest_execution_result, 'Command': (test.command || []).join(' '), 'Exit code': test.exit_code, 'Passed': test.passed_count, 'Failed': test.failed_count, 'Summary': test.summary})); };
  const testingSection = (testing) => card('Testing status', dl(testing ? {'Latest status': testing.latest_status, 'Warnings': testing.warning_count, 'Full suite completed': testing.full_suite && testing.full_suite.completed_at, 'Regression completed': testing.regression && testing.regression.completed_at} : {'Latest status': 'Unavailable'}));
  const pullRequestSection = (pr) => card('Pull request', dl(pr ? {'Available': pr.available, 'URL': pr.url, 'Number': pr.number, 'State': pr.state, 'Target': pr.target_branch, 'Head': pr.head_branch, 'Mergeable': pr.mergeable} : {'Available': false}));
  const overviewTab = (snapshot) => {
    const health = snapshot.engineering_health;
    const status = health ? health.overall_status : ((snapshot.health_warnings || []).length ? 'DEGRADED' : 'HEALTHY');
    const workflow = snapshot.workflow || {};
    const activity = (snapshot.live_activity || [])[0] || {};
    const counts = (snapshot.backlog && snapshot.backlog.counts_by_status) || {};
    const values = [['Health', `<span class="badge ${statusClass(status)}">${esc(status.charAt(0) + status.slice(1).toLowerCase())}</span>`], ['Project', esc(snapshot.project_identity)], ['Branch', `<code class="truncate">${esc(snapshot.repository && snapshot.repository.branch)}</code>`], ['Repo safe', esc(health ? health.repository_safe : snapshot.repository && snapshot.repository.is_clean)], ['Current task', esc(workflow.task_id || (snapshot.backlog && snapshot.backlog.active_task_id))], ['Agent', esc(workflow.owner_agent || activity.agent_name)], ['Execution', esc(workflow.execution_status || activity.status)], ['Phase', esc(workflow.stage || activity.phase)], ['Elapsed', esc(activity.elapsed_seconds)], ['Latest activity', esc(activity.latest_activity || snapshot.latest_execution_result)], ['Last completed', esc(activity.last_completed_action)], ['Blockers', esc((snapshot.blockers || []).length ? `${snapshot.blockers.length} recorded` : 'None')], ['DONE', esc(counts.DONE || 0)], ['REVIEW', esc(counts.REVIEW || 0)], ['TODO', esc(counts.TODO || 0)], ['BLOCKED', esc(counts.BLOCKED || 0)]];
    return `<h2>Overview</h2><div class="overview-grid">${values.map(([label, value]) => `<article class="mini-card"><div class="label">${label}</div><div class="value">${value}</div></article>`).join('')}</div><p class="muted">Freshness: <code>${esc(snapshot.data_freshness_timestamp)}</code></p>`;
  };
  const activityCard = (item) => detailCard('activity-card', `${esc(item.status)} · ${esc(item.agent_name)}`, {'Task': `${item.task_id || ''} ${item.task_title || ''}`, 'Phase': item.phase, 'Branch': item.branch, 'Run ID': item.run_id, 'Started': item.started_at, 'Elapsed': item.elapsed_seconds, 'Latest activity': item.latest_activity, 'Last completed': item.last_completed_action, 'Blocker': item.blocker, 'Timeout / recovery': `${item.timeout_state || ''} / ${item.recovery_state || ''}`});
  const recentCard = (item) => detailCard('activity-card', `${esc(item.final_status)} · ${esc(item.agent_name)}`, {'Task': item.task_id, 'Branch': item.branch, 'Run ID': item.run_id, 'Completed': item.completed_at, 'Duration': item.elapsed_seconds, 'Result': item.result_summary});
  const activityTab = (snapshot) => `<h2>Activity</h2><h3>Live Agent Activity</h3><div class="list">${(snapshot.live_activity || []).map(activityCard).join('') || '<p class="muted">No active engineering-agent activity.</p>'}</div><h3>Recent Executions</h3><div class="list">${(snapshot.recent_executions || []).map(recentCard).join('') || '<p class="muted">No recent executions.</p>'}</div>`;
  const taskCard = (task) => detailCard('task-card', `${esc(task.task_id)} · ${esc(task.status)}`, {'Title': task.title, 'Priority': task.priority, 'Owner': task.assigned_agent, 'Phase': task.current_phase, 'Updated': task.last_updated, 'Blocker': task.blocking_reason});
  const backlogTab = (snapshot) => `<h2>Backlog</h2><h3>Counts</h3>${mappingList(snapshot.backlog.counts_by_status)}<h3>Active/current task</h3>${dl({'Active task': snapshot.backlog.active_task_id, 'Title': snapshot.backlog.active_task_title, 'Status': snapshot.backlog.status, 'Owner': snapshot.backlog.owner, 'Priority': snapshot.backlog.priority})}<h3>Prioritized task list</h3><div class="list">${(snapshot.current_tasks || []).map(taskCard).join('') || '<p class="muted">No active prioritized task is currently recorded.</p>'}</div>`;
  const eventCard = (event) => detailCard('event-card', esc(event.event_type || event.type || event.message || 'event'), {'Task': event.task_id, 'Occurred': event.occurred_at, 'Payload': event.payload ? JSON.stringify(event.payload) : undefined});
  const timelineTab = (snapshot) => `<h2>Timeline</h2><div class="list">${(snapshot.recent_events || []).map(eventCard).join('') || '<p class="muted">No recent structured events.</p>'}</div>`;
  const reportCard = (report) => detailCard('report-card', esc(report.title), {'Task': report.task_id, 'Outcome': report.outcome || report.kind, 'Generated': report.generated_at, 'Path': report.path});
  const reportsTab = (snapshot) => `<h2>Reports</h2><div class="list">${(snapshot.recent_reports || []).map(reportCard).join('') || '<p class="muted">No recent reports.</p>'}</div>`;
  const healthTab = (snapshot) => `<h2>Health</h2>${healthSection(snapshot.engineering_health)}${warningsSection(snapshot.health_warnings)}<div class="grid">${repositorySection(snapshot.repository)}${testSection(snapshot)}${testingSection(snapshot.testing)}${pullRequestSection(snapshot.pull_request)}</div><p class="muted">Freshness: <code>${esc(snapshot.data_freshness_timestamp)}</code></p>`;
  const chatTab = () => `<h2>Chat</h2><p class="muted">Conversation with the existing OpenClaw trading-manager session. Messages are text-only and bounded.</p><div id="chat-state" class="chat-state" role="status" aria-live="polite">Loading trading-manager history…</div><div class="chat-status-row"><div id="chat-status" class="chat-status" data-agent-status="loading" role="status" aria-live="polite" aria-label="Trading manager agent status"><span class="dot" aria-hidden="true"></span><span class="label">Trading manager \u00b7 Loading\u2026</span></div><button id="chat-copy-since" class="chat-since-copy" type="button" data-copy-state="idle" hidden disabled aria-label="Copy every trading-manager response since your last message">Copy since my last message</button></div><div id="chat-history" class="chat-history" aria-label="Trading manager conversation history"></div><form id="chat-form" class="chat-form"><label class="label" for="chat-message">Message trading-manager</label><textarea id="chat-message" class="chat-input" name="message" maxlength="4000" required placeholder="Send a bounded text message to trading-manager…"></textarea><button id="chat-send" class="chat-send" type="submit">Send</button></form>`;
  const renderSnapshot = (snapshot) => tabNav() + panel('overview', overviewTab(snapshot), true) + panel('activity', activityTab(snapshot), false) + panel('backlog', backlogTab(snapshot), false) + panel('timeline', timelineTab(snapshot), false) + panel('reports', reportsTab(snapshot), false) + panel('health', healthTab(snapshot), false) + panel('chat', chatTab(), false);
  const selectedTab = () => { try { const stored = window.localStorage && window.localStorage.getItem(TAB_KEY); return TABS.includes(stored) ? stored : 'overview'; } catch (error) { return 'overview'; } };
  const switchTab = (tab) => {
    const selected = TABS.includes(tab) ? tab : 'overview';
    content.querySelectorAll('[data-tab]').forEach((button) => button.setAttribute('aria-selected', String(button.dataset.tab === selected)));
    content.querySelectorAll('[data-tab-panel]').forEach((panel) => { panel.hidden = panel.dataset.tabPanel !== selected; });
    try { window.localStorage && window.localStorage.setItem(TAB_KEY, selected); } catch (error) {}
    if (selected === 'chat') { refreshChatHistory(); }
    bindChatCopyControls();
  };
  const bindTabs = () => { content.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', (event) => { event.preventDefault(); switchTab(button.dataset.tab); })); switchTab(selectedTab()); bindChatForm(); };
  const setWarning = (message) => { warning.textContent = message; warning.style.display = message ? 'block' : 'none'; };
  const chatStateCache = {historyHtml: '', statusText: 'Loading trading-manager history…', statusDisplay: 'block', statusKind: 'working', draft: '', composeStatus: 'idle', scrollTop: 0, wasNearBottom: true, agentStatus: 'loading', runStatus: null, messages: [], sinceLastUserText: '', copyTimers: {}};
  const isNearBottom = (target) => !target || (target.scrollHeight - target.scrollTop <= target.clientHeight + 48);
  const applyComposeState = () => {
    const input = document.getElementById('chat-message');
    const button = document.getElementById('chat-send');
    const sending = chatStateCache.composeStatus === 'sending';
    if (input) { input.value = chatStateCache.draft || ''; input.disabled = sending; }
    if (button) { button.disabled = sending; button.textContent = sending ? 'Sending…' : 'Send'; }
  };
  const setComposeState = (draft, status) => {
    chatStateCache.draft = draft || '';
    chatStateCache.composeStatus = status || 'idle';
    applyComposeState();
  };
  const captureChatUiState = () => {
    const state = document.getElementById('chat-state');
    const target = document.getElementById('chat-history');
    const input = document.getElementById('chat-message');
    const button = document.getElementById('chat-send');
    const status = document.getElementById('chat-status');
    if (state) { chatStateCache.statusText = state.textContent || ''; chatStateCache.statusDisplay = state.style.display || ''; chatStateCache.statusKind = state.dataset && state.dataset.status ? state.dataset.status : chatStateCache.statusKind; }
    if (target) { chatStateCache.historyHtml = target.innerHTML || chatStateCache.historyHtml; chatStateCache.scrollTop = target.scrollTop || 0; chatStateCache.wasNearBottom = isNearBottom(target); }
    if (input) { chatStateCache.draft = input.value || ''; }
    if (button && button.disabled && button.textContent === 'Sending…') { chatStateCache.composeStatus = 'sending'; }
    if (status && status.dataset && status.dataset.agentStatus) { chatStateCache.agentStatus = status.dataset.agentStatus; }
  };
  const restoreChatUiState = () => {
    const state = document.getElementById('chat-state');
    const target = document.getElementById('chat-history');
    const input = document.getElementById('chat-message');
    const button = document.getElementById('chat-send');
    applyComposeState();
    renderAgentStatus();
    if (state) { state.textContent = chatStateCache.statusText || ''; state.style.display = chatStateCache.statusDisplay || (state.textContent ? 'block' : 'none'); if (state.dataset) { state.dataset.status = chatStateCache.statusKind || 'available'; } }
    if (target && chatStateCache.historyHtml) { target.innerHTML = chatStateCache.historyHtml; target.scrollTop = chatStateCache.scrollTop || 0; }
    bindChatCopyControls();
  };
  const setChatState = (message, failed) => {
    const state = document.getElementById('chat-state');
    chatStateCache.statusText = message || '';
    chatStateCache.statusDisplay = message ? 'block' : 'none';
    chatStateCache.statusKind = failed ? 'failed' : (message ? 'working' : 'available');
    if (!state) { return; }
    state.textContent = chatStateCache.statusText;
    state.style.display = chatStateCache.statusDisplay;
    if (state.dataset) { state.dataset.status = chatStateCache.statusKind; }
  };
  // Authoritative mapping from OpenClaw sessionInfo into the bounded agent
  // indicator state. Source of truth is the cached chat-status DOM attribute
  // so the indicator survives snapshot-poll DOM replacement.
  const AGENT_STATUS_LABELS = {
    idle: 'Trading manager · Idle',
    working: 'Trading manager · Working…',
    failed: 'Trading manager · Failed',
    loading: 'Trading manager · Loading…',
  };
  const TERMINAL_RUN_STATUSES = {failed: true, killed: true, timeout: true};
  const projectAgentStatus = (hasActiveRun, runStatus) => {
    const terminal = runStatus && TERMINAL_RUN_STATUSES[runStatus];
    if (terminal) { return 'failed'; }
    if (hasActiveRun) { return 'working'; }
    return 'idle';
  };
  const renderAgentStatus = () => {
    const status = document.getElementById('chat-status');
    const labelNode = status && status.querySelector ? status.querySelector('.label') : null;
    const cached = chatStateCache.agentStatus;
    const label = AGENT_STATUS_LABELS[cached] || AGENT_STATUS_LABELS.idle;
    if (!status) { return; }
    if (status.dataset) { status.dataset.agentStatus = cached; }
    if (labelNode) { labelNode.textContent = label; } else if (status.lastChild) { status.lastChild.textContent = label; }
  };
  const setAgentStatus = (next) => {
    const allowed = {'idle': true, 'working': true, 'failed': true, 'loading': true};
    chatStateCache.agentStatus = allowed[next] ? next : 'idle';
    renderAgentStatus();
  };
  const renderChatHistory = (history) => {
    const target = document.getElementById('chat-history');
    if (!target) { return; }
    // Any pending per-button copy-reset timers point at DOM nodes that are
    // about to be replaced; clear them so stale timers never mutate
    // detached elements after the new history renders.
    if (chatStateCache.copyTimers) {
      Object.keys(chatStateCache.copyTimers).forEach((key) => { clearTimeout(chatStateCache.copyTimers[key]); });
      chatStateCache.copyTimers = {};
    }
    const session = history && history.session ? history.session : {status: 'unavailable', agent: 'trading-manager'};
    const messages = Array.isArray(history && history.messages) ? history.messages : [];
    if (session.status !== 'available') {
      // Gateway is unavailable (genuine OR temporary poll failure). Per spec:
      // preserve last-known chat history, surface bounded stale message,
      // and DO NOT falsely switch Working -> Idle. The agent indicator
      // cache (`chatStateCache.agentStatus`) is intentionally not touched here.
      chatStateCache.messages = [];
      chatStateCache.sinceLastUserText = '';
      updateSinceCopyButton();
      setChatState('Chat history unavailable; keeping last known messages.', true);
      return;
    }
    const hasActiveRun = session.has_active_run === true;
    const allowedRunStatuses = {running: true, idle: true, done: true, failed: true, killed: true, timeout: true};
    const rawRunStatus = typeof session.run_status === 'string' ? session.run_status : null;
    const runStatus = rawRunStatus && allowedRunStatuses[rawRunStatus] ? rawRunStatus : null;
    chatStateCache.runStatus = runStatus;
    setAgentStatus(projectAgentStatus(hasActiveRun, runStatus));
    if (!messages.length) {
      setChatState('No visible trading-manager messages are available yet.', false);
      target.innerHTML = '';
      chatStateCache.messages = [];
      chatStateCache.sinceLastUserText = '';
      updateSinceCopyButton();
      return;
    }
    const shouldStickToBottom = isNearBottom(target) || chatStateCache.wasNearBottom;
    setChatState('', false);
    // Source-of-truth: store the already-projected messages array (PR #63
    // server projection). Copy controls MUST read from this same array so
    // hidden progress / toolUse / delivery-mirror rows can never leak into
    // the clipboard. Plain-text only, no HTML extraction, no DOM scraping.
    chatStateCache.messages = messages.slice();
    chatStateCache.sinceLastUserText = computeSinceLastUserText(messages);
    const html = messages.map((message, index) => {
      const role = message && message.role === 'user' ? 'user' : 'assistant';
      const label = role === 'user' ? 'Josh' : 'Trading manager';
      const actions = role === 'assistant'
        ? `<div class="chat-message-actions"><button type="button" class="chat-copy" data-copy-index="${index}" data-copy-state="idle" aria-label="Copy this trading-manager response">Copy</button></div>`
        : '';
      return `<article class="chat-message ${role}" data-message-index="${index}"><span class="chat-meta">${esc(label)} · ${esc(message && message.timestamp)}</span>${esc(message && message.text)}${actions}</article>`;
    }).join('');
    target.innerHTML = html;
    chatStateCache.historyHtml = html;
    updateSinceCopyButton();
    if (shouldStickToBottom) { target.scrollTop = target.scrollHeight; } else { target.scrollTop = chatStateCache.scrollTop || target.scrollTop; }
    chatStateCache.scrollTop = target.scrollTop || 0;
    chatStateCache.wasNearBottom = isNearBottom(target);
  };
  // Bound clipboard helper. Uses navigator.clipboard.writeText when available
  // and returns a bounded promise. On rejection (insecure context, denied
  // permission, unsupported browser) the caller surfaces a small failure
  // state. No unsafe HTML extraction or DOM scraping is involved: callers
  // pass the already-projected plain-text message text.
  //
  // Test injection hook: a non-default `window.__chatClipboardWriteText`
  // (set by the test harness) takes precedence over `navigator.clipboard`.
  // This is the only way to stub the clipboard under headless Node where
  // `navigator` is a read-only global without a `clipboard` member.
  const writeClipboardText = (text) => {
    const value = typeof text === 'string' ? text : '';
    if (!value) { return Promise.reject(new Error('empty clipboard payload')); }
    const hook = (typeof window !== 'undefined') ? window.__chatClipboardWriteText : null;
    if (typeof hook === 'function') {
      try { return Promise.resolve(hook(value)); } catch (error) { return Promise.reject(error); }
    }
    if (typeof navigator !== 'undefined' && navigator && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      try { return navigator.clipboard.writeText(value); } catch (error) { return Promise.reject(error); }
    }
    return Promise.reject(new Error('clipboard API unavailable'));
  };
  const setCopyState = (button, state, label) => {
    if (!button || !button.dataset) { return; }
    const allowed = {idle: true, copied: true, failed: true};
    button.dataset.copyState = allowed[state] ? state : 'idle';
    if (typeof label === 'string') { button.textContent = label; }
  };
  // Cancel any pending restore timer attached to a button before re-render so
  // a stale timer never mutates a detached node.
  const cancelCopyTimer = (button) => {
    if (!button || !chatStateCache.copyTimers) { return; }
    const key = button.dataset && button.dataset.copyTimerKey;
    if (key) {
      const existing = chatStateCache.copyTimers[key];
      if (existing) { clearTimeout(existing); delete chatStateCache.copyTimers[key]; }
      delete button.dataset.copyTimerKey;
    }
  };
  const scheduleCopyReset = (button, defaultLabel, delayMs) => {
    if (!button || !chatStateCache.copyTimers) { return; }
    cancelCopyTimer(button);
    const key = button.id || ('copy-' + Math.random().toString(36).slice(2, 10));
    button.dataset.copyTimerKey = key;
    chatStateCache.copyTimers[key] = setTimeout(() => {
      delete chatStateCache.copyTimers[key];
      if (button.dataset) { delete button.dataset.copyTimerKey; }
      setCopyState(button, 'idle', defaultLabel);
    }, delayMs);
  };
  const handlePerMessageCopy = (event) => {
    const button = event && event.target && event.target.closest ? event.target.closest('.chat-copy') : null;
    if (!button || !chatStateCache.messages) { return; }
    const index = parseInt(button.dataset.copyIndex, 10);
    const message = Number.isFinite(index) ? chatStateCache.messages[index] : null;
    if (!message || message.role !== 'assistant') { return; }
    const text = typeof message.text === 'string' ? message.text : '';
    writeClipboardText(text).then(() => {
      setCopyState(button, 'copied', 'Copied');
      scheduleCopyReset(button, 'Copy', 1800);
    }).catch(() => {
      setCopyState(button, 'failed', 'Copy failed');
      setChatState('Browser blocked the clipboard write; copy this response manually.', true);
      scheduleCopyReset(button, 'Copy', 2400);
    });
  };
  const handleSinceCopy = () => {
    const button = document.getElementById('chat-copy-since');
    if (!button || button.disabled) { return; }
    const text = chatStateCache.sinceLastUserText || '';
    if (!text) { return; }
    writeClipboardText(text).then(() => {
      setCopyState(button, 'copied', 'Copied');
      scheduleCopyReset(button, 'Copy since my last message', 1800);
    }).catch(() => {
      setCopyState(button, 'failed', 'Copy failed');
      setChatState('Browser blocked the clipboard write; copy the responses manually.', true);
      scheduleCopyReset(button, 'Copy since my last message', 2400);
    });
  };
  // Pure projection helper: returns the joined plain text of every visible
  // assistant message after the most recent visible user message. Uses the
  // already-filtered messages array (server projection from PR #63), so
  // hidden toolUse / delivery-mirror / system rows never enter the result.
  const computeSinceLastUserText = (messages) => {
    if (!Array.isArray(messages) || !messages.length) { return ''; }
    let lastUserIndex = -1;
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i] && messages[i].role === 'user') { lastUserIndex = i; break; }
    }
    const tail = lastUserIndex >= 0 ? messages.slice(lastUserIndex + 1) : messages.filter((m) => m && m.role === 'assistant');
    const parts = tail.filter((m) => m && m.role === 'assistant' && typeof m.text === 'string' && m.text.length > 0).map((m) => m.text);
    return parts.join('\n\n');
  };
  const updateSinceCopyButton = () => {
    const button = document.getElementById('chat-copy-since');
    if (!button) { return; }
    const hasText = !!(chatStateCache.sinceLastUserText && chatStateCache.sinceLastUserText.length);
    if (hasText) { button.hidden = false; button.disabled = false; }
    else { button.disabled = true; if (!chatStateCache.messages || !chatStateCache.messages.length) { button.hidden = true; } }
  };
  const bindChatCopyControls = () => {
    const history = document.getElementById('chat-history');
    if (history && (!history.dataset || history.dataset.copyBound !== 'true')) {
      if (history.dataset) { history.dataset.copyBound = 'true'; }
      if (typeof history.addEventListener === 'function') {
        history.addEventListener('click', handlePerMessageCopy);
      }
    }
    const since = document.getElementById('chat-copy-since');
    if (since && (!since.dataset || since.dataset.sinceBound !== 'true')) {
      if (since.dataset) { since.dataset.sinceBound = 'true'; }
      if (typeof since.addEventListener === 'function') {
        since.addEventListener('click', handleSinceCopy);
      }
    }
    updateSinceCopyButton();
  };
  const refreshChatHistory = async () => {
    try {
      const response = await fetch(CHAT_HISTORY_URL, {method: 'GET', headers: {'Accept': 'application/json'}, cache: 'no-store'});
      if (!response.ok) { throw new Error('chat history request failed: ' + response.status); }
      renderChatHistory(await response.json());
    } catch (error) {
      // Poll failure. Preserve all caches (chat-history HTML, agent indicator,
      // scroll position). The chat-state banner shows the existing bounded stale
      // message; the agent indicator does NOT flip Working -> Idle.
      renderChatHistory({session: {agent: 'trading-manager', status: 'unavailable'}, messages: []});
    }
  };
  const sendChatMessage = async (message) => {
    const input = document.getElementById('chat-message');
    const original = input ? input.value : String(message || '');
    const trimmed = String(original || '').trim();
    if (!trimmed) { setChatState('Enter a message before sending.', true); return; }
    if (trimmed.length > 4000) { setChatState('Message is too long; maximum is 4000 characters.', true); return; }
    setComposeState(original, 'sending');
    setChatState('Sending message to trading-manager…', false);
    try {
      const response = await fetch(CHAT_SEND_URL, {method: 'POST', headers: {'Accept': 'application/json', 'Content-Type': 'application/json'}, cache: 'no-store', body: JSON.stringify({message: trimmed})});
      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.ok !== true) { throw new Error(result.error || 'chat send failed'); }
      setComposeState('', 'idle');
      setChatState('Message sent. Trading-manager is working; waiting for response…', false);
      await refreshChatHistory();
    } catch (error) {
      setComposeState(original, 'idle');
      setChatState('Message send failed; existing chat history is preserved.', true);
    }
  };
  const bindChatForm = () => {
    const form = document.getElementById('chat-form');
    const input = document.getElementById('chat-message');
    if (!form || typeof form.addEventListener !== 'function') { return; }
    if (form.dataset && form.dataset.bound === 'true') { return; }
    if (form.dataset) { form.dataset.bound = 'true'; }
    form.addEventListener('submit', (event) => { event.preventDefault(); sendChatMessage(input && input.value); });
  };
  const refreshDashboard = async () => {
    try {
      const previousX = window.scrollX;
      const previousY = window.scrollY;
      const activeTab = selectedTab();
      captureChatUiState();
      const response = await fetch(SNAPSHOT_URL, {method: 'GET', headers: {'Accept': 'application/json'}, cache: 'no-store'});
      if (!response.ok) { throw new Error('snapshot request failed: ' + response.status); }
      const snapshot = await response.json();
      content.innerHTML = renderSnapshot(snapshot);
      if (activeTab === 'chat') { restoreChatUiState(); }
      switchTab(activeTab);
      bindTabs();
      if (activeTab === 'chat') { restoreChatUiState(); }
      window.scrollTo(previousX, previousY);
      bindChatForm();
      bindChatCopyControls();
      setWarning('');
    } catch (error) {
      setWarning('Dashboard update failed; showing the last known snapshot. Retrying every 15 seconds.');
    }
  };
  content.addEventListener('click', (event) => { const button = event.target.closest && event.target.closest('[data-tab]'); if (button && content.contains(button)) { event.preventDefault(); switchTab(button.dataset.tab); } });
  bindTabs();
  bindChatCopyControls();
  window.engineeringDashboard = {refreshDashboard, renderSnapshot, switchTab, selectedTab, refreshChatHistory, renderChatHistory, sendChatMessage, POLL_INTERVAL_MS, CHAT_POLL_INTERVAL_MS, SNAPSHOT_URL, CHAT_HISTORY_URL, CHAT_SEND_URL};
  window.setInterval(refreshDashboard, POLL_INTERVAL_MS);
  window.setInterval(refreshChatHistory, CHAT_POLL_INTERVAL_MS);
})();
</script>
'''
    return script.replace("__SNAPSHOT_ROUTE__", SNAPSHOT_ROUTE).replace("__CHAT_HISTORY_ROUTE__", CHAT_HISTORY_ROUTE).replace("__CHAT_SEND_ROUTE__", CHAT_SEND_ROUTE)


def _engineering_health_section(health: EngineeringHealthSummary | None, warnings: tuple[HealthWarning, ...]) -> str:
    if health is None:
        return _card("Engineering health", _definition_list({"Overall status": "Unavailable", "Warnings": len(warnings)}))
    return _card(
        "Engineering health",
        _definition_list(
            {
                "Overall status": health.overall_status,
                "Repository safe": health.repository_safe,
                "Branch": health.current_branch,
                "Commit": health.current_commit,
                "Last successful regression": health.last_successful_regression_run,
                "Degraded sources": ", ".join(health.degraded_sources),
                "Warnings": health.warning_count,
            }
        ),
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


def _current_tasks_section(tasks: tuple[TaskStatusSummary, ...]) -> str:
    items = []
    for task in tasks:
        items.append(
            "<li>"
            f"<strong>{_html(task.task_id)}</strong> {_html(task.title)} — {_html(task.status)} "
            f"({_html(task.completion_percent)}%)<br>Agent: {_html(task.assigned_agent)} · Phase: {_html(task.current_phase)}"
            "</li>"
        )
    return f"<section><h2>Current agent activity</h2><ul>{''.join(items) or '<li>No active engineering task.</li>'}</ul></section>"


def _blockers_section(blockers: tuple[str, ...]) -> str:
    items = "".join(f"<li>{_html(blocker)}</li>" for blocker in blockers)
    return f"<section><h2>Blockers and approvals needed</h2><ul>{items or '<li>None currently recorded.</li>'}</ul></section>"


def _live_activity_section(activity: tuple[AgentActivitySummary, ...]) -> str:
    rows = []
    for item in activity:
        rows.append(
            "<tr>"
            f"<td>{_html(item.status)}</td>"
            f"<td>{_html(item.agent_name)}</td>"
            f"<td>{_html(item.task_id)} {_html(item.task_title)}</td>"
            f"<td>{_html(item.phase)}</td>"
            f"<td><code>{_html(item.branch)}</code></td>"
            f"<td><code>{_html(item.run_id)}</code></td>"
            f"<td>{_html(item.started_at)}</td>"
            f"<td>{_html(item.elapsed_seconds)}</td>"
            f"<td>{_html(item.latest_activity)}</td>"
            f"<td>{_html(item.last_completed_action)}</td>"
            f"<td>{_html(item.blocker)}</td>"
            f"<td>{_html(item.timeout_state)} / {_html(item.recovery_state)}</td>"
            "</tr>"
        )
    header = (
        "<tr><th>Status</th><th>Agent</th><th>Task</th><th>Phase</th><th>Branch</th>"
        "<th>Run ID</th><th>Started</th><th>Elapsed seconds</th><th>Latest activity</th>"
        "<th>Last completed action</th><th>Blocker</th><th>Timeout / recovery</th></tr>"
    )
    body = "".join(rows) or "<tr><td colspan='12'>No active engineering-agent activity.</td></tr>"
    return f"<section><h2>Live agent activity</h2><table>{header}{body}</table></section>"


def _recent_executions_section(executions: tuple[RecentExecutionSummary, ...]) -> str:
    rows = []
    for item in executions:
        rows.append(
            "<tr>"
            f"<td>{_html(item.final_status)}</td>"
            f"<td>{_html(item.agent_name)}</td>"
            f"<td>{_html(item.task_id)}</td>"
            f"<td><code>{_html(item.branch)}</code></td>"
            f"<td><code>{_html(item.run_id)}</code></td>"
            f"<td>{_html(item.completed_at)}</td>"
            f"<td>{_html(item.elapsed_seconds)}</td>"
            f"<td>{_html(item.result_summary)}</td>"
            "</tr>"
        )
    header = "<tr><th>Status</th><th>Agent</th><th>Task</th><th>Branch</th><th>Run ID</th><th>Completed</th><th>Duration seconds</th><th>Result</th></tr>"
    body = "".join(rows) or "<tr><td colspan='8'>No recent executions.</td></tr>"
    return f"<section><h2>Recent executions</h2><table>{header}{body}</table></section>"


def _testing_section(testing: TestingSummary | None) -> str:
    if testing is None:
        return _card("Testing status", _definition_list({"Latest status": "Unavailable"}))
    return _card(
        "Testing status",
        _definition_list(
            {
                "Latest status": testing.latest_status,
                "Warnings": testing.warning_count,
                "Full suite completed": testing.full_suite.completed_at if testing.full_suite else None,
                "Regression completed": testing.regression.completed_at if testing.regression else None,
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
                f"<code>{_html(data.get('path'))}</code> {_html(data.get('generated_at'))} {_html(data.get('outcome'))}"
                "</li>"
            )
    return f"<section><h2>Recent reports</h2><ul>{''.join(items) or '<li>None</li>'}</ul></section>"


def _events_section(events: tuple[Mapping[str, object], ...]) -> str:
    items = []
    for event in events:
        label = event.get("event_type") or event.get("type") or event.get("message") or "event"
        task = event.get("task_id")
        occurred = event.get("occurred_at")
        payload = event.get("payload")
        payload_text = f" <span>{_html(payload)}</span>" if payload else ""
        items.append(
            f"<li><strong>{_html(label)}</strong> {_html(task)} "
            f"<code>{_html(occurred)}</code>{payload_text}</li>"
        )
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
