"""STRAT-025 Part 1 unit tests: risk caps, daily loss limit, earnings blackout,
kill switch, stop-after-fill sequencing (mock broker), paper guard.

Run (from the strat-025 worktree root, using the trading-bot venv):
  /root/.openclaw/workspace/trading-bot/.venv/bin/python -m pytest \
      tests/test_strat_025_engine.py -p no:cacheprovider -v
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from experiments.strat_025.engine import setups
from experiments.strat_025.engine.config import Config, ORDER_TAG
from experiments.strat_025.engine.engine import SwingEngine
from experiments.strat_025.engine.guard import PaperOnlyError, enforce_paper_only
from experiments.strat_025.engine.risk import (
    DailyLossLimit,
    EarningsBlackout,
    RiskManager,
    kill_switch_active,
    position_size,
    sector_cap_ok,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no ambient flags leak between tests; every flag defaults OFF."""
    for k in list(os.environ):
        if k.startswith("STRAT025_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    yield


# ------------------------------------------------------------------- guard
class TestPaperGuard:
    def test_refuses_without_env_var(self):
        with pytest.raises(PaperOnlyError):
            enforce_paper_only("https://paper-api.alpaca.markets")

    def test_refuses_non_paper_endpoint_even_with_env(self, monkeypatch):
        monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
        with pytest.raises(PaperOnlyError):
            enforce_paper_only("https://api.alpaca.markets")

    def test_refuses_live_optin_even_with_paper_env(self, monkeypatch):
        monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
        monkeypatch.setenv("STRAT025_ALLOW_LIVE", "1")
        with pytest.raises(PaperOnlyError):
            enforce_paper_only("https://paper-api.alpaca.markets")

    def test_accepts_paper_env_and_paper_endpoint(self, monkeypatch):
        monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
        enforce_paper_only("https://paper-api.alpaca.markets")  # no raise


# ------------------------------------------------------------------ sizing
class TestRiskCaps:
    def test_one_percent_cap_binds(self):
        # equity 100k, risk 1% -> $1000 budget; $2 risk/share -> 500 shares
        assert position_size(100_000, 50.0, 48.0, 1.0) == 500

    def test_stop_above_entry_refused(self):
        assert position_size(100_000, 50.0, 51.0, 1.0) == 0

    def test_zero_or_negative_inputs_refused(self):
        assert position_size(0, 50, 48, 1) == 0
        assert position_size(100_000, 0, 48, 1) == 0
        assert position_size(100_000, 50, 0, 1) == 0
        assert position_size(100_000, 50, 48, 0) == 0

    def test_below_sleeve_floor_refused(self):
        # $1 risk budget -> 1 share ($50 notional) < $600 floor -> refuse
        assert position_size(100, 50.0, 49.0, 1.0, floor_usd=600) == 0

    def test_max_notional_cap(self):
        n = position_size(1_000_000, 100.0, 90.0, 1.0, max_notional=10_000)
        assert n * 100.0 <= 10_000

    def test_whole_shares_only(self):
        assert position_size(100_000, 50.0, 47.5, 1.0) == int(1000 / 2.5)


class TestSectorCap:
    MAP = {"AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech", "XOM": "Energy"}

    def test_under_cap_ok(self):
        pos = [{"symbol": "AAPL"}]
        assert sector_cap_ok("MSFT", pos, self.MAP, max_per_sector=2)

    def test_at_cap_blocked(self):
        pos = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        assert not sector_cap_ok("NVDA", pos, self.MAP, max_per_sector=2)

    def test_unknown_sector_groups_together(self):
        pos = [{"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "CCC"}]
        assert not sector_cap_ok("DDD", pos, self.MAP, max_per_sector=3)


# ------------------------------------------------------------ daily loss halt
class TestDailyLossLimit:
    def test_halt_at_limit(self):
        assert DailyLossLimit(3.0).is_halted(100_000, 97_000)

    def test_no_halt_above_limit(self):
        assert not DailyLossLimit(3.0).is_halted(100_000, 97_001)

    def test_no_halt_on_gain(self):
        assert not DailyLossLimit(3.0).is_halted(100_000, 101_000)

    def test_zero_start_equity_never_halts(self):
        assert not DailyLossLimit(3.0).is_halted(0, -50)


# ----------------------------------------------------------- earnings blackout
class TestEarningsBlackout:
    BO = EarningsBlackout({"AAPL": ["2026-11-03"]}, window_days=1)

    def test_blocked_on_earnings_day(self):
        assert self.BO.is_blacked_out("AAPL", date(2026, 11, 3))

    def test_blocked_within_window(self):
        assert self.BO.is_blacked_out("AAPL", date(2026, 11, 2))
        assert self.BO.is_blacked_out("AAPL", date(2026, 11, 4))

    def test_outside_window_ok(self):
        assert not self.BO.is_blacked_out("AAPL", date(2026, 11, 6))

    def test_other_symbols_unaffected(self):
        assert not self.BO.is_blacked_out("MSFT", date(2026, 11, 3))

    def test_from_json_missing_file_empty(self, tmp_path):
        bo = EarningsBlackout.from_json(str(tmp_path / "nope.json"))
        assert bo.dates == {}


# ---------------------------------------------------------------- kill switch
class TestKillSwitch:
    def _cfg(self, tmp_path):
        return Config(kill_switch_file=str(tmp_path / "KILL"))

    def test_inactive_by_default(self, tmp_path):
        assert not kill_switch_active(self._cfg(tmp_path))

    def test_env_activates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STRAT025_KILL_SWITCH", "1")
        assert kill_switch_active(self._cfg(tmp_path))

    def test_file_activates(self, tmp_path):
        Path(str(tmp_path / "KILL")).write_text("halt")
        assert kill_switch_active(self._cfg(tmp_path))

    def test_blocks_entry_via_risk_manager(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STRAT025_KILL_SWITCH", "1")
        rm = RiskManager(self._cfg(tmp_path))
        ok, reason = rm.approve(symbol="AAPL", equity=100_000,
                                day_start_equity=100_000, positions=[])
        assert not ok and reason == "kill_switch"

    def test_engine_flattens_when_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STRAT025_KILL_SWITCH", "1")
        monkeypatch.setenv("STRAT025_KILL_FLATTEN", "1")
        cfg = Config(kill_switch_file=str(tmp_path / "KILL"), kill_flatten=True,
                     db_path=str(tmp_path / "t.db"))
        b = MockBroker()
        b.positions = [{"symbol": "AAPL", "qty": 100}]
        eng = SwingEngine(cfg, b, RiskManager(cfg))
        Path(str(tmp_path / "KILL")).write_text("halt")
        assert eng.handle_kill_switch() is True
        assert eng.broker.sells and eng.broker.get_positions() == []


# ---------------------------------------------------- stop-after-fill sequence
class MockBroker:
    """In-memory broker: fills market buys, records order sequencing."""

    def __init__(self, fail_stop=False):
        self.equity = 100_000
        self.orders: list[dict] = []
        self.positions: list[dict] = []
        self.sells = []
        self.fail_stop = fail_stop

    def get_account_equity(self):
        return self.equity

    def is_market_open(self):
        return True

    def _order(self, symbol, qty, side, otype, coid, **kw):
        o = {"id": f"o{len(self.orders)+1}", "symbol": symbol, "qty": qty,
             "side": side, "type": otype, "status": "filled",
             "client_order_id": coid, **kw}
        self.orders.append(o)
        return o

    def submit_market_buy(self, symbol, qty, coid):
        o = self._order(symbol, qty, "buy", "market", coid)
        o["filled_avg_price"] = 50.0
        self.positions.append({"symbol": symbol, "qty": qty})
        return o

    def submit_stop_sell(self, symbol, qty, stop_price, coid):
        if self.fail_stop:
            return {"id": f"o{len(self.orders)+1}", "status": "rejected",
                    "client_order_id": coid}
        return self._order(symbol, qty, "sell", "stop", coid,
                           stop_price=stop_price, status="held")

    def submit_market_sell(self, symbol, qty, coid):
        o = self._order(symbol, qty, "sell", "market", coid)
        o["filled_avg_price"] = 50.0
        self.sells.append(o)
        self.positions = [p for p in self.positions if p["symbol"] != symbol]
        return o

    def wait_for_fill(self, order_id, timeout_sec=20.0):
        for o in self.orders:
            if o["id"] == order_id:
                return o
        return None

    def get_positions(self):
        return list(self.positions)

    def cancel_order(self, order_id):
        pass


def _engine(tmp_path, broker, monkeypatch=None, **cfg_over):
    cfg = Config(db_path=str(tmp_path / "t.db"),
                 risk_per_trade_pct=1.0, **cfg_over)
    return SwingEngine(cfg, broker, RiskManager(cfg))


class TestStopAfterFill:
    def test_entry_then_stop_sequence_and_tag(self, tmp_path):
        b = MockBroker()
        eng = _engine(tmp_path, b)
        r = eng.enter_with_stop("AAPL", "pullback_trend", 50.0, 48.0)
        assert r["entered"] is True
        kinds = [(o["type"], o["side"], o["client_order_id"]) for o in b.orders]
        assert kinds[0][0] == "market" and kinds[0][1] == "buy"
        assert kinds[1][0] == "stop" and kinds[1][1] == "sell"
        for _, _, coid in kinds:  # every order tagged sl_short
            assert coid.startswith(ORDER_TAG + "-")
        stop = b.orders[1]
        assert abs(stop["stop_price"] - 48.0) < 1e-9
        # 1% of 100k = $1000 risk; $2/share stop distance -> 500 shares
        assert r["qty"] == 500
        # DB records the entry with its stop
        rows = sqlite3.connect(eng.cfg.db_path).execute(
            "SELECT status, stop_order_id FROM entries").fetchall()
        assert rows and rows[0][0] == "open" and rows[0][1] == stop["id"]

    def test_stop_placement_failure_flattens_immediately(self, tmp_path):
        b = MockBroker(fail_stop=True)
        eng = _engine(tmp_path, b)
        r = eng.enter_with_stop("AAPL", "pullback_trend", 50.0, 48.0)
        assert r["entered"] is False
        assert r["reason"] == "stop_place_failed_flattened"
        sides = [(o["type"], o["side"]) for o in b.orders]
        # sequencing: buy -> (stop rejected) -> immediate market sell flatten
        assert sides == [("market", "buy"), ("market", "sell")]
        assert b.get_positions() == []
        row = sqlite3.connect(eng.cfg.db_path).execute(
            "SELECT status, reason FROM entries").fetchone()
        assert row == ("failed", "stop_place_failed_flattened")

    def test_no_entry_without_fill(self, tmp_path):
        class NoFillBroker(MockBroker):
            def submit_market_buy(self, symbol, qty, coid):
                o = self._order(symbol, qty, "buy", "market", coid)
                o["status"] = "pending_new"
                o["id"] = "never"
                self.orders.append(o)
                return o

            def wait_for_fill(self, order_id, timeout_sec=20.0):
                return {"status": "pending_new"}

        eng = _engine(tmp_path, NoFillBroker())
        r = eng.enter_with_stop("AAPL", "pullback_trend", 50.0, 48.0)
        assert r["entered"] is False and r["reason"] == "entry_not_filled"
        # no stop, no position, no entry row marked open
        assert all(o["type"] != "stop" for o in eng.broker.orders)


# --------------------------------------------------- setup registry defaults
class TestSetupRegistry:
    def test_all_setups_default_off(self):
        for name in setups.registry():
            assert not setups.is_setup_enabled(name), f"{name} should default OFF"

    def test_env_flag_enables(self, monkeypatch):
        monkeypatch.setenv("STRAT025_SETUP_PULLBACK_TREND", "1")
        assert setups.is_setup_enabled("pullback_trend")

    def test_placeholders_never_fire(self):
        df = pd.DataFrame({"close": [1.0, 2.0]})
        assert setups.evaluate_all_enabled(df) == [] or all(
            not setups.evaluate(df, n) for n in setups.registry())

    def test_unknown_setup_disabled(self, monkeypatch):
        df = pd.DataFrame({"close": [1.0]})
        assert not setups.evaluate(df, "NOPE")
        monkeypatch.setenv("STRAT025_SETUP_NOPE", "1")
        assert "NOPE" not in setups.enabled_setups()  # unregistered -> never trades


# ------------------------------------------------- RiskManager integration
class TestRiskManagerGates:
    def test_daily_loss_halt_latches_for_day(self, tmp_path):
        cfg = Config(db_path=str(tmp_path / "t.db"), daily_loss_limit_pct=3.0)
        rm = RiskManager(cfg)
        ok, reason = rm.approve(symbol="XOM", equity=96_000,
                                day_start_equity=100_000, positions=[])
        assert not ok and reason == "daily_loss_halt"
        # latched: even recovered equity stays halted today
        ok2, reason2 = rm.approve(symbol="XOM", equity=100_000,
                                  day_start_equity=100_000, positions=[])
        assert not ok2 and reason2 == "daily_loss_halt"

    def test_blackout_blocks(self, tmp_path):
        cfg = Config(db_path=str(tmp_path / "t.db"))
        rm = RiskManager(cfg, blackout=EarningsBlackout({"AAPL": ["2026-11-03"]}))
        ok, reason = rm.approve(symbol="AAPL", equity=100_000,
                                day_start_equity=100_000, positions=[],
                                today=date(2026, 11, 3))
        assert not ok and reason == "earnings_blackout"

    def test_sector_cap_blocks(self, tmp_path):
        cfg = Config(db_path=str(tmp_path / "t.db"), max_positions_per_sector=2)
        rm = RiskManager(cfg, sector_map={"AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech"})
        ok, reason = rm.approve(symbol="MSFT", equity=100_000,
                                day_start_equity=100_000,
                                positions=[{"symbol": "AAPL"}, {"symbol": "NVDA"}])
        assert not ok and reason == "sector_cap"
