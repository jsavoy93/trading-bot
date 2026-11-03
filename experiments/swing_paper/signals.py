"""STRAT-SWING-PAPER — signal computation (frozen research parameters).

Re-implements the exact entry predicates from the frozen research records:
  C4 (STRAT-022, PREREGISTRATION.md): RSI(2) < 10 AND close > SMA200 AND
      vol20 >= 1M AND close >= 5. Entry next open. Exit: close > SMA5
      (days after entry), stop entry_open - 3.0*ATR14(signal day),
      time exit 10 trading days.
  RSI(2) (STRAT-026 S1a rsi2_regime): RSI(2) < 10 AND vol20 >= 1M AND
      close >= 5 AND SPY close > SPY SMA200 (regime). Initial stop
      entry - 2.0*ATR14(t); chandelier 3.0*ATR14(t); 50% partial at +2R;
      time exit 15 trading days.

NO threshold in this module may be relaxed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# --- frozen C4 parameters (STRAT-022 grid row C4; DO NOT RELAX) -----------
C4_ENTRY_RSI = 10
C4_ATR_STOP_MULT = 3.0
C4_TIME_STOP = 10
C4_MIN_VOL20 = 1_000_000
C4_MIN_CLOSE = 5.0

# --- frozen RSI(2) parameters (STRAT-026 S1a; DO NOT RELAX) ----------------
RSI2_ENTRY_RSI = 10
RSI2_INIT_STOP_ATR = 2.0
RSI2_TRAIL_ATR = 3.0
RSI2_PARTIAL_R = 2.0
RSI2_TIME_EXIT = 15
RSI2_MIN_VOL20 = 1_000_000
RSI2_MIN_CLOSE = 5.0

WARMUP_BARS = 220  # SMA200 + slack, same convention as the research code


def wilder_rsi(close: pd.Series, period: int = 2) -> pd.Series:
    """Wilder RSI, bit-identical to the STRAT-026 research engine
    (no zero-loss substitution -> NaN there instead of 100)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    for i in range(period * 2, len(close)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100 - 100 / (1 + rs)


def add_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    """bars columns t,o,h,l,c,v. Adds date/sma200/sma5/rsi2/atr14/vol20
    (same formulas as the frozen research engines)."""
    df = bars.rename(columns={"o": "open", "h": "high", "l": "low",
                              "c": "close", "v": "volume"}).copy()
    df["date"] = pd.to_datetime(pd.to_datetime(df["t"], utc=True).dt.date)
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    c = df["close"]
    prev_close = c.shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["sma200"] = c.rolling(200).mean()
    df["sma5"] = c.rolling(5).mean()
    df["rsi2"] = wilder_rsi(c, 2)
    df["atr14"] = tr.rolling(14).mean()
    df["vol20"] = df["volume"].rolling(20).mean()
    return df


def c4_entry_signal(df: pd.DataFrame) -> bool:
    """C4 entry predicate on the latest completed daily close (frozen)."""
    last = len(df) - 1
    if last < WARMUP_BARS - 1:
        return False
    row_ok = (
        df["sma200"].notna() & df["rsi2"].notna() & df["atr14"].notna()
        & df["vol20"].notna()
        & (df["close"] > df["sma200"])
        & (df["rsi2"] < C4_ENTRY_RSI)
        & (df["vol20"] >= C4_MIN_VOL20)
        & (df["close"] >= C4_MIN_CLOSE)
    )
    return bool(row_ok.iloc[last])


def rsi2_entry_signal(df: pd.DataFrame, spy_bars: pd.DataFrame) -> bool:
    """RSI(2) rsi2_regime entry predicate on the latest completed close,
    with the SPY > SMA200 regime filter (no lookahead: latest SPY bar at
    or before the symbol's latest date)."""
    last = len(df) - 1
    if last < WARMUP_BARS - 1:
        return False
    row_ok = (
        df["sma200"].notna() & df["rsi2"].notna() & df["atr14"].notna()
        & df["vol20"].notna()
        & (df["rsi2"] < RSI2_ENTRY_RSI)
        & (df["vol20"] >= RSI2_MIN_VOL20)
        & (df["close"] >= RSI2_MIN_CLOSE)
    )
    if not bool(row_ok.iloc[last]):
        return False
    spy = add_indicators(spy_bars)
    spy_ok = (spy["sma200"].notna() & (spy["close"] > spy["sma200"]))
    spy_dates = list(spy["date"])
    idx = int(np.searchsorted(spy_dates, df["date"].iloc[last], side="right")) - 1
    if idx < 0:
        return False
    return bool(spy_ok.iloc[idx])


def c4_stop_price(entry_px: float, atr14_signal_day: float) -> float:
    """C4 initial stop: entry_open - 3.0 * ATR14(signal day) (frozen)."""
    return float(entry_px) - C4_ATR_STOP_MULT * float(atr14_signal_day)


def rsi2_stops(entry_px: float, atr14_signal_day: float) -> tuple[float, float, float]:
    """RSI(2) stops (frozen): returns
    (initial_stop, chandelier_first_stop_anchor, partial_price)
      initial_stop  = entry - 2.0*ATR14(t)
      chandelier    = max(close since entry) - 3.0*ATR14(t); at entry day the
                      anchor IS the entry-day close, so the first-day
                      trailing anchor is entry_px itself
      partial_price = entry + 2R where R = 2.0*ATR14(t)
    """
    risk = RSI2_INIT_STOP_ATR * float(atr14_signal_day)
    return (float(entry_px) - risk, float(entry_px) - RSI2_TRAIL_ATR * float(atr14_signal_day),
            float(entry_px) + RSI2_PARTIAL_R * risk)
