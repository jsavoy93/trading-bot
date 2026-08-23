# Engineering Dashboard mobile command center

- Current layout problems found: `/engineering` rendered roughly 25 sections and 4 wide tables in one long page, making mobile users scroll through repository, workflow, activity, backlog, reports, timeline, and health details before finding the high-level state. The only stable refresh hooks were `#dashboard-content`, `#update-warning`, and the polling script.
- Final tab structure: Overview, Activity, Backlog, Timeline, Reports, Health.
- Overview fields: health, project, branch, repository safe state, current task, agent, execution status, phase, elapsed, latest activity, last completed action, blocker summary, and DONE/REVIEW/TODO/BLOCKED backlog counts.
- Mobile CSS/layout strategy: compact shell, sticky horizontally-scrollable tab bar, two-column mobile overview cards, detail cards/lists instead of tables, no normal horizontal page overflow, 44px tab tap targets, safe visual truncation for branch/run IDs with titles.
- Polling interaction: preserved existing `GET /api/engineering/snapshot` every 15 seconds; no meta refresh; failures keep last-known data and bounded warning; recovery clears warning; selected tab survives polling and is persisted with localStorage.
- Safety: read-only only; no backend behavior change, workflow semantic change, trading logic change, write controls, WebSockets, server push, process inspection, raw stdout/stderr, prompts, private reasoning, secrets, or unbounded logs.
- Focused tests: `.venv/bin/python -m pytest tests/test_dashboard_api_app.py tests/test_dashboard_timeline.py tests/test_dashboard_engineering_read_model.py tests/test_dashboard_api_provider.py -q` → `79 passed, 2 warnings`.
- Full suite: `.venv/bin/python -m pytest -q` → `788 passed, 85 warnings`.
- `git diff --check`: PASS.
- Manual mobile verification: local ASGI/mobile-structure harness PASS: `/engineering` 200 in 62.889 ms, snapshot 200 in 64.495 ms, 6 tabs, Overview default, no meta refresh, no tables, sticky tabs, mobile media CSS, overflow guard, 44px tap target, scroll preservation hook, localStorage tab persistence. Actual mobile Safari/Termius device unavailable in this environment.
