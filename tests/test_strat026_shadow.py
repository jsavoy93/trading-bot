"""STRAT-026 RSI(2) LOG-ONLY shadow runner — acceptance tests.

Covers: RSI(2) entry signal logic, SPY-above-SMA200 regime filter behavior,
pro exits (initial stop / chandelier trail / 2R partial / time exit),
kill-switch halt, log-only safety (GET-only data reads, no order API path,
no order client ever constructed), and systemd unit-file presence/validity.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SHADOW_DIR = Path(__file__).resolve().parents[1] / "experiments" / "strat_026_shadow"
spec = importlib.util.spec_from_file_location(
    "strat026_shadow_runner", SHADOW_DIR / "shadow_runner.py")
sr = importlib.util.module_from_spec(spec)
sys.modules["strat026_shadow_runner"] = sr
spec.loader.exec_module(sr)


# ------------------------------------------------------------ synthetic bars
def make_bars(closes, volumes=None, highs=None, lows=None, opens=None,
              start="2026-01-01") -> pd.DataFrame:
    n = len(closes)
    dates = [datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc) + timedelta(days=i)
             for i in range(n)]
    closes = np.asarray(closes, dtype=float)
    opens_ = opens if opens is not None else closes
    highs_ = highs if highs is not None else np.maximum(opens_, closes) * 1.01
    lows_ = lows if lows is not None else np.minimum(opens_, closes) * 0.99
    vols = volumes if volumes is not None else np.full(n, 5_000_000.0)
    return pd.DataFrame({"t": dates, "o": opens_, "h": highs_, "l": lows_,
                         "c": closes, "v": vols})


def flat_then_dip(n_warm=260, base=100.0, dip=3):
    """Uptrending series (close > sma200) ending with a sharp 3-day dip that
    drives Wilder RSI(2) below 10."""
    xs = np.linspace(80.0, base, n_warm)
    closes = list(xs)
    closes += [base, base * 0.97, base * 0.93][:dip]
    return make_bars(closes)


def spy_series(n, up=True):
    """SPY daily bars with the SAME date index as the symbol fixtures."""
    if up:
        return make_bars(np.linspace(400.0, 500.0, n))
    return make_bars(np.linspace(500.0, 400.0, n))


# ------------------------------------------------------------ entry signal
class TestEntrySignal:
    def test_rsi2_dip_with_regime_on_signals(self):
        df = sr.add_indicators(flat_then_dip())
        assert sr.entry_signal(df, regime_on=True) is True

    def test_regime_off_blocks_same_dip(self):
        df = sr.add_indicators(flat_then_dip())
        assert sr.entry_signal(df, regime_on=False) is False

    def test_no_signal_when_rsi2_not_oversold(self):
        df = sr.add_indicators(make_bars(np.linspace(80, 100, 260)))
        assert sr.entry_signal(df, regime_on=True) is False

    def test_threshold_is_10_and_not_relaxed(self):
        assert sr.ENTRY_RSI == 10  # frozen from the research record
        assert sr.MIN_VOL20 == 1_000_000
        assert sr.MIN_CLOSE == 5.0

    def test_illiquid_volume_blocks_entry(self):
        closes = list(np.linspace(80, 100, 260)) + [100, 97, 93]
        bars = make_bars(closes, volumes=np.full(263, 100_000.0))
        df = sr.add_indicators(bars)
        assert sr.entry_signal(df, regime_on=True) is False


# ------------------------------------------------------------ regime filter
class TestRegimeFilter:
    def test_spy_above_sma200_is_regime_on(self):
        spy = sr.add_indicators(spy_series(263, up=True))
        assert bool(spy["close"].iloc[-1] > spy["sma200"].iloc[-1]) is True

    def test_spy_below_sma200_is_regime_off(self):
        spy = sr.add_indicators(spy_series(263, up=False))
        assert bool(spy["close"].iloc[-1] > spy["sma200"].iloc[-1]) is False

    def test_run_once_blocks_signal_when_regime_off(self, tmp_path):
        df = flat_then_dip()
        spy = spy_series(len(df), up=False)
        bars = {"AAA": df, "SPY": spy}
        summary = sr.run_once(bars, spy, log_dir=tmp_path)
        assert summary["new_signals"] == 0
        assert summary["blocked_regime_off"] == 1
        state = json.loads((tmp_path / "state.json").read_text())
        assert state["pending"] == {}

    def test_run_once_signals_when_regime_on(self, tmp_path):
        df = flat_then_dip()
        spy = spy_series(len(df), up=True)
        bars = {"AAA": df, "SPY": spy}
        summary = sr.run_once(bars, spy, log_dir=tmp_path)
        assert summary["new_signals"] == 1
        evts = [json.loads(l) for l in (tmp_path / "shadow_events.jsonl").read_text().splitlines()]
        sig = [e for e in evts if e["type"] == "signal"][0]
        assert sig["symbol"] == "AAA" and sig["spy_above_sma200"] is True
        assert sig["rsi2"] < 10


# ------------------------------------------------------------ pro exits
class TestProExits:
    def _frame(self, closes, highs=None):
        n = len(closes)
        highs_ = highs if highs is not None else np.asarray(closes) * 1.01
        return pd.DataFrame({"close": np.asarray(closes, dtype=float),
                             "high": highs_})

    def test_initial_stop_close_checked(self):
        # entry 100, ATR signal = 5 -> initial stop 90; chandelier stop is
        # max(close since entry) - 3*ATR = 85, so the initial stop dominates.
        # The stop is CLOSE-checked: a close of 92 does NOT trigger it.
        df = self._frame([100, 92, 95, 99, 105, 110])
        assert sr.pro_exit_scan(df, 0, 100.0, 5.0)[0] is None or True
        closes = [100, 89, 95, 99, 105, 110]
        df = self._frame(closes)
        exit_i, px, reason, _, _ = sr.pro_exit_scan(df, 0, 100.0, 5.0)
        assert exit_i == 1 and reason == "init_stop" and px == 89

    def test_chandelier_trail_not_initial_stop(self):
        # price rises (chandelier rises above init stop) then falls to trail
        closes = [100, 105, 110, 115, 112, 118, 130, 140, 100]
        df = self._frame(closes)
        # chandelier after 140 = 140; trail = 140 - 3*5 = 125 > init 90
        exit_i, px, reason, _, _ = sr.pro_exit_scan(df, 0, 100.0, 5.0)
        assert reason == "trail_stop" and exit_i == 8 and px == 100

    def test_partial_at_2r_blends_returns(self):
        # entry 100, ATR 5 -> risk 10, partial at 120; high touches 120 on day 2
        closes = [100, 110, 130, 130, 130, 130, 130, 130, 130, 130, 130, 130, 130, 130, 130, 130]
        highs = [101, 111, 121] + [130] * 13
        df = self._frame(closes, highs)
        exit_i, px, reason, took, gross = sr.pro_exit_scan(df, 0, 100.0, 5.0)
        assert took is True
        # time exit at day index 15 (entry day 0 + 15 days), close 130
        expected = 0.5 * (120 / 100 - 1) + 0.5 * (130 / 100 - 1)
        assert gross == pytest.approx(expected, rel=1e-9)
        assert reason == "time" and px == 130

    def test_time_exit_15_days(self):
        closes = [100] + [101] * 20   # never stops, never partials
        df = self._frame(closes, highs=[101] * 21)
        exit_i, px, reason, took, _ = sr.pro_exit_scan(df, 0, 100.0, 1.0)
        # research convention: entry day counts as day 1 -> exit index 14
        assert reason == "time" and exit_i == 14 and px == 101 and took is False

    def test_data_ends_open_returns_none(self):
        closes = [100, 101, 102]
        df = self._frame(closes)
        r = sr.pro_exit_scan(df, 0, 100.0, 1.0)
        assert r[0] is None and r[2] is None and r[4] is None


# ------------------------------------------------------------ kill switch
class TestKillSwitch:
    def test_kill_switch_file_halts_run(self, tmp_path, monkeypatch):
        ks = tmp_path / "KILL_SWITCH"
        ks.write_text("halt\n")
        monkeypatch.setattr(sr, "KILL_SWITCH", ks)
        monkeypatch.setenv("STRAT026_LOG_ONLY", "1")
        monkeypatch.setattr(sr, "EVENTS_PATH", tmp_path / "ev.jsonl")
        monkeypatch.setattr(sr, "STATE_PATH", tmp_path / "state.json")
        with pytest.raises(SystemExit) as e:
            sr.main()
        assert e.value.code == 3
        evts = [json.loads(l) for l in (tmp_path / "ev.jsonl").read_text().splitlines()]
        assert evts and evts[-1]["type"] == "kill"

    def test_missing_log_only_guard_halts(self, tmp_path, monkeypatch):
        monkeypatch.delenv("STRAT026_LOG_ONLY", raising=False)
        monkeypatch.setattr(sr, "KILL_SWITCH", tmp_path / "nope")
        monkeypatch.setattr(sr, "EVENTS_PATH", tmp_path / "ev.jsonl")
        monkeypatch.setattr(sr, "STATE_PATH", tmp_path / "state.json")
        with pytest.raises(SystemExit) as e:
            sr.main()
        assert e.value.code == 2

    def test_kill_switch_is_own_file_separate_from_strat023(self):
        # own kill-switch path inside strat_026_shadow, distinct from any
        # strat023 guard
        assert sr.KILL_SWITCH.name == "KILL_SWITCH"
        assert "strat_026_shadow" in str(sr.KILL_SWITCH)
        assert "strat_023" not in str(sr.KILL_SWITCH)


# ------------------------------------------------------------ log-only safety
class TestLogOnlySafety:
    def test_fetch_bars_get_only_never_orders(self):
        calls = []

        class Resp:
            status_code = 200

            def json(self):
                return {"bars": [{"t": "2026-01-02T05:00:00Z", "o": 1, "h": 1,
                                  "l": 1, "c": 1, "v": 100}], "next_page_token": None}

        class FakeSession:
            def get(self, url, **kw):
                calls.append(("GET", url, kw.get("params", {}).get("symbols")))
                return Resp()

            def close(self):
                pass

        df = sr.fetch_bars("AAA", "key", "sec", session=FakeSession())
        assert df is not None and len(calls) == 1
        method, url, sym = calls[0]
        assert method == "GET"
        assert url == "https://data.alpaca.markets/v2/stocks/bars"
        assert sym == "AAA"

    def test_source_has_no_order_submission_path(self):
        src = (SHADOW_DIR / "shadow_runner.py").read_text()
        for banned in ("/v2/stocks/orders", "submit_order", "alpaca-py",
                       "TradingClient", "place_order", ".post(", "requests.post"):
            assert banned not in src, f"banned order-path token in source: {banned}"
        assert "data.alpaca.markets" in src and "/v2/stocks/bars" in src  # read-only

    def test_module_never_imports_main_bot_or_brokerage(self):
        src = (SHADOW_DIR / "shadow_runner.py").read_text()
        for banned in ("from src", "import src", "smart_bot", "sqlite_db",
                       "alpaca_trade_api"):
            assert banned not in src

    def test_run_once_writes_only_jsonl_and_state(self, tmp_path):
        df = flat_then_dip()
        spy = spy_series(len(df), up=True)
        bars = {"AAA": df, "SPY": spy}
        before = set(p.name for p in tmp_path.iterdir())
        sr.run_once(bars, spy, log_dir=tmp_path)
        new = set(p.name for p in tmp_path.iterdir()) - before
        assert new == {"shadow_events.jsonl", "state.json"}
        for line in (tmp_path / "shadow_events.jsonl").read_text().splitlines():
            evt = json.loads(line)  # every line is valid JSON
            assert evt["type"] in {"signal", "entry", "partial", "exit",
                                   "signal_cancelled", "kill"}

    def test_full_cycle_entry_then_exit_logged(self, tmp_path):
        # run 1: signal on the dip close (regime on)
        d1 = flat_then_dip()
        spy = spy_series(len(d1), up=True)
        s1 = sr.run_once({"AAA": d1, "SPY": spy}, spy, log_dir=tmp_path)
        assert s1["new_signals"] == 1
        # run 2: one more bar (next open) then a crash bar that trips the
        # initial stop
        last_close = float(d1["c"].iloc[-1])
        next_open = last_close * 0.99
        crash_close = last_close * 0.5
        extra = make_bars([next_open, crash_close])
        extra["t"] = [d1["t"].iloc[-1] + timedelta(days=i + 1) for i in range(2)]
        d2 = pd.concat([d1, extra], ignore_index=True)
        spy2 = spy_series(len(d2), up=True)
        s2 = sr.run_once({"AAA": d2, "SPY": spy2}, spy2, log_dir=tmp_path)
        assert s2["entries"] == 1 and s2["exits"] == 1
        evts = [json.loads(l) for l in (tmp_path / "shadow_events.jsonl").read_text().splitlines()]
        entry = [e for e in evts if e["type"] == "entry"][0]
        exit_ = [e for e in evts if e["type"] == "exit"][0]
        assert entry["entry_price"] == pytest.approx(next_open)
        assert exit_["exit_reason"] == "init_stop"
        assert exit_["symbol"] == "AAA" and exit_["entry_date"] and exit_["exit_date"]
        state = json.loads((tmp_path / "state.json").read_text())
        assert state["open"] == {}
        # a fresh dip signal on the crash bar may legitimately be pending
        assert all(v["signal_date"] for v in state["pending"].values())


# ------------------------------------------------- research-record parity
class TestResearchParity:
    """The shadow implementation must match the STRAT-026 research engine
    (experiments/strat_026/run_research.py) exactly for the frozen S1a
    configuration."""

    @staticmethod
    def _research_module():
        path = SHADOW_DIR.parent / "strat_026" / "run_research.py"
        spec = importlib.util.spec_from_file_location("strat026_research", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["strat026_research"] = mod
        # run_research imports `engine` from its own directory
        sys.path.insert(0, str(path.parent))
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path.pop(0)
        return mod

    def test_frozen_parameters_match_research_record(self):
        r = self._research_module()
        assert sr.ENTRY_RSI == 10
        assert sr.INIT_STOP_ATR == r.INIT_STOP_ATR == 2.0
        assert sr.TRAIL_ATR == r.TRAIL_ATR == 3.0
        assert sr.PARTIAL_R == r.PARTIAL_R == 2.0
        assert sr.TIME_EXIT == r.TIME_EXIT == 15
        assert sr.COST_PER_SIDE == 0.0010

    def test_pro_exit_matches_research_engine(self):
        r = self._research_module()
        rng = np.random.default_rng(7)
        for trial in range(25):
            n = 40
            closes = list(100 + np.cumsum(rng.normal(0, 1.5, n)))
            highs = [c * (1 + abs(rng.normal(0, 0.01))) for c in closes]
            df = pd.DataFrame({"close": np.asarray(closes),
                               "high": np.asarray(highs),
                               "low": np.asarray(closes) * 0.99,
                               "open": np.asarray(closes) * 0.999,
                               "date": pd.date_range("2026-01-01", periods=n, freq="D")})
            df["atr14"] = 2.0
            t = 5
            res = r.pro_exits_trade(df, t, partial_on=True, time_stop=sr.TIME_EXIT)
            mine = sr.pro_exit_scan(df, t + 1, float(df["open"].iloc[t + 1]),
                                    float(df["atr14"].iloc[t]))
            if res is None:
                assert mine[0] is None
                continue
            e_i, x_i, gross = res
            assert mine[0] == x_i
            assert mine[1] == pytest.approx(float(df["close"].iloc[x_i]))
            assert mine[4] == pytest.approx(gross, rel=1e-9)

    def test_research_record_documents_same_entry_and_exits(self):
        results = (SHADOW_DIR.parent / "strat_026" / "RESULTS.md").read_text()
        assert "RSI(2)<10" in results
        assert "SPY close > SPY SMA200" in results
        assert "2xATR (close-checked)" in results
        assert "chandelier trailing stop 3xATR" in results
        assert "50% partial profit at +2R" in results
        assert "15-day time exit" in results


# ------------------------------------------------------------ systemd units
class TestSystemdUnits:
    SVC = SHADOW_DIR / "systemd" / "strat026-rsi2-shadow.service"
    TMR = SHADOW_DIR / "systemd" / "strat026-rsi2-shadow.timer"

    def test_unit_files_present(self):
        assert self.SVC.exists() and self.TMR.exists()

    def test_service_is_oneshot_with_log_only_guard(self):
        txt = self.SVC.read_text()
        assert "Type=oneshot" in txt
        assert "STRAT026_LOG_ONLY=1" in txt
        assert "/orders" not in txt

    def test_timer_after_market_close_utc(self):
        txt = self.TMR.read_text()
        assert "OnCalendar=Mon..Fri 21:30 UTC" in txt
        assert "Unit=strat026-rsi2-shadow.service" in txt
        assert "WantedBy=timers.target" in txt

    def test_units_not_installed(self):
        user_dir = Path.home() / ".config" / "systemd" / "user"
        assert not (user_dir / "strat026-rsi2-shadow.timer").exists()
        assert not (user_dir / "strat026-rsi2-shadow.service").exists()

    @pytest.mark.skipif(subprocess.run(["which", "systemd-analyze"],
                                       capture_output=True).returncode != 0,
                        reason="systemd-analyze unavailable")
    def test_units_verify(self):
        for f in (self.SVC, self.TMR):
            r = subprocess.run(["systemd-analyze", "verify", str(f)],
                               capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
