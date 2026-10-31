"""STRAT-025 setup research — build the union universe (PREREGISTRATION §3).

C4 universe = STRAT-006R curated liquid names + STRAT-023 recheck delisted additions.
Plus main bot watchlist (quality_symbols from src/core/smart_bot.py).
Returns (listed_symbols, delisted_symbols) where delisted come from the recheck CSV
(their bars are extracted from the DATA-001 parquet shards, not Alpaca).
"""
from __future__ import annotations
from pathlib import Path
import csv

HERE = Path(__file__).resolve().parent
MAIN_WS = Path("/root/.openclaw/workspace/trading-bot")  # read-only: strat_006R universe lives there

import importlib.util
spec = importlib.util.spec_from_file_location(
    "cand", MAIN_WS / "experiments/strat_006r_candidate_universe.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
C4_CORE = sorted({s for s in mod.POPULAR_LIQUID_US if s})

# main watchlist: quality_symbols list in src/core/smart_bot.py (verified 2026-10-31)
WATCHLIST = [
    'AAPL', 'MSFT', 'GOOGL', 'GOOG', 'AMZN', 'NVDA', 'TSLA', 'META', 'BRK.A', 'BRK.B',
    'NFLX', 'ADBE', 'CRM', 'ORCL', 'CSCO', 'INTC', 'AMD', 'QCOM', 'AVGO', 'TXN',
    'JPM', 'BAC', 'WFC', 'GS', 'MS', 'C', 'AXP', 'V', 'MA', 'PYPL',
    'JNJ', 'PFE', 'MRK', 'ABT', 'TMO', 'UNH', 'CVS', 'ABBV', 'BMY', 'LLY',
    'PG', 'KO', 'PEP', 'WMT', 'HD', 'MCD', 'SBUX', 'NIKE', 'DIS', 'COST',
    'BA', 'CAT', 'GE', 'MMM', 'HON', 'UPS', 'RTX', 'LMT', 'NOC', 'GD',
    'XOM', 'CVX', 'COP', 'EOG', 'SLB', 'MPC', 'VLO', 'PSX', 'OXY', 'WMB',
    'AMT', 'CCI', 'EQIX', 'PLD', 'PSA', 'WELL', 'VTR', 'ARE', 'DLR', 'IRM',
    'SPY', 'QQQ', 'IWM', 'VTI', 'VOO', 'VEA', 'VWO', 'BND', 'AGG', 'GLD',
]
WATCHLIST = [w for w in WATCHLIST if w != 'NIKE'] + ['NKE']  # typo guard, keep NKE

with open(HERE / ".." / ".." / "strat_022" / "recheck" / "_additions_selected.csv") as f:
    DELISTED = sorted({row["symbol"] for row in csv.DictReader(f)})

ALL = sorted(set(C4_CORE) | set(WATCHLIST) | set(DELISTED))
# names Alpaca cannot serve cleanly (class shares not in paper data under these tickers)
ALPACA_SKIP = {"BRK.A", "BRK.B"}

def listed_for_alpaca() -> list[str]:
    return [s for s in sorted(set(C4_CORE) | set(WATCHLIST)) if s not in ALPACA_SKIP]

if __name__ == "__main__":
    print(f"C4 core: {len(C4_CORE)}, delisted additions: {len(DELISTED)}, "
          f"watchlist: {len(WATCHLIST)}, union: {len(ALL)}")
    print(f"alpaca download list: {len(listed_for_alpaca())}")
