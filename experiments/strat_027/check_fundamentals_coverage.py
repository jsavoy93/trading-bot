"""STRAT-027 — coverage check for the point-in-time fundamentals panel.

Answers the question that decides whether the QUALITY / VALUE factors are usable:
for every monthly rebalance, how many of the LIQUID-ELIGIBLE names have a 10-K that
was already public, with the data needed to compute the factor?
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from fundamentals import fundamental_factors, load_fundamentals
from panel import ADV_WINDOW, MIN_ADV_SHARES, MIN_HISTORY_BARS, MIN_PRICE, load_panel

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def main() -> int:
    panel = load_panel()
    fund = load_fundamentals()
    R = len(panel.rebal_idx)
    dates = panel.rebal_dates
    last_close = np.vstack([panel.close_m[i - 1] for i in panel.rebal_idx]).astype(float)
    q, v = fundamental_factors(fund, panel.symbols, dates, last_close,
                              cache=DATA / "fundamental_factors.npz")

    rows = []
    for r in range(R):
        i = int(panel.rebal_idx[r])
        if i < MIN_HISTORY_BARS:
            continue
        d = pd.Timestamp(dates[r])
        last_close_r = panel.close_m[i - 1]
        adv = panel.volume_m[i - ADV_WINDOW:i].mean(axis=0)
        hist = np.isfinite(panel.close_m[:i]).cumsum(axis=0)[i - 1]
        base_elig = (np.isfinite(last_close_r) & (last_close_r >= MIN_PRICE)
                     & np.isfinite(adv) & (adv >= MIN_ADV_SHARES)
                     & (hist >= MIN_HISTORY_BARS) & np.isfinite(panel.open_m[i]))
        rows.append({
            "date": d.date().isoformat(),
            "base_eligible": int(base_elig.sum()),
            "with_roe": int((base_elig & np.isfinite(q[r])).sum()),
            "with_earnings_yield": int((base_elig & np.isfinite(v[r])).sum()),
            "universe_roe": int(np.isfinite(q[r]).sum()),
            "universe_earnings_yield": int(np.isfinite(v[r]).sum()),
        })

    df = pd.DataFrame(rows)
    df.to_csv(DATA / "fundamentals_coverage.csv", index=False)
    print(df.tail(18).to_string(index=False))
    dev = df[(df["date"] >= "2021-01-01") & (df["date"] < "2025-01-01")]
    hold = df[df["date"] >= "2025-01-01"]
    for label, part in (("DEV 2021-2024", dev), ("HOLDOUT 2025+", hold)):
        print(f"\n{label}: n_months {len(part)}")
        for c in ["base_eligible", "with_roe", "with_earnings_yield"]:
            print(f"  {c:22s} min {part[c].min():5d}  median {part[c].median():7.0f}  max {part[c].max():5d}")
    (DATA / "fundamentals_coverage.json").write_text(json.dumps({
        "rows": rows,
        "dev_median_roe": float(dev["with_roe"].median()),
        "dev_median_ey": float(dev["with_earnings_yield"].median()),
        "holdout_median_roe": float(hold["with_roe"].median()),
        "holdout_median_ey": float(hold["with_earnings_yield"].median()),
    }, indent=2))
    print(f"\nwrote {DATA/'fundamentals_coverage.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
