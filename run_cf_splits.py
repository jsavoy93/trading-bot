"""SPLITSCREEN-002 shim: byte-identical to scripts/crashfilter_run_clean_v2.py
except (1) panel input path -> panel_splits.parquet, (2) output file name.
Frozen rules/seeds/costs/borrow/eligibility unchanged (per task brief)."""

import sys, json
sys.path.insert(0, "/root/research/splitscreen-001/scripts")
import numpy as np, pandas as pd
import engine as E, run_study as R
panel = E.load_panel("/root/research/splitscreen-002/panel_splits.parquet")
months = E.build_months(panel)
usable = [m for m in months if m.exit_date <= R.PANEL_LAST and E.month_end_index(panel, m.period - 13) >= 0]
md_list = E.compute_month_data(panel, usable)
spy = int(np.flatnonzero(panel.symbols == "SPY")[0])
cl = panel.close_m[:, spy]
def bear(m):
    d = m.decision_idx
    return bool(d >= 504 and cl[d] / cl[d - 504] - 1 < 0)
flags = [bear(m) for m in usable]
dev = [i for i, m in enumerate(usable) if R.DEV_START <= m.period <= R.DEV_END]
L, S, EL, N = [], [], [], []
for i, md in enumerate(md_list):
    lg, sh, el = E.select_legs(md, "MOM")
    if flags[i]:
        lg = lg[:0]; sh = sh[:0]
    L.append(lg); S.append(sh); EL.append(np.flatnonzero(el)); N.append(len(lg))
mdd = [md_list[i] for i in dev]
lo = E.simulate_leg(mdd, [L[i] for i in dev], "long")
so = E.simulate_leg(mdd, [S[i] for i in dev], "short")
net = E.net_series(lo, so)
draws = R.random_monthly_nets(mdd, [EL[i] for i in dev], [N[i] for i in dev], E.N_DRAWS, 20261118)
rm = draws.mean(axis=0)
m = R.window_metrics(net, net - rm, rm, lo.net - lo.cost, -so.net - so.borrow - so.cost, draws.mean(axis=1))
m["bars"] = R.dev_bars(m)
out = {"panel_path": "/root/research/splitscreen-002/panel_splits.parquet",
       "flagged_dev_months": [usable[i].key for i in dev if flags[i]],
       "dev": m, "net": [float(x) for x in net], "keys": [usable[i].key for i in dev]}
json.dump(out, open("/root/research/splitscreen-001/results_after_clean/v2/crashfilter_results_splits.json", "w"), indent=1, default=float)
print(json.dumps({"flagged_dev_months": out["flagged_dev_months"]}), json.dumps(m, default=float)[:1500])
