"""STRAT-025 setup research — the nine pre-registered setup signal functions.

Each function takes a daily-bars DataFrame (columns: open/high/low/close/volume plus
engine-computed sma50/sma200/ema20/rsi2/atr14/vol20/range and `date`, integer index)
and returns a boolean Series aligned to the index: True on SIGNAL day t (entry t+1 open).
Exact rules per PREREGISTRATION.md §4. No function looks at data after day t.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _warm(df) -> pd.Series:
    return df["sma200"].notna() & df["atr14"].notna() & df["vol20"].notna()


# (a) Pullback to 20-EMA in an uptrend
def ema20_pullback(df: pd.DataFrame) -> pd.Series:
    uptrend = (df["close"] > df["sma200"]) & (df["sma50"] > df["sma200"])
    touch = (df["low"] <= df["ema20"]) & (df["close"] >= 0.995 * df["ema20"])
    return _warm(df) & uptrend & touch


# (b) 20-day-high close breakout on 1.5x average volume
def high20_breakout_vol(df: pd.DataFrame) -> pd.Series:
    prior20_high = df["close"].rolling(20).max().shift(1)
    prior20_vol = df["volume"].rolling(20).mean().shift(1)
    brk = df["close"] > prior20_high
    volx = df["volume"] > 1.5 * prior20_vol
    return _warm(df) & brk & volx


# (c) Minervini VCP (exact definition in PREREG §4c)
def vcp_minervini(df: pd.DataFrame) -> pd.Series:
    n = len(df)
    out = pd.Series(False, index=df.index)
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    sma200 = df["sma200"].values
    for t in range(65, n):  # need 63d lookback + window
        if not np.isfinite(sma200[t]) or c[t] <= sma200[t]:
            continue
        h63 = c[t - 63:t].max()
        if c[t] < 0.85 * h63:
            continue
        # three consecutive 5-day windows in t-15..t-1
        w1 = (t - 15, t - 10); w2 = (t - 10, t - 5); w3 = (t - 5, t)
        r1 = h[w1[0]:w1[1]].max() - l[w1[0]:w1[1]].min()
        r2 = h[w2[0]:w2[1]].max() - l[w2[0]:w2[1]].min()
        r3 = h[w3[0]:w3[1]].max() - l[w3[0]:w3[1]].min()
        if not (r1 > 0 and r2 <= 0.70 * r1 and r3 <= 0.70 * r2):
            continue
        pivot_high = h[t - 15:t].max()
        if c[t] > pivot_high:
            out.iloc[t] = True
    return out & _warm(df)


# (d1) Gap-and-go: open gaps >+2% vs prior close, close > open
def gap_and_go(df: pd.DataFrame) -> pd.Series:
    gap = df["open"] / df["close"].shift(1) - 1.0
    return _warm(df) & (gap > 0.02) & (df["close"] > df["open"])


# (d2) Gap-fill fade: open gaps <-2%, close > open (reclaim)
def gap_fill_fade(df: pd.DataFrame) -> pd.Series:
    gap = df["open"] / df["close"].shift(1) - 1.0
    return _warm(df) & (gap < -0.02) & (df["close"] > df["open"])


# (e) RS leaders vs SPY — requires cross-sectional ranks; handled in run_setup.
def rs_line(df: pd.DataFrame, spy_close_by_date: pd.Series, rsw: int) -> pd.Series:
    """RS line = sym return over rsw days divided by SPY return over rsw days.
    spy_close_by_date: SPY close indexed by trading date; aligned onto df via date."""
    spy = df["date"].map(spy_close_by_date)
    sym_ret = df["close"] / df["close"].shift(rsw)
    spy_ret = spy / spy.shift(rsw)
    return sym_ret / spy_ret


# (f1) Inside day
def inside_day(df: pd.DataFrame) -> pd.Series:
    cond = (df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))
    return _warm(df) & cond & (df["close"] > df["sma200"])


# (f2) NR7: narrowest daily range of the last 7 days
def nr7(df: pd.DataFrame) -> pd.Series:
    r = df["range"]
    cond = pd.Series(True, index=df.index)
    for k in range(1, 7):
        cond &= r < r.shift(k)
    return _warm(df) & cond & (df["close"] > df["sma200"])


# (g) RSI(2) uptrend C4 entry (distinct exit lives in the engine: NO sma5 exit)
def rsi2_uptrend_v2(df: pd.DataFrame) -> pd.Series:
    return (_warm(df) & df["rsi2"].notna()
            & (df["close"] > df["sma200"]) & (df["rsi2"] < 10))
