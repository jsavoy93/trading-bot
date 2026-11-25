"""STRAT-023 tests — run in isolation from the repo suite:

    /path/to/venv/bin/python -m pytest experiments/strat_023/test_shadow_runner.py \
        -p no:cacheprovider -v

Covers:
  1. Paper-only hard guard (subprocess, no env -> refuses, nonzero; with env ->
     runs end-to-end on a synthetic fixture and writes signals/fills).
  2. Entry/exit logic parity with the PR 116 backtest conventions
     (experiments/strat_022/run_backtest.py).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "strat_022"))

import shadow_runner as sr  # noqa: E402
import run_backtest as rb  # noqa: E402


# ------------------------------------------------------------------ fixtures
def synth_uptrend_with_dip(n_warm: int = 240, dip1: float = 0.91, dip2: float = 0.97,
                           seed: int = 7) -> pd.DataFrame:
    """Rising series (close > SMA200), then a sharp 2-day dip (day 2 shallow so
    the 3xATR stop is NOT breached at the entry day) so Wilder RSI(2) < 10,
    then a 3-day bounce for the SMA5 exit. Volume passes the 1M filter."""
    rng = np.random.default_rng(seed)
    closes = [50.0]
    for _ in range(n_warm - 1):
        closes.append(closes[-1] * (1.0 + 0.002 + rng.normal(0, 0.004)))
    closes.append(closes[-1] * dip1)
    closes.append(closes[-1] * dip2)
    for _ in range(3):
        closes.append(closes[-1] * 1.03)
    closes = np.array(closes)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * 1.005
    lows = np.minimum(opens, closes) * 0.995
    vols = np.full(len(closes), 5_000_000.0)
    dates = pd.bdate_range("2025-01-01", periods=len(closes))
    return pd.DataFrame({
        "t": [d.isoformat() + "Z" for d in dates],
        "o": opens, "h": highs, "l": lows, "c": closes, "v": vols,
    })


@pytest.fixture()
def fixture_dir(tmp_path: Path) -> Path:
    """Two-stage fixture: stage 1 ends on the signal day; stage 2 extends
    through the bounce (fill + exit)."""
    full = synth_uptrend_with_dip()
    sig_idx = len(full) - 5  # second dip day = signal day (close > sma200, rsi2 < 10)
    d1 = tmp_path / "stage1"
    d2 = tmp_path / "stage2"
    d1.mkdir()
    d2.mkdir()
    full.iloc[: sig_idx + 1].to_csv(d1 / "TEST.csv", index=False)
    full.to_csv(d2 / "TEST.csv", index=False)
    return tmp_path


def db_rows(db_path: Path, table: str):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    conn.close()
    return rows


ENV_RUN = dict(os.environ, TRADING_BOT_PAPER_ONLY="1")


# --------------------------------------------------------- 1) the hard guard
def test_guard_refuses_without_env_var(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "TRADING_BOT_PAPER_ONLY"}
    r = subprocess.run(
        [sys.executable, str(HERE / "shadow_runner.py")],
        env={**env, "STRAT023_FIXTURE_DIR": str(tmp_path), "STRAT023_DB": str(tmp_path / "x.db")},
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode != 0, "runner must exit nonzero without TRADING_BOT_PAPER_ONLY=1"
    assert "TRADING_BOT_PAPER_ONLY" in r.stderr
    assert "REFUSING" in r.stderr


def test_guard_runs_end_to_end_with_env_var(fixture_dir, tmp_path):
    db = tmp_path / "shadow.db"
    # stage 1: signal day -> one pending signal, no trades
    r1 = subprocess.run(
        [sys.executable, str(HERE / "shadow_runner.py")],
        env={**ENV_RUN, "STRAT023_FIXTURE_DIR": str(fixture_dir / "stage1"),
             "STRAT023_DB": str(db)},
        capture_output=True, text=True, timeout=120,
    )
    assert r1.returncode == 0, r1.stderr
    sigs = db_rows(db, "signals")
    assert len(sigs) == 1 and sigs[0][7] == "pending"
    # stage 2: next open fill + bounce closes the trade via sma5 exit
    r2 = subprocess.run(
        [sys.executable, str(HERE / "shadow_runner.py")],
        env={**ENV_RUN, "STRAT023_FIXTURE_DIR": str(fixture_dir / "stage2"),
             "STRAT023_DB": str(db)},
        capture_output=True, text=True, timeout=120,
    )
    assert r2.returncode == 0, r2.stderr
    sigs = db_rows(db, "signals")
    trades = db_rows(db, "trades")
    assert sigs[0][7] == "filled"
    assert len(trades) == 1
    t = trades[0]
    assert t[7] == "sma5"          # exit_reason
    assert t[10] == 10.0           # cost_bps recorded per side
    # net return = gross - 2 * 10bps
    assert t[9] == pytest.approx(t[8] - 2 * 0.0010)


# ------------------------------------------- 2) parity with PR 116 backtest
def test_wilder_rsi_identical_to_backtest():
    rng = np.random.default_rng(3)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 300)))
    pd.testing.assert_series_equal(sr.wilder_rsi(close, 2), rb.wilder_rsi(close, 2))


def test_entry_signals_match_backtest_conventions():
    df = sr.add_indicators(synth_uptrend_with_dip())
    got = sr.entry_signal_rows(df).index.tolist()
    want = rb.signal_indices(
        df, 10, df["date"].min(), df["date"].max(),
    )
    assert got == want
    assert len(got) >= 1, "synthetic fixture must produce a C4 entry signal"


def test_exit_logic_matches_run_trades():
    """Compare shadow exit evaluation to run_backtest.run_trades on data where
    the trade closes before the data ends (all three exit reasons exercised)."""
    rng = np.random.default_rng(11)
    base = []
    c = 80.0
    for _ in range(260):
        c *= 1.0 + 0.0015 + rng.normal(0, 0.003)
        base.append(c)
    cases = {
        # (tail multipliers applied AFTER dip day 1 = signal day)
        "atr_stop": {"dip2": 0.91, "tail": [0.93, 0.93]},   # keeps crashing -> 3xATR stop at entry day
        "sma5": {"dip2": 0.98, "tail": [1.0, 1.06, 1.06]},  # shallow dip + bounce -> sma5
        "time": {"dip2": 0.98, "tail": [1.0] * 12},         # drifts flat -> 10-day time stop
    }
    for reason, spec in cases.items():
        closes = list(base)
        closes.append(closes[-1] * 0.91)   # dip day 1 (signal day)
        closes.append(closes[-1] * spec["dip2"])
        for m in spec["tail"]:
            closes.append(closes[-1] * m)
        closes = np.array(closes)
        opens = np.concatenate([[closes[0]], closes[:-1]])
        raw = pd.DataFrame({
            "t": [d.isoformat() + "Z" for d in pd.bdate_range("2025-01-01", periods=len(closes))],
            "o": opens, "h": np.maximum(opens, closes) * 1.005,
            "l": np.minimum(opens, closes) * 0.995, "c": closes,
            "v": np.full(len(closes), 5e6),
        })
        df = sr.add_indicators(raw)
        sigs = sr.entry_signal_rows(df).index.tolist()
        t = len(base)  # the engineered ~9% dip day (index is deterministic)
        assert t in sigs, f"{reason}: dip day must be a signal day (sigs={sigs[-3:]})"
        bt = rb.run_trades(df, "X", [t], sr.ATR_MULT, sr.TIME_STOP)
        assert bt, f"{reason}: backtest must produce a trade"
        bt = bt[0]
        e, entry_px = bt["entry_i"], bt["entry_px"]
        stop = entry_px - sr.ATR_MULT * df["atr14"].iloc[t]
        ei, ep, why = sr.evaluate_exit(df, e, entry_px, stop)
        assert ei == bt["exit_i"]
        assert ep == pytest.approx(bt["exit_px"])
        assert why == bt["reason"]
        assert why == reason


def test_no_threshold_relaxation():
    """C4 parameters are frozen exactly as pre-registered."""
    assert sr.ENTRY_RSI == 10
    assert sr.ATR_MULT == 3.0
    assert sr.TIME_STOP == 10
    assert sr.COST_PER_SIDE == 0.0010
