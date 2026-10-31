"""Setup-signal registry: setup name -> daily-bar predicate.

A predicate takes a pandas DataFrame with computed indicator columns
(convention below) for ONE symbol and returns True when the setup fires on the
latest completed daily bar.

Flags DEFAULT OFF: a setup only trades if STRAT025_SETUP_<NAME>=1 is set AND
the loop flags (STRAT025_ENABLE_INTRADAY_SCAN / _ENABLE_EOD_ENTRIES) are on.
STRAT-025 Part 2 (another agent) fills in real predicates + verdicts in
experiments/strat_025/setup_research/ and registers them here via
register_setup(). Placeholders below always return False (never fire).

DataFrame column convention (daily bars, computed before the predicate runs):
  open, high, low, close, volume, date  (+ any indicators the setup needs,
  e.g. sma200, rsi2, atr14, vol20 as in strat_023.shadow_runner.add_indicators).
"""
from __future__ import annotations

import os
from typing import Callable

import pandas as pd

SetupPredicate = Callable[[pd.DataFrame], bool]

_REGISTRY: dict[str, SetupPredicate] = {}


def register_setup(name: str, predicate: SetupPredicate) -> None:
    """Register (or replace) a setup predicate by name."""
    if not name.isidentifier():
        raise ValueError(f"setup name must be an identifier: {name!r}")
    _REGISTRY[name] = predicate


def registry() -> dict[str, SetupPredicate]:
    return dict(_REGISTRY)


def is_setup_enabled(name: str) -> bool:
    """Setup flags DEFAULT OFF: only STRAT025_SETUP_<NAME> in (1,true,yes,on)."""
    v = os.environ.get(f"STRAT025_SETUP_{name.upper()}")
    return v is not None and v.strip().lower() in ("1", "true", "yes", "on")


def enabled_setups() -> list[str]:
    return sorted(n for n in _REGISTRY if is_setup_enabled(n))


def evaluate(df: pd.DataFrame, name: str) -> bool:
    pred = _REGISTRY.get(name)
    if pred is None:
        return False
    try:
        return bool(pred(df))
    except Exception:
        return False


def evaluate_all_enabled(df: pd.DataFrame) -> list[str]:
    return [n for n in enabled_setups() if evaluate(df, n)]


# ---------------------------- placeholders (Part 2 replaces these) ----------
def _placeholder_never(name: str) -> SetupPredicate:
    def _pred(df: pd.DataFrame) -> bool:  # pragma: no cover - trivially False
        return False
    _pred.__name__ = f"placeholder_{name}"
    return _pred


for _name in ("pullback_trend", "breakout_range", "gap_reclaim"):
    register_setup(_name, _placeholder_never(_name))
