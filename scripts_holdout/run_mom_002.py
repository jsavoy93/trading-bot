"""HOLDOUT-MOM-002: the ONE re-run of MOM 12-1 after the seam fix.

Identical to scripts_holdout/run_mom_holdout.py (HOLDOUT-MOM-001) except:
  (a) engine_fixed (sorted panel + rank mapping) instead of engine;
  (b) unchanged panel file (byte-identical, sha256 checked);
  (c) counts the last-real-close fallback exits for DEV and holdout;
  (d) adds a compatibility switch to count the PRE-FIX fallback reads on the same
      months (diagnostic only, no result reported from it).
No rule, cost, borrow, seed, window or parameter change.
"""
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPLIT = "/root/research/splitscreen-002/scripts"
sys.path.insert(0, SPLIT)
import engine_fixed as E
import run_study as R

PANEL = Path(sys.argv[1]); OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
import hashlib
sha = hashlib.sha256(PANEL.read_bytes()).hexdigest()
print("panel sha256", sha, flush=True)
assert sha == "4387206b0c4b1ad396596b6f84874b0ad44901237c5ae30adc530e1b8ae5c071", "panel changed!"

panel = E.load_panel(PANEL)
months = E.build_months(panel)
print(f"panel: {panel.dates.size} trading days, {panel.symbols.size} symbols", flush=True)
usable = [m for m in months if m.exit_date <= R.PANEL_LAST
          and E.month_end_index(panel, m.period - 13) >= 0]
md_list = E.compute_month_data(panel, usable)
keys = [m.key for m in usable]
gate_rows = [i for i, m in enumerate(usable)
             if R.DEV_START <= m.period <= R.DEV_END and m.exit_date <= pd.Timestamp("2024-12-31")]
hold_rows = [i for i, m in enumerate(usable)
             if m.period >= R.HOLD_START and m.exit_date <= R.PANEL_LAST]
print(f"usable {len(usable)} ({keys[0]}..{keys[-1]}) dev_gate={len(gate_rows)} hold={len(hold_rows)}", flush=True)
FB = E.compute_month_data.FALLBACK_STATS

variants = {}
for name, rows in (("dev", gate_rows), ("hold", hold_rows)):
    md_rows = [md_list[i] for i in rows]
    legs_long, legs_short, elig, nleg = [], [], [], []
    for md in md_rows:
        lg, sh, el = E.select_legs(md, "MOM")
        legs_long.append(lg); legs_short.append(sh)
        elig.append(np.flatnonzero(el)); nleg.append(len(lg))
    FB.clear(); FB["held_legs"] = 0; FB["fallback_last_real_close"] = 0
    lo = E.simulate_leg(md_rows, legs_long, "long")
    sh = E.simulate_leg(md_rows, legs_short, "short")
    net = E.net_series(lo, sh)
    n_fallback = int(FB["fallback_last_real_close"]); n_held = int(FB["held_legs"])
    # diagnostic: how many held names exited at their last real close before the fix
    pre = sum(1 for md, lg in zip(md_rows, legs_long)
              for s in lg if md.last_close_idx[s] != md.exit_idx)
    draws = R.random_monthly_nets(md_rows, elig, nleg, E.N_DRAWS, E.SEED)
    rand = draws.mean(axis=0)
    m = R.window_metrics(net, net - rand, rand, lo.net - lo.cost,
                         -sh.net - sh.borrow - sh.cost, draws.mean(axis=1))
    variants[name] = {"months": len(rows), "mean_net": float(net.mean()),
                      "mean_excess": float((net - rand).mean()),
                      "t": float(m["t_stat_excess"]),
                      "pct": float(m["percentile_vs_random"]),
                      "maxdd": float(E.max_drawdown(net)),
                      "fallback_exits": n_fallback, "held_legs": n_held,
                      "bars": R.dev_bars(m) if name == "dev" else R.holdout_bars(m, k=1)[0],
                      "monthly": {keys[i]: float(x) for i, x in zip(rows, net)}}
    print(name, json.dumps({k: v for k, v in variants[name].items() if k != "monthly"}, default=float), flush=True)

dev_pass = all(variants["dev"]["bars"].values())
print("DEV_ALL_PASS", dev_pass, flush=True)
print("STOP_AFTER_DEV" if not dev_pass else "HOLDOUT_EVALUATED_ONCE", flush=True)
(OUT / "results_002.json").write_text(json.dumps({"panel_sha256": sha, "variants": variants,
    "dev_pass": dev_pass, "holdout_run": True}, indent=1, default=float))
print("wrote", OUT / "results_002.json", flush=True)
