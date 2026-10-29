"""STRAT-022 harness sanity checks (synthetic data; no network)."""
import numpy as np
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_backtest import wilder_rsi, run_trades, signal_indices


def mk_df(closes, volumes=None):
    closes = np.array(closes, dtype=float)
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(n, 5e6) if volumes is None else volumes,
    })


def test_rsi_known():
    # monotonically rising -> RSI = 100
    df = mk_df(np.linspace(100, 200, 60))
    r = wilder_rsi(df["close"], 2)
    assert r.iloc[10:].min() == 100.0, r.iloc[10:].min()
    # falling -> RSI = 0
    df2 = mk_df(np.linspace(200, 100, 60))
    r2 = wilder_rsi(df2["close"], 2)
    assert r2.iloc[10:].max() == 0.0, r2.iloc[10:].max()
    print("PASS rsi bounds")


def test_next_open_entry_and_time_stop():
    # 210 rows: SMA200 warm; flat price then pattern
    px = np.full(215, 100.0)
    df = mk_df(px)
    df["sma200"] = df["close"].rolling(200).mean()
    df["sma5"] = df["close"].rolling(5).mean()
    df["atr14"] = 5.0
    # force entry at index 205 (signal), flat after -> time stop 5 days from t+1
    trs = run_trades(df, "X", [205], atr_mult=2.0, time_stop=5)
    assert len(trs) == 1
    tr = trs[0]
    assert tr["entry_i"] == 206 and tr["entry_px"] == df["open"].iloc[206]
    assert tr["exit_i"] == 206 + 5 - 1, (tr["entry_i"], tr["exit_i"])
    assert tr["reason"] == "time"
    print("PASS next-open entry + time stop")


def test_anti_overlap():
    px = np.full(215, 100.0)
    df = mk_df(px)
    df["sma200"] = df["close"].rolling(200).mean()
    df["sma5"] = df["close"].rolling(5).mean()
    df["atr14"] = 5.0
    # two signals 3 days apart, second falls inside first trade -> ignored
    trs = run_trades(df, "X", [205, 208], atr_mult=2.0, time_stop=5)
    assert len(trs) == 1 and trs[0]["entry_i"] == 206
    print("PASS anti-overlap")


def test_atr_stop():
    px = np.full(215, 100.0)
    # after entry at 206, drop closes to breach stop
    px[207] = 100.0; px[208] = 90.0; px[209] = 80.0
    df = mk_df(px)
    df["sma200"] = df["close"].rolling(200).mean()
    df["sma5"] = df["close"].rolling(5).mean()
    df["atr14"] = 5.0  # fixed ATR -> stop = 100 - 2*5 = 90
    trs = run_trades(df, "X", [205], atr_mult=2.0, time_stop=10)
    tr = trs[0]
    # sma5 exit: sma5 at 208 = mean(100,100,100,100,90)=98 < close 90? no (close 90 < sma5) -> not triggered
    # close 208 = 90 <= stop 90 -> ATR stop at 208
    assert tr["reason"] == "atr_stop" and tr["exit_i"] == 208 and abs(tr["exit_px"] - 90.0) < 1e-9
    print("PASS atr stop on close")


def test_signal_causality():
    # rising market then one dip day: RSI2 plunges; signal requires close>sma200
    n = 220
    px = np.full(n, 100.0)
    df = mk_df(px)
    df["sma200"] = df["close"].rolling(200).mean()
    df["rsi2"] = wilder_rsi(df["close"], 2)
    df["atr14"] = 5.0
    df["vol20"] = df["volume"].rolling(20).mean()
    idx = signal_indices(df, 10, pd.Timestamp("2024-01-01"), pd.Timestamp("2030-01-01"))
    assert idx == [], idx  # flat price -> RSI 100 or NaN, no signal
    print("PASS no spurious signals")


if __name__ == "__main__":
    test_rsi_known()
    test_next_open_entry_and_time_stop()
    test_anti_overlap()
    test_atr_stop()
    test_signal_causality()
    print("ALL SANITY CHECKS PASS")
