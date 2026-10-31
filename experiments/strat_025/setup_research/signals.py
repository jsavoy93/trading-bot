"""STRAT-025 Part 2 — standalone signal functions for the pre-registered swing setups.

STATUS (2026-10-31): NO setup passed the frozen development bar
(experiments/strat_025/setup_research/PREREGISTRATION.md §10), therefore NO setup was
run on the holdout and the PASS list is EMPTY. This module is shipped so that the
signal logic is reusable/testable, and so that a future, separately pre-registered
sleeve can reuse it. `PASSING_SETUPS` is the authoritative registry of setups that
cleared BOTH dev and holdout; it is intentionally empty.

Each function takes a daily-bars DataFrame and returns a boolean Series (True = signal
day t, entry at t+1 open). Required input columns:
    date, open, high, low, close, volume
plus (computed or provided) sma50, sma200, ema20, rsi2, atr14, vol20.
The helper `prepare_indicators()` computes any missing indicator columns from OHLCV.

Nothing here touches production code, config, DBs or brokers. PAPER ONLY.
"""
from __future__ import annotations
from typing import Callable
import numpy as np
import pandas as pd

# Authoritative registry of setups that passed dev AND holdout under the frozen
# pre-registration. EMPTY as of 2026-10-31 (see RESULTS.md).
PASSING_SETUPS: list[str] = []


# ---------------------------------------------------------------- indicators
def wilder_rsi(close: pd.Series, period: int = 2) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    for i in range(period * 2, len(close)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.where(avg_loss != 0, 100.0)


def prepare_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with sma50/sma200/ema20/rsi2/atr14/vol20/range columns."""
    df = df.copy()
    c = df["close"]
    if "sma50" not in df:
        df["sma50"] = c.rolling(50).mean()
    if "sma200" not in df:
        df["sma200"] = c.rolling(200).mean()
    if "ema20" not in df:
        df["ema20"] = c.ewm(span=20, adjust=False).mean()
    if "rsi2" not in df:
        df["rsi2"] = wilder_rsi(c, 2)
    if "atr14" not in df:
        pc = c.shift(1)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                        (df["low"] - pc).abs()], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()
    if "vol20" not in df:
        df["vol20"] = df["volume"].rolling(20).mean()
    if "range" not in df:
        df["range"] = df["high"] - df["low"]
    return df


def _warm(df: pd.DataFrame) -> pd.Series:
    return df["sma200"].notna() & df["atr14"].notna() & df["vol20"].notna()


def eligible(df: pd.DataFrame) -> pd.Series:
    """Pre-registered point-in-time eligibility: vol20 >= 1,000,000 and close >= $5."""
    return (df["vol20"].notna() & (df["vol20"] >= 1_000_000)
            & df["close"].notna() & (df["close"] >= 5.0))


# ------------------------------------------------------------------- setups
def ema20_pullback(df: pd.DataFrame) -> pd.Series:
    """(a) Pullback to 20-EMA in an uptrend."""
    up = (df["close"] > df["sma200"]) & (df["sma50"] > df["sma200"])
    touch = (df["low"] <= df["ema20"]) & (df["close"] >= 0.995 * df["ema20"])
    return _warm(df) & up & touch


def high20_breakout_vol(df: pd.DataFrame) -> pd.Series:
    """(b) 20-day-high close breakout with volume > 1.5x prior-20d average volume."""
    prior_high = df["close"].rolling(20).max().shift(1)
    prior_vol = df["volume"].rolling(20).mean().shift(1)
    return _warm(df) & (df["close"] > prior_high) & (df["volume"] > 1.5 * prior_vol)


def vcp_minervini(df: pd.DataFrame) -> pd.Series:
    """(c) Minervini VCP: 2 successive daily-range contractions + pivot breakout."""
    n = len(df)
    out = pd.Series(False, index=df.index)
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    sma200 = df["sma200"].values
    for t in range(65, n):
        if not np.isfinite(sma200[t]) or c[t] <= sma200[t]:
            continue
        if c[t] < 0.85 * c[t - 63:t].max():
            continue
        r1 = h[t - 15:t - 10].max() - l[t - 15:t - 10].min()
        r2 = h[t - 10:t - 5].max() - l[t - 10:t - 5].min()
        r3 = h[t - 5:t].max() - l[t - 5:t].min()
        if not (r1 > 0 and r2 <= 0.70 * r1 and r3 <= 0.70 * r2):
            continue
        if c[t] > h[t - 15:t].max():
            out.iloc[t] = True
    return out & _warm(df)


def gap_and_go(df: pd.DataFrame) -> pd.Series:
    """(d1) Gap-and-go: open gaps up >2% vs prior close and the close holds above the open."""
    gap = df["open"] / df["close"].shift(1) - 1.0
    return _warm(df) & (gap > 0.02) & (df["close"] > df["open"])


def gap_fill_fade(df: pd.DataFrame) -> pd.Series:
    """(d2) Gap-fill fade: open gaps down >2% vs prior close and the close reclaims the open."""
    gap = df["open"] / df["close"].shift(1) - 1.0
    return _warm(df) & (gap < -0.02) & (df["close"] > df["open"])


def rs_line(df: pd.DataFrame, spy_close_by_date: pd.Series, rsw: int = 63) -> pd.Series:
    """(e) helper: relative-strength line of df versus SPY over rsw trading days."""
    spy = df["date"].map(spy_close_by_date)
    return (df["close"] / df["close"].shift(rsw)) / (spy / spy.shift(rsw))


def inside_day(df: pd.DataFrame) -> pd.Series:
    """(f1) Inside day (high and low inside the prior bar) with close > SMA200."""
    cond = (df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))
    return _warm(df) & cond & (df["close"] > df["sma200"])


def nr7(df: pd.DataFrame) -> pd.Series:
    """(f2) NR7: today's range is the narrowest of the last 7 bars, close > SMA200."""
    r = df["range"]
    cond = pd.Series(True, index=df.index)
    for k in range(1, 7):
        cond &= r < r.shift(k)
    return _warm(df) & cond & (df["close"] > df["sma200"])


def rsi2_uptrend_v2(df: pd.DataFrame) -> pd.Series:
    """(g) RSI(2) < 10 in an uptrend (close > SMA200). C4 ENTRY with a distinct exit
    (ATR stop + time stop only; NO SMA5 profit exit) — see RESULTS.md §'RSI(2) variant'."""
    return _warm(df) & df["rsi2"].notna() & (df["close"] > df["sma200"]) & (df["rsi2"] < 10)


SIGNAL_FUNCTIONS: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "ema20_pullback": ema20_pullback,
    "high20_breakout_vol": high20_breakout_vol,
    "vcp_minervini": vcp_minervini,
    "gap_and_go": gap_and_go,
    "gap_fill_fade": gap_fill_fade,
    "inside_day": inside_day,
    "nr7": nr7,
    "rsi2_uptrend_v2": rsi2_uptrend_v2,
}
