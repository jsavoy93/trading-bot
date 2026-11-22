"""SECFILE-001: parse cached form.idx into a filing index and map tickers to CIKs."""
import json, re
from pathlib import Path
from collections import Counter
import numpy as np, pandas as pd
DATA = Path("/root/research/secfile-001/data")
REG = {"S-1", "S-3", "F-1", "F-3", "S-3ASR", "F-3ASR"}
OFF = {"424B1", "424B2", "424B3", "424B4", "424B5", "424B7"}
rows = []
for p in sorted(DATA.glob("form_*.idx")):
    for L in p.read_text(errors="replace").splitlines()[10:]:
        m = re.match(r"^(.*?)\s{2,}(\S.*?)\s{2,}(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\S+)\s*$", L)
        if not m: continue
        form, name, cik, date, path = m.groups()
        if form in REG or form in OFF:
            rows.append((form, int(cik), date, path))
    print(p.name, "parsed", len(rows), flush=True)
fi = pd.DataFrame(rows, columns=["form", "cik", "date", "path"])
fi["date"] = pd.to_datetime(fi["date"])
print("relevant filings", len(fi), fi.form.value_counts().to_dict(), flush=True)
print("date range", fi.date.min().date(), "..", fi.date.max().date(), flush=True)
assert fi.date.max() <= pd.Timestamp("2024-12-31"), "post-2024 filing leaked"
fi.to_parquet(DATA / "filings.parquet")

cur = json.loads((DATA / "company_tickers.json").read_text())
exc = json.loads((DATA / "company_tickers_exchange.json").read_text())
hist = {}
# exchange file: {"fields":[...], "data":[[cik,name,ticker,exchange],...]}
data = exc["data"] if isinstance(exc, dict) and "data" in exc else []
mapping = {}
for r in list(cur.values()) + [dict(zip(exc["fields"], d)) for d in data]:
    t = str(r.get("ticker", "")).upper().strip(); c = r.get("cik")
    if not t or c is None: continue
    if t in hist and hist[t] != int(c): print("conflict", t, flush=True)
    hist.setdefault(t, int(c))
json.dump(hist, open(DATA / "ticker_cik.json", "w"))
print("ticker->cik pairs", len(hist), flush=True)
print("forms in index by year", fi.groupby(fi.date.dt.year).form.count().to_dict(), flush=True)
