"""SECFILE-001: CIK mapping coverage, per-month removal counts, descriptive returns."""
import json
from pathlib import Path
import numpy as np, pandas as pd
DATA = Path("/root/research/secfile-001/data")
legs = json.load(open(DATA / "dev_legs.json")); t2c = json.load(open(DATA / "ticker_cik.json"))
fi = pd.read_parquet(DATA / "filings.parquet")
REG = {"S-1", "S-3", "F-1", "F-3", "S-3ASR", "F-3ASR"}
OFF = {"424B1", "424B2", "424B3", "424B4", "424B5", "424B7"}
dec = pd.to_datetime(legs["dec"]); long_ = legs["long"]
assert dec.max() < pd.Timestamp("2025-01-01")
ci = fi.groupby("cik").date.apply(lambda s: np.sort(s.values)).to_dict()
n_cik = {}
def removed(sym, d, forms):
    c = t2c.get(sym.upper())
    if c is None: return False
    ds = ci.get(c)
    if ds is None: return False
    i = np.searchsorted(ds, np.datetime64(d), side="left")  # only strictly-before dates
    lo = np.datetime64(d - pd.Timedelta(days=30))
    return bool(((ds[:i] >= lo) & np.isin(fi_cik_form, []).sum() == 0) or False) if False else None
# simpler: per-CIK set of forms -> iterate dates
cik_form = {}
for c, d, f in zip(fi.cik.values, fi.date.values, fi.form.values):
    cik_form.setdefault(c, []).append((d, f))
for c in cik_form: cik_form[c].sort()
def removed(sym, d, forms):  # noqa: F811
    c = t2c.get(sym.upper())
    if c is None or c not in cik_form: return False
    lo = np.datetime64(d - pd.Timedelta(days=30))
    for dd, f in cik_form[c]:
        if dd >= np.datetime64(d): break
        if dd >= lo and f in forms: return True
    return False
cov = tot = 0
res = {"coverage": {}, "removed_lists": {}}
for name, forms in (("V1", REG), ("V2", REG | OFF)):
    nrem = []; removed_names = []
    for i, d in enumerate(dec):
        r = [s for s in long_[i] if removed(s, d, forms)]
        nrem.append(len(r)); removed_names.append(r)
    res["removed_lists"][name] = removed_names
    res[name] = {"n_removed": nrem, "avg": float(np.mean(nrem)), "total": int(np.sum(nrem)),
                 "months_with_any": int(sum(1 for x in nrem if x > 0)),
                 "max": int(max(nrem)), "pct_of_long": float(np.mean(nrem) / np.mean([len(x) for x in long_]))}
    print(name, res[name], flush=True)
for i in range(len(dec)):
    for s in long_[i]:
        tot += 1; cov += s.upper() in t2c
res["coverage"] = {"long_name_months": tot, "mapped": cov, "pct": 100.0 * cov / tot}
allcik = set(fi.cik.unique())
res["coverage"]["mapped_with_recent_filings_pct"] = 100.0 * sum(1 for i in range(len(dec)) for s in long_[i]
    if t2c.get(s.upper()) in allcik) / tot
print("coverage", res["coverage"], flush=True)
res["filings_index"] = {"n": int(len(fi)), "by_form": fi.form.value_counts().to_dict(),
                        "first": str(fi.date.min().date()), "last": str(fi.date.max().date())}
json.dump(res, open("/root/research/secfile-001/results_index.json", "w"), default=float)
