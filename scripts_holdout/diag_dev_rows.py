"""HOLDOUT-MOM-001 plumbing diagnostic (not an evaluation): why does DEV have
48 rows here vs 47 in SPLITSCREEN-002 results.json? RESEARCH ONLY."""
import sys, json, numpy as np
from pathlib import Path
sys.path.insert(0, "/root/research/splitscreen-002/scripts")
import pandas as pd
import engine as E, run_study as R
GATE_SLICE = True

def run(panel_path, drop_key=None):
    panel = E.load_panel(Path(panel_path)); months = E.build_months(panel)
    usable = [m for m in months if m.exit_date <= R.PANEL_LAST
              and E.month_end_index(panel, m.period - 13) >= 0]
    if drop_key: usable = [m for m in usable if m.key != drop_key]
    md = E.compute_month_data(panel, usable)
    dev = [i for i, m in enumerate(usable) if R.DEV_START <= m.period <= R.DEV_END]
    if GATE_SLICE: dev = [i for i in dev if usable[i].exit_date <= pd.Timestamp("2024-12-31")]
    ll, ls, el = [], [], []
    for i in dev:
        lg, sh, e = E.select_legs(md[i], "MOM"); ll.append(lg); ls.append(sh); el.append(np.flatnonzero(e))
    md_dev = [md[i] for i in dev]
    L = E.simulate_leg(md_dev, ll, "long"); S = E.simulate_leg(md_dev, ls, "short")
    net = E.net_series(L, S)
    draws = R.random_monthly_nets(md_dev, el, [len(x) for x in ll], E.N_DRAWS, E.SEED)
    rand = draws.mean(axis=0)
    m = R.window_metrics(net, net - rand, rand, L.net - L.cost,
                         -S.net - S.borrow - S.cost, draws.mean(axis=1))
    return {k: m[k] for k in ("n_months", "mean_monthly_net", "mean_monthly_excess",
                              "t_stat_excess", "percentile_vs_random", "max_drawdown")} | {"bars": R.dev_bars(m)}

ref = json.load(open("/root/research/splitscreen-002/results_splits/results.json"))["variants"]["MOM_12_1"]["windows"]["dev"]
out = {"ref_splitsscreen_002_dev": {k: ref[k] for k in ("n_months", "mean_monthly_net", "t_stat_excess", "percentile_vs_random")},
       "holdout_panel_dev_gate_slice": run("/root/research/holdout-mom-001/scripts_holdout/panel_holdout.parquet")}
for k in ("2024-11", "2024-12"):
    out[f"holdout_panel_dev_minus_{k}"] = run("/root/research/holdout-mom-001/scripts_holdout/panel_holdout.parquet", k)
out["dev_panel_dev"] = run("/root/research/splitscreen-002/panel_splits.parquet")
print(json.dumps(out, indent=1, default=float))
Path("/root/research/holdout-mom-001/scripts_holdout/diag_dev_rows.json").write_text(json.dumps(out, indent=1, default=float))
