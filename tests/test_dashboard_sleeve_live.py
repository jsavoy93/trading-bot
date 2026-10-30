"""DASH-SLEEVE: live sleeve panel tests.

Proves that:
  - /api/sleeve-live is a READ-ONLY GET endpoint (no new mutating routes).
  - The snapshot is broker-authoritative: quantities, market values and
    cash come from the (mocked) Alpaca PAPER account, never fabricated.
  - Per-sleeve attribution is derived from trades.sleeve ledger tags via
    SleevePositionBook (only FILLED rows with a valid 'L'/'S' tag count).
  - Legacy NULL-sleeve quantity is attributed to the "legacy" bucket
    (broker qty minus tagged qty), never guessed.
  - Ledger-vs-broker quantity mismatches are surfaced, not hidden.
  - Bot health fields (service state, last session, holds/data errors in
    the last hour) are reported without touching systemd state mutably.
  - The endpoint degrades safely when trading_client is None.

No real Alpaca calls are made: trading_client is a MagicMock, matching
the broker safety gate conventions in tests/conftest.py.
"""
import sqlite3
import tempfile
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _make_position(symbol, qty, mv, pl, price=100.0):
    p = MagicMock()
    p.symbol = symbol
    p.qty = str(qty)
    p.market_value = str(mv)
    p.unrealized_pl = str(pl)
    p.current_price = str(price)
    p.avg_entry_price = "90.0"
    return p


