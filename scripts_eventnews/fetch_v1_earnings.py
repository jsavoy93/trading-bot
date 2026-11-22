"""V1 earnings dates, DEV 2021-2024 ONLY (no data after 2024-12-31 requested).
Source: Nasdaq earnings calendar (day list, cached per day) -> yfinance fallback.
Output: data/v1_earnings_dates.json {symbol: [YYYY-MM-DD...]}, data/v1_manifest.json."""
import json, sys, time
from pathlib import Path
import urllib.request
import pandas as pd, numpy as np
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts_holdout"))
import engine_fixed as E
DATA = HERE / "data"; DATA.mkdir(exist_ok=True)
CACHE = DATA / "nasdaq_days.json"
LO, HI = pd.Timestamp("2020-12-01"), pd.Timestamp("2024-12-31")

panel = E.load_panel(HERE / "panel_splits.parquet")
months = E.build_months(panel)
dev = [m for m in months if pd.Period("2021-01", "M") <= m.period <= pd.Period("2024-12", "M")
       and m.exit_date <= pd.Timestamp("2024-12-31")]
cands = set()
mds = E.compute_month_data(panel, months)
md_by_key = {m.key: md for m, md in zip(months, mds)}
for m in dev:
    md = md_by_key[m.key]
    el = np.flatnonzero(np.isfinite(md.mom) & np.isfinite(md.ret))
    cands.update(panel.symbols[el].tolist())
cands = sorted(cands)
print("candidates", len(cands), flush=True)

days = json.loads(CACHE.read_text()) if CACHE.exists() else {}
HDR = {"User-Agent": "Mozilla/5.0 (research)", "Accept": "application/json"}
d = LO
t0 = time.time(); fetched = 0
while d <= HI:
    if d.weekday() < 5:
        k = d.strftime("%Y-%m-%d")
        if k not in days:
            if time.time() - t0 > 1500: break
            url = f"https://api.nasdaq.com/api/calendar/earnings?date={k}"
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=20) as r:
                    js = json.loads(r.read().decode())
                rows = ((js or {}).get("data") or {}).get("rows") or []
                days[k] = {row["symbol"]: str(k) for row in rows if row.get("symbol")}
                fetched += 1
            except Exception as e:
                days[k] = {}
            time.sleep(0.25)
    d += pd.Timedelta(days=1)
CACHE.write_text(json.dumps(days, default=str))
print("nasdaq days cached", len(days), "fetched this run", fetched, flush=True)

dates = {}
for k, m in days.items():
    for sym in m:
        if sym in cands:
            dates.setdefault(sym, set()).add(k)
need = [s for s in cands if s not in dates]
print("symbols with nasdaq dates", len(dates), "fallback needed", len(need), flush=True)
import yfinance as yf
import warnings; warnings.filterwarnings("ignore")
for i, sym in enumerate(need):
    try:
        ed = yf.Ticker(sym).get_earnings_dates(limit=40)
        got = sorted({pd.Timestamp(ts).tz_localize(None) if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).strftime("%Y-%m-%d") for ts in ed.index})
    except Exception:
        got = []
    got = [g for g in got if LO <= pd.Timestamp(g) <= HI]
    if got:
        dates[sym] = set(got)
print("fallback done; symbols with dates", len(dates), "of", len(cands), flush=True)
out = {s: sorted(v) for s, v in dates.items()}
(DATA / "v1_earnings_dates.json").write_text(json.dumps(out, default=str))
man = {"candidates": len(cands), "with_dates": len(out),
       "coverage_pct": 100.0 * len(out) / len(cands),
       "nasdaq_days_cached": len(days), "nasdaq_days_fetched_this_run": fetched,
       "requested_range": [str(LO.date()), str(HI.date())],
       "hard_cutoff": "2024-12-31 (nothing after is requested)"}
(DATA / "v1_manifest.json").write_text(json.dumps(man, indent=1, default=str))
print(json.dumps(man), flush=True)
