# Engineering Dashboard smooth-refresh improvement

- Root cause: `/engineering` used `<meta http-equiv='refresh' content='15'>`, so every 15-second update navigated/reloaded the full page even though backend snapshot latency was healthy.
- Refresh implementation: initial `GET /engineering` still renders the dashboard shell and current snapshot server-side; the page now polls existing read-only `GET /api/engineering/snapshot` and replaces the visible dashboard content in place.
- Polling interval: 15 seconds.
- Failure behavior: failed polls keep the last-known snapshot visible, show a bounded stale/update warning, and clear the warning on the next successful poll.
- Safety: no write controls, WebSockets, server push, trading logic changes, workflow semantic changes, process inspection, raw stdout/stderr, prompts, secrets, or snapshot caching added.
- Manual verification: local ASGI checks showed `/engineering` status 200 in 92.129 ms with no meta refresh; `/api/engineering/snapshot` status 200 in 98.618 ms; HTML contains the snapshot poll endpoint, 15-second interval, scroll-preservation hook, and bounded warning text. Node-based browser harness verified successful refresh, failure preservation, warning recovery, HTML escaping, and scroll position preservation. Actual mobile Safari/Termius device was not available in this environment.
- Focused tests: `.venv/bin/python -m pytest tests/test_dashboard_api_app.py tests/test_dashboard_engineering_read_model.py tests/test_dashboard_api_provider.py tests/test_dashboard_timeline.py -q` → `74 passed, 2 warnings`.
- Full suite: `.venv/bin/python -m pytest -q` → `783 passed, 86 warnings`.
- `git diff --check`: PASS.
