"""BOT-001: Dashboard truthfulness tests.

Proves that:
  - /api/runtime-status returns three independent booleans.
  - The three tiers (alpaca_api_reachable, smartbot_runner_active,
    active_session_id) are reported independently and never conflated.
  - /api/start-session does NOT spawn the bot; response makes that
    explicit (bot_started=False).
  - /api/stop-session closes stale rows but does not stop the runner.
  - The 3-tier status dot is rendered correctly for each tier state
    (green/yellow/red).
  - The status dot legend is rendered honestly.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def client(monkeypatch):
    """Build a TestClient for dashboard.py with isolated SQLite DB.

    Strategy: patch DB_PATH on BOTH the `database.sqlite_db` module (the
    one dashboard.py imports via `sys.path.append("src")`) AND the
    `src.database.sqlite_db` module (used by tests). Then call
    _init_schema() against the new path on the dashboard-side module.
    """
    import tempfile
    import os
    import sys

    # 1) Patch DB_PATH on BOTH module references BEFORE any import.
    import src.database.sqlite_db as sqlite_mod_src
    # Force the dashboard-style import path: src must be in sys.path so
    # `from database.sqlite_db import ...` resolves to the same package.
    if "/root/.openclaw/workspace/trading-bot/src" not in sys.path:
        sys.path.insert(0, "/root/.openclaw/workspace/trading-bot/src")
    import database.sqlite_db as sqlite_mod

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    new_path = type(sqlite_mod.DB_PATH)(tmp.name)
    monkeypatch.setattr(sqlite_mod, "DB_PATH", new_path)
    monkeypatch.setattr(sqlite_mod_src, "DB_PATH", new_path)

    # 2) Run schema migration on the dashboard-side module (the one
    # actually used by the dashboard).
    sqlite_mod.sqlite_db._init_schema()

    # 2b) Pre-close any ACTIVE/OPEN rows so cross-test pollution from earlier
    # tests (which may have constructed SmartTradingBot instances and left
    # rows behind) does not affect this test. Use a far-future cutoff so
    # EVERY open row in this fresh DB is reaped; tests that need an ACTIVE
    # row create it explicitly after fixture setup completes.
    sqlite_mod.sqlite_db.close_stale_sessions(cutoff_iso="2999-01-01T00:00:00")

    # 3) Patch the Alpaca client so we don't need real credentials.
    if "/root/.openclaw/workspace/trading-bot" not in sys.path:
        sys.path.insert(0, "/root/.openclaw/workspace/trading-bot")
    import dashboard as dash_module
    dash_module.trading_client = MagicMock()
    dash_module.trading_client.get_account.return_value = MagicMock(
        portfolio_value="100000.0",
        cash="50000.0",
        buying_power="200000.0",
        pattern_day_trader=False,
        trading_blocked=False,
        transfers_blocked=False,
    )

    from fastapi.testclient import TestClient
    app = dash_module.app
    with TestClient(app) as c:
        yield c, dash_module

    try:
        os.unlink(tmp.name)
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────
# /api/runtime-status endpoint
# ─────────────────────────────────────────────────────────────────────────

def test_runtime_status_returns_three_independent_tiers(client) -> None:
    """All three tiers present, each its own boolean."""
    c, _ = client
    # Patch is_smartbot_runner_active to False for determinism.
    with patch("dashboard.is_smartbot_runner_active", return_value=False):
        r = c.get("/api/runtime-status")
    assert r.status_code == 200
    body = r.json()
    assert set(["alpaca_api_reachable", "smartbot_runner_active", "active_session_id",
                "fully_ready", "checked_at"]).issubset(body.keys())
    assert isinstance(body["alpaca_api_reachable"], bool)
    assert isinstance(body["smartbot_runner_active"], bool)
    assert body["active_session_id"] is None or isinstance(body["active_session_id"], int)
    assert isinstance(body["fully_ready"], bool)
    assert body["smartbot_runner_active"] is False
    assert body["fully_ready"] is False


def test_runtime_status_fully_ready_requires_all_three_tiers(client) -> None:
    """fully_ready must NOT be True unless alpaca AND runner AND session
    are all healthy. Prevents the legacy green-dot conflation."""
    c, dash_module = client
    with patch.object(dash_module, "is_smartbot_runner_active", return_value=True):
        r = c.get("/api/runtime-status")
    body = r.json()
    assert body["smartbot_runner_active"] is True
    assert body["fully_ready"] is False, (
        "fully_ready=False because active_session_id is None even when runner is on"
    )


def test_runtime_status_reports_active_session_when_present(client) -> None:
    c, dash_module = client
    # Create an ACTIVE row directly.
    dash_module.simple_rest.create_session(bot_version="2.1.0", notes="t")
    with patch.object(dash_module, "is_smartbot_runner_active", return_value=True):
        r = c.get("/api/runtime-status")
    body = r.json()
    assert body["active_session_id"] is not None
    assert body["fully_ready"] is True, (
        "All three tiers true → fully_ready must be True"
    )


def test_runtime_status_does_not_conflate_alpaca_with_runner(client) -> None:
    """If Alpaca is reachable but runner is not, the legacy green dot would
    have been misleading. /api/runtime-status must report this honestly."""
    c, dash_module = client
    with patch.object(dash_module, "is_smartbot_runner_active", return_value=False):
        r = c.get("/api/runtime-status")
    body = r.json()
    assert body["alpaca_api_reachable"] is True
    assert body["smartbot_runner_active"] is False
    assert body["fully_ready"] is False


# ─────────────────────────────────────────────────────────────────────────
# /api/start-session: must NOT spawn the bot
# ─────────────────────────────────────────────────────────────────────────

def test_start_session_does_not_spawn_bot(client) -> None:
    c, _ = client
    r = c.post("/api/start-session")
    assert r.status_code == 200
    body = r.json()
    assert body["bot_started"] is False, (
        "api_start_session must NOT spawn SmartBot (BOT-001 contract)"
    )
    assert "session_id" in body
    assert "message" in body
    assert "runtime_status" in body


def test_start_session_response_mentions_runner_disabled(client) -> None:
    c, _ = client
    r = c.post("/api/start-session")
    body = r.json()
    assert "smartbot-runner.service" in body["message"]
    assert "disabled" in body["message"].lower() or "does not" in body["message"].lower()


# ─────────────────────────────────────────────────────────────────────────
# /api/stop-session: closes stale rows, does NOT stop runner
# ─────────────────────────────────────────────────────────────────────────

def test_stop_session_closes_stale_rows_and_does_not_stop_bot(client) -> None:
    c, dash_module = client
    # Seed a stale ACTIVE row by manually inserting an old session.
    sid = dash_module.simple_rest.create_session(bot_version="2.1.0", notes="stale")
    dash_module.simple_rest.update_session(
        sid,
        {"session_start": "2000-01-01T00:00:00"},
    )
    r = c.post("/api/stop-session")
    assert r.status_code == 200
    body = r.json()
    assert body["bot_stopped"] is False
    assert body["sessions_closed"] == 1


# ─────────────────────────────────────────────────────────────────────────
# SPA template: 3-tier status dot
# ─────────────────────────────────────────────────────────────────────────

def test_template_renders_yellow_dot_when_alpaca_only(client) -> None:
    """Alpaca reachable, runner off → yellow dot with honest tooltip."""
    c, dash_module = client
    with patch.object(dash_module, "is_smartbot_runner_active", return_value=False):
        r = c.get("/")
    assert r.status_code == 200
    html = r.text
    assert "status-yellow" in html
    assert "API Online, Bot Off" in html
    assert "Alpaca reachable" in html
    assert "smartbot-runner.service is not active" in html


def test_template_renders_green_dot_when_fully_ready(client) -> None:
    c, dash_module = client
    dash_module.simple_rest.create_session(bot_version="2.1.0", notes="t")
    with patch.object(dash_module, "is_smartbot_runner_active", return_value=True):
        r = c.get("/")
    html = r.text
    assert "status-green" in html
    assert "Running" in html


def test_template_renders_red_dot_when_alpaca_unreachable(client) -> None:
    c, dash_module = client
    # Make get_account_info return empty (no Alpaca).
    with patch.object(dash_module, "get_account_info", return_value={}):
        r = c.get("/")
    html = r.text
    assert "status-red" in html
    assert "API Offline" in html
    assert "Alpaca paper API is unreachable" in html


def test_template_includes_botAction_javascript(client) -> None:
    """The Start/Stop buttons must use the botAction() helper that posts
    to the new endpoints and renders the response honestly."""
    c, _ = client
    r = c.get("/")
    html = r.text
    assert "async function botAction" in html
    assert "/api/start-session" in html
    assert "/api/stop-session" in html
    assert "bot_started" in html  # rendered from response


def test_template_no_longer_shows_misleading_start_session_link(client) -> None:
    """The legacy <a href='/api/start-session'> link is gone (it was a GET
    that never worked correctly anyway; replaced by the botAction() button)."""
    c, _ = client
    r = c.get("/")
    html = r.text
    # The bot-action-result div exists.
    assert 'id="bot-action-result"' in html
    # The old <a href="/api/start-session" class="btn btn-secondary"> is gone.
    assert 'href="/api/start-session"' not in html


# ─────────────────────────────────────────────────────────────────────────
# is_smartbot_runner_active caching
# ─────────────────────────────────────────────────────────────────────────

def test_runner_active_detection_caches_for_2_seconds(client) -> None:
    c, dash_module = client
    # Invalidate any cache from earlier tests in this session.
    dash_module._smartbot_runner_active_cache = None
    dash_module._smartbot_runner_active_cache_ts = None
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="active\n", returncode=0)
        assert dash_module.is_smartbot_runner_active() is True
        assert dash_module.is_smartbot_runner_active() is True
        # subprocess.run only called once because of cache.
        assert mock_run.call_count == 1


def test_runner_active_detection_returns_false_on_subprocess_error(client) -> None:
    c, dash_module = client
    dash_module._smartbot_runner_active_cache = None
    dash_module._smartbot_runner_active_cache_ts = None
    with patch("subprocess.run", side_effect=OSError("no systemctl")):
        assert dash_module.is_smartbot_runner_active() is False
