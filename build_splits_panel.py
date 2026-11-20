"""SPLITSCREEN-002: panel built from recorded split records only."""
import json, sys, time
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
SRC = "/root/.openclaw/worktrees/STRAT-027/experiments/strat_027/data/panel.parquet"
RAW = HERE / "splits_raw.json"
DONE = HERE / "splits_fetch_done.json"   # symbols Yahoo really returned (empty = no split data)
OUT = HERE / "panel_splits.parquet"
DEV0, DEV1 = pd.Timestamp("2021-01-01"), pd.Timestamp("2024-12-31")

df = pd.read_parquet(SRC, columns=["symbol", "date", "open", "close", "volume"])
df["date"] = pd.to_datetime(df["date"])
df = df[df.date <= DEV1].copy()                      # never carry holdout rows forward
syms = np.sort(df.symbol.unique())
print("panel symbols", len(syms), "rows", len(df), flush=True)

# ---- 1. cached split records (never fetched twice) ----
# offline mode: workers 1-2 already fetched; never re-fetch (SPLITSCREEN-002 rule)
OFFLINE = (HERE / "splits_raw_completedicts.json").exists()
if OFFLINE:
    RAW = HERE / "splits_raw_completedicts.json"
raw = json.loads(RAW.read_text()) if RAW.exists() else {}
done = set(syms) if OFFLINE else (set(json.loads(DONE.read_text())) if DONE.exists() else set())
need = [s for s in syms if s not in done]   # retry gaps AND empties; record real empties in done
print("need fetch", len(need), flush=True)
if need:
    import yfinance as yf
    B = 200
    for i in range(0, len(need), B):
        chunk = need[i:i + B]
        try:
            yf.set_tz_cache_location(str(HERE / ".yf"))
            res = yf.Tickers(" ".join(chunk))
            for s in chunk:
                try:
                    sp = res.tickers[s].splits
                    raw[s] = {str(pd.Timestamp(k).date()): float(v) for k, v in sp.items()}
                    done.add(s)          # fetched successfully, even if empty
                except Exception as e:
                    raw[s] = {"__error__": str(e)[:120]}
        except Exception as e:
            for s in chunk:
                raw.setdefault(s, {})
                if not raw[s]: raw[s] = {"__error__": "batch:" + str(e)[:120]}
        RAW.write_text(json.dumps(raw)); DONE.write_text(json.dumps(sorted(done)))
        print(f"batch {i//B+1}/{(len(need)+B-1)//B} ok", flush=True)
        time.sleep(1)

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
        if d > np.datetime64("2024-12-31"):
            continue
        if d < np.datetime64("2019-01-02"):
            pre2021.append(s); continue
        recs.append((s, d, float(q)))
recs.sort(key=lambda x: (x[1], x[0]), reverse=True)   # latest ex-date first
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

# ---- 3. BHVN spot-check: contiguous per symbol, shares in symbol space ----
def closes_around(sym, around):
    sub = df.loc[df.symbol.values == sym].reset_index(drop=True)
    j = int(np.searchsorted(sub.date.values, np.datetime64(around)))
    return [(str(sub.date[i].date()), round(float(sub.close[i]), 2)) for i in range(max(0, j-2), min(len(sub), j+2))]
bhvn = closes_around("BHVN", "2022-10-04")

# ---- 4. unexplained >40% DEV one-day moves (symbol-contiguous) ----
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
summary = {
    "panel_symbols": int(len(syms)),
    "symbols_with_split_record": int(sum(1 for s in syms if raw.get(s) and "__error__" not in raw.get(s, {}))),
    "symbols_with_split_applied": int(len({a[0] for a in applied})),
    "splits_applied": len(applied),
    "splits_skipped_already_adjusted": len(skipped),
    "symbols_no_lookup": len(no_lookup),
    "symbols_pre2021_only_records": len(set(pre2021)),
    "unexplained_dev_moves_gt40": len(big),
    "top10": big[:10],
    "skipped_sample": skipped[:15],
    "applied_sample": applied[:10],
    "bhvn": bhvn,
}
(HERE / "splits_summary.json").write_text(json.dumps(summary, indent=1))
df.to_parquet(OUT, index=False)
print(json.dumps(summary, indent=1))
