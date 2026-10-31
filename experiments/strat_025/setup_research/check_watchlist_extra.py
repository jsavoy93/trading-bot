"""STRAT-025 setup research — SUPPLEMENTARY check: do the 191 extra bot-watchlist names
(analysis_status.json watchlist, not in the frozen prereg universe) change any dev verdict?

Counts, over the dev window 2021-01-01..2024-12-31:
  - symbols with any data
  - symbols with >=1 ELIGIBLE symbol-day (vol20 >= 1M and close >= $5, per prereg §3)
  - signal days contributed per setup (pre-reg setups only; RS setups need cross-sectional
    ranks and are excluded from this count — noted in RESULTS.md)
Also reports the eligibility rate for the frozen prereg universe for comparison.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

import engine as E
import setups as S

HERE = Path(__file__).resolve().parent
DEV = (pd.Timestamp("2021-01-01"), pd.Timestamp("2024-12-31"))
EX = HERE / "data_watchlist_extra"
PREREG_DATA = HERE / "data"

NON_CROSS = ["ema20_pullback", "high20_breakout_vol", "vcp_minervini", "gap_and_go",
             "gap_fill_fade", "inside_day", "nr7", "rsi2_uptrend_v2"]
FUNCS = {
    "ema20_pullback": S.ema20_pullback,
    "high20_breakout_vol": S.high20_breakout_vol,
    "vcp_minervini": S.vcp_minervini,
    "gap_and_go": S.gap_and_go,
    "gap_fill_fade": S.gap_fill_fade,
    "inside_day": S.inside_day,
    "nr7": S.nr7,
    "rsi2_uptrend_v2": S.rsi2_uptrend_v2,
}


def scan(d: Path):
    syms = sorted(d.glob("*.csv"))
    n_with_data = n_eligible_syms = 0
    elig_days = 0
    sig_counts = {k: 0 for k in NON_CROSS}
    for f in syms:
        df = E.load_symbol(f)
        if df is None:
            continue
        n_with_data += 1
        m = E.liq_ok(df) & (df["date"] >= DEV[0]) & (df["date"] <= DEV[1])
        e = int(m.sum())
        elig_days += e
        if e:
            n_eligible_syms += 1
        for k, fn in FUNCS.items():
            s = fn(df) & m
            sig_counts[k] += int(s.sum())
    return {"csv_files": len(syms), "symbols_with_data": n_with_data,
            "symbols_with_eligible_days": n_eligible_syms,
            "eligible_symbol_days": elig_days,
            "signal_days": sig_counts}


def main():
    out = {"prereg_universe": scan(PREREG_DATA),
           "watchlist_extra": scan(EX)}
    (HERE / "results" / "_watchlist_extra_check.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