@pytest.fixture
def client(monkeypatch):
    """TestClient for dashboard.py with an isolated SQLite DB.

    Mirrors tests/test_bot001_dashboard_status.py: DB_PATH is patched on
    BOTH module references so dashboard reads hit the temp DB.
    """
    import src.database.sqlite_db as sqlite_mod_src
    if str(PROJECT_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
    import database.sqlite_db as sqlite_mod

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    monkeypatch.setattr(sqlite_mod, "DB_PATH", Path(tmp.name))
    monkeypatch.setattr(sqlite_mod_src, "DB_PATH", Path(tmp.name))
    sqlite_mod.sqlite_db._init_schema()

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    import dashboard as dash_module
    dash_module.trading_client = MagicMock()
    dash_module.trading_client.get_account.return_value = MagicMock(
        portfolio_value="100000.0",
        cash="40000.0",
        buying_power="80000.0",
        pattern_day_trader=False,
        trading_blocked=False,
        transfers_blocked=False,
    )
    # No broker positions by default; tests override.
    dash_module.trading_client.get_all_positions.return_value = []

    from fastapi.testclient import TestClient
    with TestClient(dash_module.app) as c:
        yield c, dash_module, sqlite_mod.sqlite_db

    try:
        os.unlink(tmp.name)
    except OSError:
        pass


def _db_path():
    """The monkeypatched DB path (module-level attr)."""
    import database.sqlite_db as _mod
    return _mod.DB_PATH


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _add_trade(db, symbol, side, filled_qty, sleeve, status="FILLED"):
    assert db.log_trade(None, {
        "symbol": symbol,
        "side": side,
        "qty": filled_qty,
        "filled_qty": filled_qty,
        "status": status,
        "sleeve": sleeve,
    })


def _add_failed_analysis(db_path, symbol, blocked_by, minutes_ago=5):
    ts = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO failed_analyses (symbol, timestamp, signal_type, "
        "failed_filters, blocked_by) VALUES (?, ?, 'BUY', '[]', ?)",
        (symbol, ts, blocked_by),
    )
    conn.commit()
    conn.close()


class TestSleeveLiveEndpointReadOnly:
    def test_endpoint_is_get_only(self, client):
        c, dash, _ = client
        routes = {
            (r.path, frozenset(r.methods))
            for r in dash.app.routes
            if getattr(r, "methods", None)
        }
        assert ("/api/sleeve-live", frozenset({"GET"})) in routes

    def test_no_new_mutating_routes(self, client):
        c, dash, _ = client
        mutating = [
            r.path for r in dash.app.routes
            if getattr(r, "methods", None)
            and r.methods - {"GET", "HEAD"}
        ]
        # Only the pre-existing session-control endpoints may mutate; the
        # new sleeve route must be read-only.
        assert sorted(mutating) == [
            "/api/start-session", "/api/stop-session",
        ]
        assert "/api/sleeve-live" not in mutating


class TestSleeveAttribution:
    def test_per_sleeve_split_and_legacy_bucket(self, client):
        c, dash, db = client
        # Broker (authoritative): 10 AAPL total.
        dash.trading_client.get_all_positions.return_value = [
            _make_position("AAPL", 10, 2000.0, 100.0, price=200.0)
        ]
        # Ledger: 6 shares tagged S, 2 tagged L, 2 untagged legacy.
        _add_trade(db, "AAPL", "BUY", 6, "S")
        _add_trade(db, "AAPL", "BUY", 2, "L")

        resp = c.get("/api/sleeve-live")
        assert resp.status_code == 200
        d = resp.json()
        assert d["paper_only"] is True
        assert d["cash"] == 40000.0
        sleeves = {s["sleeve"]: s for s in d["sleeves"]}
        assert set(sleeves) == {"S", "L", None}
        # Derived pro-rata split of the broker-authoritative numbers.
        assert sleeves["S"]["market_value"] == 1200.0  # 6/10 * 2000
        assert sleeves["S"]["unrealized_pl"] == 60.0
        assert sleeves["L"]["market_value"] == 400.0   # 2/10 * 2000
        assert sleeves[None]["market_value"] == 400.0  # 2/10 legacy
        pos_s = sleeves["S"]["positions"][0]
        assert pos_s["symbol"] == "AAPL" and pos_s["qty"] == 6

    def test_unfilled_and_invalid_rows_do_not_count(self, client):
        c, dash, db = client
        dash.trading_client.get_all_positions.return_value = [
            _make_position("MSFT", 5, 500.0, 10.0, price=100.0)
        ]
        _add_trade(db, "MSFT", "BUY", 5, "S", status="SUBMITTED")
        sleeves = {s["sleeve"]: s for s in c.get("/api/sleeve-live").json()["sleeves"]}
        # SUBMITTED rows never count -> everything is legacy/unattributed.
        assert sleeves["S"]["position_count"] == 0
        assert sleeves[None]["positions"][0]["qty"] == 5

    def test_sell_reduces_sleeve_qty(self, client):
        c, dash, db = client
        dash.trading_client.get_all_positions.return_value = [
            _make_position("TSLA", 4, 800.0, -20.0, price=200.0)
        ]
        _add_trade(db, "TSLA", "BUY", 10, "S")
        _add_trade(db, "TSLA", "SELL", 6, "S")
        sleeves = {s["sleeve"]: s for s in c.get("/api/sleeve-live").json()["sleeves"]}
        assert sleeves["S"]["positions"][0]["qty"] == 4


class TestReconciliation:
    def test_ledger_broker_mismatch_surfaced(self, client):
        c, dash, db = client
        # Broker says 10, ledger only accounts for 7 -> mismatch flagged.
        dash.trading_client.get_all_positions.return_value = [
            _make_position("NVDA", 10, 1000.0, 0.0, price=100.0)
        ]
        _add_trade(db, "NVDA", "BUY", 7, "S")
        h = c.get("/api/sleeve-live").json()["health"]
        assert h["mismatch_count"] == 1
        m = h["ledger_broker_mismatches"][0]
        assert m["symbol"] == "NVDA"
        assert m["ledger_qty"] == 7 and m["broker_qty"] == 10

    def test_no_mismatch_when_quantities_agree(self, client):
        c, dash, db = client
        dash.trading_client.get_all_positions.return_value = [
            _make_position("AAPL", 10, 2000.0, 0.0, price=200.0)
        ]
        _add_trade(db, "AAPL", "BUY", 10, "S")
        h = c.get("/api/sleeve-live").json()["health"]
        assert h["mismatch_count"] == 0


class TestHealth:
    def test_health_fields_present(self, client, monkeypatch):
        c, dash, db = client
        monkeypatch.setattr(dash, "is_smartbot_runner_active", lambda: True)
        monkeypatch.setattr(
            dash, "_sleeve_systemd_state", lambda unit="x": "active"
        )
        _add_failed_analysis(_db_path(), "GME", "Score < 55")
        _add_failed_analysis(
            _db_path(), "BBBY", "No market data"
        )
        h = c.get("/api/sleeve-live").json()["health"]
        assert h["smartbot_runner_active"] is True
        assert h["smartbot_service_state"] == "active"
        assert h["holds_last_hour"]["total_holds"] == 2
        assert h["holds_last_hour"]["data_errors"] == 1
        assert "last_session" in h

    def test_old_holds_excluded_from_last_hour(self, client):
        c, dash, db = client
        _add_failed_analysis(
            _db_path(), "OLD", "Score < 55", minutes_ago=120
        )
        h = c.get("/api/sleeve-live").json()["health"]
        assert h["holds_last_hour"]["total_holds"] == 0


class TestDegradation:
    def test_no_trading_client_is_safe(self, client):
        c, dash, db = client
        dash.trading_client = None
        resp = c.get("/api/sleeve-live")
        assert resp.status_code == 200
        d = resp.json()
        assert d["cash"] is None
        assert all(s["position_count"] == 0 for s in d["sleeves"])
        assert d["health"]["mismatch_count"] == 0

    def test_recent_trades_and_signals_sections_exist(self, client):
        c, dash, db = client
        _add_trade(db, "AAPL", "BUY", 3, "S")
        d = c.get("/api/sleeve-live").json()
        assert isinstance(d["recent_trades"], list)
        assert d["recent_trades"][0]["symbol"] == "AAPL"
        assert d["recent_trades"][0]["sleeve"] == "S"
        assert isinstance(d["recent_signals"], list)
        assert "notes" in d
