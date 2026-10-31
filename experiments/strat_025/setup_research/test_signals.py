"""STRAT-025 Part 2 — parity + smoke tests for the standalone signal functions.

Run:  pytest experiments/strat_025/setup_research/test_signals.py -p no:cacheprovider -q
(uses only synthetic data; no network, no repo state changes)
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import setups as S          # research implementation used for the backtests
import signals as SG        # standalone shipped functions


def _synthetic(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1.5, n))
    close = np.maximum(close, 5.5)
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    vol = rng.integers(2_000_000, 8_000_000, n)
    df = pd.DataFrame({
        "date": pd.bdate_range("2021-01-04", periods=n),
        "open": open_, "high": high, "low": low, "close": close, "volume": vol,
    })
    return SG.prepare_indicators(df)


def test_prepare_indicators_adds_columns():
    df = _synthetic()
    for col in ("sma50", "sma200", "ema20", "rsi2", "atr14", "vol20", "range"):
        assert col in df.columns
    assert df["sma200"].notna().sum() > 100


@pytest.mark.parametrize("name", sorted(SG.SIGNAL_FUNCTIONS.keys()))
def test_parity_with_research_implementations(name):
    df = _synthetic()
    a = SG.SIGNAL_FUNCTIONS[name](df)
    b = getattr(S, name)(df)
    assert list(a.index) == list(b.index)
    assert a.fillna(False).equals(b.fillna(False)), f"parity mismatch for {name}"


def test_signal_output_is_bool_series():
    df = _synthetic()
    for name, fn in SG.SIGNAL_FUNCTIONS.items():
        s = fn(df)
        assert isinstance(s, pd.Series)
        assert s.dtype == bool
        assert len(s) == len(df)


def test_passing_registry_is_empty_and_documented():
    assert SG.PASSING_SETUPS == []
    assert "PASS" in (SG.__doc__ or "")


def test_no_lookahead_equivalence_truncation():
    """A signal computed on a truncated series must equal the same prefix of the
    full-series signal for non-cross-sectional setups (no future leakage)."""
    full = _synthetic()
    for name in ("ema20_pullback", "gap_and_go", "gap_fill_fade", "inside_day", "nr7",
                 "rsi2_uptrend_v2", "high20_breakout_vol"):
        a = SG.SIGNAL_FUNCTIONS[name](full)
        b = SG.SIGNAL_FUNCTIONS[name](full.iloc[:250].copy())
        assert a.iloc[:250].fillna(False).equals(b.fillna(False)), f"lookahead in {name}"
