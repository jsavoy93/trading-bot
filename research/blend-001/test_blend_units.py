"""BLEND-001 unit tests — pure functions of run_blend.py on synthetic data.

Offline, no network, no real data files: pytest collects this file (name starts
with test_) but it lives under research/, which pytest.ini's testpaths (tests/)
does not include, so the default suite is untouched. Run it directly:

    python3 -m pytest research/blend-001/test_blend_units.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "blend-001"))
sys.path.insert(0, str(ROOT / "research" / "volsize-001"))
import run_blend as rb  # noqa: E402


def _frame(dates, closes, opens=None, volume=2_000_000.0):
    closes = np.asarray(closes, float)
    opens = closes.copy() if opens is None else np.asarray(opens, float)
    return pd.DataFrame({
        "date": pd.DatetimeIndex(dates),
        "open": opens,
        "high": np.maximum(opens, closes) * 1.01,
        "low": np.minimum(opens, closes) * 0.99,
        "close": closes,
        "volume": np.full(len(closes), volume),
    })


def test_wilder_rsi_matches_strat022_definition():
    # strat_022: avg gain/loss seeded with 2-period simple means from index 2,
    # Wilder recursion from index 4; all-gain series -> RSI 100.
    close = pd.Series(np.linspace(10, 20, 30))
    df = rb.add_features(_frame(pd.bdate_range("2021-01-01", periods=30), close))
    assert df["rsi2"].iloc[5:].dropna().eq(100.0).all()
    down = pd.Series(np.linspace(20, 10, 30))
    df2 = rb.add_features(_frame(pd.bdate_range("2021-01-01", periods=30), down))
    assert (df2["rsi2"].iloc[5:].dropna() < 1.0).all()


def test_features_are_backward_only():
    # changing a later bar must not change any earlier feature value
    dates = pd.bdate_range("2021-01-01", periods=60)
    a = _frame(dates, np.linspace(10, 15, 60))
    b = a.copy()
    b.loc[40:, "close"] *= 3.0
    fa, fb = rb.add_features(a.copy()), rb.add_features(b.copy())
    for col in ("rsi2", "atr14", "vol20", "sma5"):
        pd.testing.assert_series_equal(fa[col].iloc[:40], fb[col].iloc[:40], check_names=False)


def test_run_trades_exits_after_entry_and_respects_time_stop():
    n = 120
    dates = pd.bdate_range("2021-01-01", periods=n)
    closes = np.full(n, 100.0)
    closes[50:] = np.linspace(100, 90, n - 50)   # no close > SMA5 -> time stop
    df = rb.add_features(_frame(dates, closes))
    tr = rb.valid_trades(rb.run_trades(df, "T", np.array([20])))
    assert len(tr) == 1
    t = tr[0]
    assert t["entry_i"] == 21 and t["exit_i"] == 21 + rb.C4_TIME_STOP - 1
    assert t["exit_date"] > t["entry_date"]


def test_valid_trades_drops_zero_holding_period():
    df = rb.add_features(_frame(pd.bdate_range("2021-01-01", periods=50), np.full(50, 10.0)))
    same = [{"entry_i": 5, "exit_i": 5}]
    assert rb.valid_trades(same) == []


def test_monthly_series_matches_documented_aggregation():
    """Pins erratum E1: cash, and the TRAILING expanding-p95 denominator."""
    gi = pd.bdate_range("2021-01-04", "2021-03-31")
    keys = ["2021-01", "2021-02", "2021-03"]
    # one trade, entry session 0, exit session 2, +1% then -2%
    tr = [{"entry_date": gi[0], "exit_date": gi[2],
           "daily_ret": [0.01, -0.02]}]
    out = rb.monthly_swing_series(tr, keys, gi)
    d = rb.trailing_denominator(rb._open_counts(tr, gi))
    assert d[0] == 1.0 and d[1] == 1.0 and d[2] == 1.0    # trailing, 1 position
    assert out[0] == pytest.approx(0.01 - 0.02 - 2 * rb.C4_COST_PER_SIDE)
    assert out[1] == 0.0 and out[2] == 0.0                 # cash, not renormalised


def test_monthly_series_costs_split_by_session():
    gi = pd.bdate_range("2021-01-04", "2021-02-26")
    keys = ["2021-01", "2021-02"]
    # spans the month boundary: entry Jan session, exit Feb session
    jan_last = [i for i, d in enumerate(gi) if d < pd.Timestamp("2021-02-01")][-1]
    tr = [{"entry_date": gi[jan_last - 1], "exit_date": gi[jan_last + 1],
           "daily_ret": [0.001, 0.002]}]
    out = rb.monthly_swing_series(tr, keys, gi)
    assert out[0] == pytest.approx(0.001 - rb.C4_COST_PER_SIDE)
    assert out[1] == pytest.approx(0.002 - rb.C4_COST_PER_SIDE)


def test_cap_divisor_is_trailing_only():
    """The denominator on session i must not depend on sessions after i."""
    gi = pd.bdate_range("2021-01-04", "2021-01-22")
    n = len(gi)
    cnt = np.ones(n)
    cnt[-3:] = 9.0                      # a busy patch at the very end
    d = rb.trailing_denominator(cnt)
    # a longer busy patch lets the expanding p95 settle at 9 after the patch ends
    patch = np.ones(40); patch[10:13] = 9.0
    d3 = rb.trailing_denominator(patch)
    d4 = rb.trailing_denominator(np.ones(40))
    assert np.array_equal(d3[:10], d4[:10]) and d3[-1] == 9.0
    assert d[0] == 1.0 and d[11] == 1.0         # patch at 12..14 not yet known
    # exact series under the documented method: two 2-session trades
    n = len(gi)
    tr = [{"entry_date": gi[0], "exit_date": gi[2], "daily_ret": [0.01, 0.02]},
          {"entry_date": gi[1], "exit_date": gi[3], "daily_ret": [0.03, 0.04]}]
    d = rb.trailing_denominator(rb._open_counts(tr, gi))   # [1,1,1,1,...]
    out = rb.monthly_swing_series(tr, ["2021-01"], gi)
    open_rets = np.zeros(n)
    fees = np.zeros(n)
    open_rets[1] += 0.01; open_rets[2] += 0.02 + 0.03; open_rets[3] += 0.04
    for s in (0, 1, 2, 3):
        fees[s] += rb.C4_COST_PER_SIDE
    expect = np.sum(open_rets / d) - np.sum(fees / d)
    assert out[0] == pytest.approx(expect)
    # ...and it differs from the whole-window p95 scaling (look-ahead)
    ref = rb.monthly_swing_series(
        tr, ["2021-01"], gi, denom=np.full(n, rb.cap_divisor(tr, gi)))
    assert out[0] != pytest.approx(ref[0])


def test_cap_divisor_matches_whole_window_p95_reference():
    gi = pd.bdate_range("2021-01-04", "2021-01-15")
    tr = [{"entry_date": gi[0], "exit_date": gi[-1]},
          {"entry_date": gi[1], "exit_date": gi[2]}]
    counts = rb._open_counts(tr, gi)
    assert rb.cap_divisor(tr, gi) == pytest.approx(np.percentile(counts[counts > 0], 95))


def test_bar_report_bars_are_all_required():
    mom = np.array([0.01, -0.02, 0.03, 0.0])
    net = mom.copy()
    sigma = mom.std(ddof=1)
    rand = np.array([[0.0] * 4, [-0.01] * 4])
    r = rb.bar_report("X", net, mom, sigma, rand)
    assert r["a_sample"] is False and r["PASS"] is False       # n < 24
    r2 = rb.bar_report("Y", np.full(24, 0.01), np.zeros(24), 0.01, rand)
    assert r2["d_paired"] is False or r2["paired_t"] >= rb.T_BAR


def test_load_swing_bars_drops_holdout_rows(tmp_path):
    dates = pd.bdate_range("2020-06-01", "2024-12-31")
    df = _frame(dates, np.linspace(10, 20, len(dates)))
    out = df.rename(columns={"date": "t", "open": "o", "high": "h", "low": "l",
                             "close": "c", "volume": "v"})
    out["t"] = out["t"].dt.strftime("%Y-%m-%dT05:00:00Z")
    # poison: a 2025 row that must never survive the load
    poison = out.iloc[[-1]].copy()
    poison["t"] = "2025-03-03T05:00:00Z"
    pd.concat([out, poison]).to_csv(tmp_path / "AAA.csv", index=False)
    bars = rb.load_swing_bars(tmp_path)
    assert set(bars) == {"AAA"}
    assert bars["AAA"]["date"].max() <= pd.Timestamp("2024-12-31")
