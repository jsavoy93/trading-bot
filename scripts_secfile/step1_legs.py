"""SECFILE-001 plumbing: panel check, DEV legs, SEC full-index fetch, CIK mapping, form index."""
import hashlib, json, time, urllib.request
from pathlib import Path
import numpy as np, pandas as pd, sys
HERE = Path("/root/.openclaw/worktrees/SECFILE-001")
sys.path.insert(0, str(HERE / "scripts_holdout"))
sys.path.insert(0, "/root/research/splitscreen-002/scripts")
import engine_fixed as E
import run_study as R
RES = Path("/root/research/secfile-001"); DATA = RES / "data"; DATA.mkdir(parents=True, exist_ok=True)

PANEL = Path("/root/research/holdout-mom-001/scripts_holdout/panel_holdout.parquet")
sha = hashlib.sha256(PANEL.read_bytes()).hexdigest()
assert sha.startswith("4387206b") and sha.endswith("c071"), sha
print("panel sha256 OK", sha[:8], sha[-4:], flush=True)

DEV_START, DEV_END = pd.Period("2021-01", "M"), pd.Period("2024-12", "M")
panel = E.load_panel(PANEL); months = E.build_months(panel); mds = E.compute_month_data(panel, months)
syms = list(panel.symbols)
rows = [i for i, m in enumerate(months) if DEV_START <= m.period <= DEV_END and m.exit_date <= pd.Timestamp("2024-12-31")]
md_rows = [mds[i] for i in rows]
keys = [months[i].key for i in rows]
dec = [months[i].decision_date for i in rows]
legs = [E.select_legs(md, "MOM") for md in md_rows]
long_syms = [[syms[s] for s in lg] for lg, sh, el in legs]
short_syms = [[syms[s] for s in sh] for lg, sh, el in legs]
print("DEV months", len(rows), keys[0], "..", keys[-1], "max decision_date", max(dec).date(), flush=True)
json.dump({"keys": keys, "dec": [d.strftime("%Y-%m-%d") for d in dec],
           "long": long_syms, "short": short_syms}, open(DATA / "dev_legs.json", "w"))
print("long names/month mean", round(np.mean([len(x) for x in long_syms]), 2),
      "short", round(np.mean([len(x) for x in short_syms]), 2), flush=True)

UA = {"User-Agent": "SmartBot research"}
def get(url, dest):
    if dest.exists() and dest.stat().st_size > 1000:
        return "cached"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        b = r.read()
    dest.write_bytes(b); time.sleep(0.25); return f"{len(b)} bytes"

n_idx = 0
for y, q in [(2020, 4)] + [(yy, qq) for yy in (2021, 2022, 2023, 2024) for qq in (1, 2, 3, 4)]:
    p = DATA / f"form_{y}_Q{q}.idx"
    print(p.name, get(f"https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.idx", p), flush=True)
    n_idx += 1
print("index files", n_idx, flush=True)
for u in ["https://www.sec.gov/files/company_tickers.json", "https://www.sec.gov/files/company_tickers_exchange.json"]:
    p = DATA / u.rsplit("/", 1)[1]
    print(p.name, get(u, p), flush=True)
