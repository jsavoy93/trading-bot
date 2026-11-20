"""HOLDOUT-MOM-001: holdout panel built from recorded split records only.

Copy of SPLITSCREEN-002 build_splits_panel.py with plumbing changes ONLY:
  - rows kept to 2026-09-29 (instead of 2024-12-31)
  - split-record cutoff 2026-09-29 (instead of 2024-12-31)
  - raw split records reused read-only from splitscreen-002 (OFFLINE, no fetch)
  - extra summary counters for the 2025-01+ holdout window
Cleaning/matching rule is verbatim: |r/q-1| <= 0.10, +/-2 trading-day window,
closest ratio. RESEARCH ONLY: reads parquet, writes under scripts_holdout/.
"""
import json, sys, time
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent                 # scripts_holdout/
ROOT = HERE.parent                                     # holdout-mom-001/
SRC = "/root/.openclaw/worktrees/STRAT-027/experiments/strat_027/data/panel.parquet"
RAW = Path("/root/research/splitscreen-002/splits_raw_completedicts.json")  # read-only reuse
OUT = HERE / "panel_holdout.parquet"
DEV0, DEV1 = pd.Timestamp("2021-01-01"), pd.Timestamp("2026-09-29")
HOLD0 = pd.Timestamp("2025-01-01")

df = pd.read_parquet(SRC, columns=["symbol", "date", "open", "close", "volume"])
df["date"] = pd.to_datetime(df["date"])
df = df[(df.date >= pd.Timestamp("2019-01-02")) & (df.date <= DEV1)].copy()
syms = np.sort(df.symbol.unique())
print("panel symbols", len(syms), "rows", len(df), flush=True)

# ---- 1. cached split records (never fetched; OFFLINE=True always) ----
OFFLINE = True
raw = json.loads(RAW.read_text())
done = set(syms)
need = []
print("need fetch", len(need), flush=True)

# ---- 2. apply splits ----
df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
applied, skipped, no_lookup, pre2021 = [], [], [], []
pos = {s: np.flatnonzero(df.symbol.values == s) for s in syms}
recs = []
for s in syms:
    rec = raw.get(s, {})
    if not rec or "__error__" in rec:
        no_lookup.append(s); continue
    for dstr, q in rec.items():
        d = np.datetime64(dstr)
        if d > np.datetime64("2026-09-29"):
            continue
        if d < np.datetime64("2019-01-02"):
            pre2021.append(s); continue
        recs.append((s, d, float(q)))
recs.sort(key=lambda x: (x[1], x[0]), reverse=True)
for s, d, q in recs:
    idx = pos[s]
    dates = df.date.values[idx]
    close = df.close.values[idx]
    k = int(np.searchsorted(dates, d))
    best, br = None, 0.0
    for j in range(max(1, k-2), min(len(idx), k+3)):
        a, b = close[j-1], close[j]
        if a > 0 and b > 0:
            r = b / a
            if best is None or abs(r / q - 1) < abs(br / q - 1):
                best, br = j, r
    if best is None or abs(br / q - 1) > 0.10:
        skipped.append((s, str(pd.Timestamp(d).date()), q, None if best is None else round(float(br), 4)))
        continue
    f = 1.0 / q
    rows = idx[dates < dates[best]]
    df.loc[rows, "open"] = df.loc[rows, "open"].values * f
    df.loc[rows, "close"] = df.loc[rows, "close"].values * f
    df.loc[rows, "volume"] = df.loc[rows, "volume"].values * f
    applied.append((s, str(pd.Timestamp(d).date()), q, round(float(br), 4)))

# ---- 3. BHVN spot-check ----
def closes_around(sym, around):
    sub = df.loc[df.symbol.values == sym].reset_index(drop=True)
    j = int(np.searchsorted(sub.date.values, np.datetime64(around)))
    return [(str(sub.date[i].date()), round(float(sub.close[i]), 2)) for i in range(max(0, j-2), min(len(sub), j+2))]
bhvn = closes_around("BHVN", "2022-10-04")

# ---- 4. unexplained >40% one-day moves ----
big = []
for s in syms:
    sub = df.loc[df.symbol.values == s]
    if len(sub) < 2: continue
    d_, c_ = sub.date.values, sub.close.values
    ok = (c_[:-1] > 0) & (c_[1:] > 0)
    r = np.where(ok, c_[1:] / c_[:-1], 1.0)
    dd = d_[1:]
    m = (np.abs(r - 1) > 0.40) & (ok) & (dd >= np.datetime64("2021-01-01"))
    for j in np.flatnonzero(m):
        big.append((s, str(pd.Timestamp(dd[j]).date()), round(float(r[j]), 4)))
big.sort(key=lambda x: -abs(np.log(x[2])))
big_dev = [b for b in big if b[1] <= "2024-12-31"]
big_hold = [b for b in big if b[1] >= "2025-01-01"]
applied_hold = [a for a in applied if a[1] >= "2025-01-01"]
summary = {
    "panel_first_date": str(pd.Timestamp(df.date.min()).date()),
    "panel_last_date": str(pd.Timestamp(df.date.max()).date()),
    "split_record_cutoff": "2026-09-29",
    "panel_symbols": int(len(syms)),
    "symbols_with_split_record": int(sum(1 for s in syms if raw.get(s) and "__error__" not in raw.get(s, {}))),
    "symbols_with_split_applied": int(len({a[0] for a in applied})),
    "splits_applied": len(applied),
    "splits_applied_holdout_2025plus": len(applied_hold),
    "splits_skipped_already_adjusted": len(skipped),
    "symbols_no_lookup": len(no_lookup),
    "symbols_pre2019_only_records": len(set(pre2021)),
    "unexplained_moves_gt40_total": len(big),
    "unexplained_dev_moves_gt40": len(big_dev),
    "unexplained_holdout_moves_gt40": len(big_hold),
    "top10": big[:10],
    "holdout_applied": applied_hold[:20],
    "holdout_big_moves": big_hold[:20],
    "skipped_sample": skipped[:15],
    "applied_sample": applied[:10],
    "bhvn": bhvn,
}
(HERE / "splits_summary_holdout.json").write_text(json.dumps(summary, indent=1))
df.to_parquet(OUT, index=False)
print(json.dumps(summary, indent=1))
