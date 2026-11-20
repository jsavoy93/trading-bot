"""SPLITSCREEN-001 runner: LONGSHORT-001 MOM 12-1 + VOL 60d on a given panel.

Copies the frozen run_study.py main() body exactly, with ONE difference:
the DEV loop stops after the MOM and VOL variants (the other variants are not
re-run) and NO holdout is ever computed (dev-only task rule).
"""
import sys, json, platform
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as E
import run_study as R

def main(argv):
    panel_path = Path(argv[1]); out = Path(argv[2]); out.mkdir(parents=True, exist_ok=True)
    panel = E.load_panel(panel_path)
    months = E.build_months(panel)
    print(f"panel: {panel.dates.size} trading days, {panel.symbols.size} symbols", flush=True)
    usable = [m for m in months
              if m.exit_date <= R.PANEL_LAST
              and E.month_end_index(panel, m.period - 13) >= 0]
    md_list = E.compute_month_data(panel, usable)
    keys = [m.key for m in usable]
    dev_rows = [i for i, m in enumerate(usable) if R.DEV_START <= m.period <= R.DEV_END]
    import subprocess
    sha = subprocess.run(["sha256sum", str(panel_path)], capture_output=True, text=True).stdout.split()[0]
    results = {"panel_path": str(panel_path), "panel_sha256": sha, "seed": E.SEED,
               "n_draws": E.N_DRAWS, "cost_per_side": E.COST_PER_SIDE,
               "borrow_per_year": E.BORROW_PER_YEAR,
               "dev_keys": [keys[i] for i in dev_rows], "variants": {}}
    for variant, factor in (("MOM_12_1", "MOM"), ("VOL_60D", "VOL")):
        legs_long, legs_short, elig_list, n_leg_list = [], [], [], []
        for md in md_list:
            lg, sh, el = E.select_legs(md, factor)
            legs_long.append(lg); legs_short.append(sh)
            elig_list.append(np.flatnonzero(el)); n_leg_list.append(len(lg))
        md_dev = [md_list[i] for i in dev_rows]
        long_dev = E.simulate_leg(md_dev, [legs_long[i] for i in dev_rows], "long")
        short_dev = E.simulate_leg(md_dev, [legs_short[i] for i in dev_rows], "short")
        net_dev = E.net_series(long_dev, short_dev)
        print(f"{variant}: running {E.N_DRAWS} random draws ...", flush=True)
        draws_dev = R.random_monthly_nets(md_dev, [elig_list[i] for i in dev_rows],
                                          [n_leg_list[i] for i in dev_rows], E.N_DRAWS, E.SEED)
        rand_dev = draws_dev.mean(axis=0)
        dev_m = R.window_metrics(net_dev, net_dev - rand_dev, rand_dev,
                                 long_dev.net - long_dev.cost,
                                 -short_dev.net - short_dev.borrow - short_dev.cost,
                                 draws_dev.mean(axis=1))
        dev_m["bars"] = R.dev_bars(dev_m)
        results["variants"][variant] = {"regime_filter": None, "windows": {"dev": dev_m},
            "monthly": {"months": [keys[i] for i in dev_rows],
                        "net": [float(x) for x in net_dev]}}
        print(json.dumps({"variant": variant, "dev": dev_m}, default=float))
    (out / "results.json").write_text(json.dumps(results, indent=1, default=float))
    print("wrote", out / "results.json", flush=True)

if __name__ == "__main__":
    main(sys.argv)
