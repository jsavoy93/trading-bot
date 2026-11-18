"""Tests for: optimizer shape fix, swing-sleeve panel, error summary."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.analysis.portfolio_optimizer import PortfolioOptimizer


class _Bot:
    """Fake bot: every symbol has 60 days of history except MISSING."""
    def __init__(self, missing=("MISSING",)):
        self.missing = set(missing)
        rng = np.random.default_rng(1)
        self.data = {}
        for i, s in enumerate(["AAA", "BBB", "CCC"]):
            close = 100 * np.exp(np.cumsum(rng.normal(0.001 * (i + 1), 0.01, 60)))
            self.data[s] = pd.DataFrame({"close": close})

    def get_market_data(self, sym):
        if sym in self.missing:
            return None
        return self.data.get(sym)


def test_current_sharpe_ignores_symbol_without_history():
    opt = PortfolioOptimizer()
    syms = ["AAA", "BBB", "MISSING", "CCC"]
    weights = [0.25, 0.25, 0.25, 0.25]
    # Before the fix this raised "shapes (4,) and (3,) not aligned".
    sharpe = opt.compute_current_sharpe(syms, weights, _Bot())
    assert np.isfinite(sharpe)


def test_current_sharpe_matches_when_all_symbols_have_history():
    opt = PortfolioOptimizer()
    bot = _Bot()
    s1 = opt.compute_current_sharpe(["AAA", "BBB", "CCC"], [0.5, 0.3, 0.2], bot)
    s2 = opt.compute_current_sharpe(["AAA", "BBB", "MISSING", "CCC"], [0.5, 0.3, 0.9, 0.2], bot)
    assert s1 == pytest.approx(s2)


def test_current_sharpe_zero_weight_on_history_symbols():
    opt = PortfolioOptimizer()
    assert opt.compute_current_sharpe(["AAA", "BBB", "MISSING"], [0.0, 0.0, 1.0], _Bot()) == 0.0


@pytest.fixture
def dash():
    import dashboard as _dashboard
    return _dashboard


def _make_c4_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE positions(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,
        signal_date TEXT, signal_atr14 REAL, entry_order_coid TEXT, entry_date TEXT,
        entry_price REAL, qty REAL, status TEXT, exit_date TEXT, exit_price REAL,
        exit_reason TEXT, created_ts TEXT, exit_time TEXT, realized_pnl REAL)""")
    rows = [("MMM", "2026-11-16", 180.0, 2.0, "open", None),
            ("XOM", "2026-11-11", 160.0, 10.0, "closed", 50.0),
            ("IQV", "2026-11-12", 200.0, 1.0, "closed", -20.0),
            ("ABC", None, None, None, "pending_fill", None)]
    for sym, ed, ep, q, st, pnl in rows:
        conn.execute("INSERT INTO positions(symbol, signal_date, signal_atr14, entry_order_coid,"
                     " entry_date, entry_price, qty, status, created_ts, realized_pnl)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?)", (sym, "x", 1.0, "c", ed, ep, q, st, "t", pnl))
    conn.commit()
    conn.close()


def test_c4_sleeve_reads_ledger(dash, tmp_path):
    db = tmp_path / "live_paper.db"
    _make_c4_db(db)
    before = db.read_bytes()
    out = dash.read_c4_sleeve(db, {"MMM": 190.0})
    assert out["available"] is True
    assert [p["symbol"] for p in out["open"]] == ["MMM", "ABC"]
    assert out["open"][0]["unrealized_pnl"] == pytest.approx(20.0)
    assert out["open"][1]["unrealized_pnl"] is None
    assert out["unrealized_pnl"] == pytest.approx(20.0)
    assert out["closed_count"] == 2
    assert out["realized_pnl"] == pytest.approx(30.0)
    assert db.read_bytes() == before  # read-only


def test_c4_sleeve_missing_ledger(dash, tmp_path):
    out = dash.read_c4_sleeve(tmp_path / "nope.db", {})
    assert out["available"] is False and out["error"]


def test_rsi2_sleeve_reads_state_and_events(dash, tmp_path):
    now = datetime.now(timezone.utc)
    (tmp_path / "state.json").write_text(json.dumps({"open": {
        "rsi2-FSLR-1": {"symbol": "FSLR", "qty": 10, "entry_fill_px": 170.0,
                        "entry_date": "2026-11-12", "stop_px": 150.0, "entry_filled": True}}}))
    events = [
        {"type": "close", "entry_id": "a", "ts": str(now)},
        {"type": "close", "entry_id": "a", "ts": str(now)},
        {"type": "close", "entry_id": "b", "ts": str(now)},
        {"type": "stop_error", "ts": str(now)},
        {"type": "exit_error", "ts": str(now - timedelta(days=30))},
    ]
    (tmp_path / "swing_events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n")
    out = dash.read_rsi2_sleeve(tmp_path, {"FSLR": 175.0})
    assert out["available"] is True
    assert out["open"][0]["unrealized_pnl"] == pytest.approx(50.0)
    assert out["open"][0]["stop_price"] == 150.0
    assert out["closed_count"] == 2
    assert out["recent_errors"] == 1
    assert out["realized_pnl"] is None


def test_swing_endpoint_survives_missing_files(dash, tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "SWING_C4_DB", tmp_path / "x.db")
    monkeypatch.setattr(dash, "SWING_RSI2_LOG_DIR", tmp_path)
    monkeypatch.setattr(dash, "trading_client", None)
    out = dash.api_swing_sleeves()
    assert [s["available"] for s in out["sleeves"]] == [False, False]


def test_error_summary_counts_recent_errors_only(dash, tmp_path):
    log = tmp_path / "trading_bot.log"
    now = datetime.now()
    old = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    new = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    log.write_text("\n".join([
        f"{old} - ERROR - Portfolio optimisation failed: shapes (9,) and (8,)",
        f"{new} - ERROR - Portfolio optimisation failed: shapes (9,) and (8,)",
        f"{new} - ERROR - Portfolio optimisation failed: shapes (7,) and (6,)",
        f"{new} - INFO - fine",
        f"{new} - CRITICAL - boom",
        "garbage line - ERROR - no timestamp",
    ]) + "\n")
    out = dash.summarize_log_errors(log, hours=24)
    assert out["total"] == 3
    assert out["top"][0]["count"] == 2
    assert out["top"][0]["message"].startswith("Portfolio optimisation failed")


def test_error_summary_missing_log(dash, tmp_path):
    assert dash.summarize_log_errors(tmp_path / "none.log")["error"]


def test_template_has_panels():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "templates" / "dashboard.html").read_text()
    assert 'id="swing-sleeves-card"' in html and "/api/swing-sleeves" in html
    assert 'id="error-summary-card"' in html and "/api/error-summary" in html
