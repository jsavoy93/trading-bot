"""HOLDOUT-MOM-001: the ONE holdout evaluation of MOM 12-1 (frozen LONGSHORT-001 rules).

Extended copy of SPLITSCREEN-002 scripts/run_mom_vol_only.py. Differences ONLY:
  (a) after the DEV section it computes the HOLDOUT slice with the verbatim
      logic of run_study.compute_holdout;
  (b) VOL is not evaluated (MOM only);
  (c) engine/run_study are imported from splitscreen-002/scripts (frozen code);
  (d) output dir is the one given on the command line.
Zero parameter changes. RESEARCH ONLY: no orders, no database.
"""
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
SPLIT = "/root/research/splitscreen-002/scripts"
sys.path.insert(0, SPLIT)
import engine as E
import run_study as R


def compute_holdout(md_list, dev_rows, hold_rows, legs_long, legs_short,
                    elig_list, n_leg_list, keys):
    """Verbatim copy of run_study.compute_holdout body (dev_passed gate removed
    because MOM already passed every dev bar in SPLITSCREEN-002; the gate value
    is asserted against the stored DEV results before this is called)."""
    all_idx = list(dev_rows) + list(hold_rows)
    md_all = [md_list[i] for i in all_idx]
    long_all = E.simulate_leg(md_all, [legs_long[i] for i in all_idx], "long")
    short_all = E.simulate_leg(md_all, [legs_short[i] for i in all_idx], "short")
    net_all = E.net_series(long_all, short_all)
    draws_all = R.random_monthly_nets(md_all, [elig_list[i] for i in all_idx],
                                      [n_leg_list[i] for i in all_idx], E.N_DRAWS, E.SEED)
    rand_all = draws_all.mean(axis=0)
    h = np.arange(len(dev_rows), len(all_idx))
    hnet = net_all[h]
    hrand = rand_all[h]
    hm = R.window_metrics(hnet, hnet - hrand, hrand,
                          (long_all.net - long_all.cost)[h],
                          (-short_all.net - short_all.borrow - short_all.cost)[h],
                          draws_all[:, h].mean(axis=1))
    hm["bars"] = {}
    monthly = {
        "months": [keys[i] for i in hold_rows],
        "net": [float(x) for x in hnet],
        "excess": [float(x) for x in (hnet - hrand)],
        "random_mean": [float(x) for x in hrand],
        "long_leg": [float(x) for x in (long_all.net - long_all.cost)[h]],
        "short_leg": [float(x) for x in (-short_all.net - short_all.borrow - short_all.cost)[h]],
    }
    return hm, monthly


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
    # GATE-SLICE FIX (plumbing, not rules): engine.build_months blocks a month
    # whose exit day is on/below its decision lookback, and 'usable' checks
    # exit_date <= PANEL_LAST. On a panel that continues past DEV (this one ends
    # 2026-09-29 vs 2024-12-31) 2024-12's exit day (2025-01-02) exists, so DEV
    # would carry 48 months instead of the 47 the DEV gate froze. The DEV GATE
    # is therefore evaluated on exactly the frozen DEV slice
    # (dev months with exit_date <= 2024-12-31); only then is the holdout, whose
    # logic needs the calendar to continue, simulated once over dev+hold.
    gate_rows = [i for i, m in enumerate(usable)
                 if R.DEV_START <= m.period <= R.DEV_END and m.exit_date <= pd.Timestamp("2024-12-31")]
    dev_rows = [i for i, m in enumerate(usable) if R.DEV_START <= m.period <= R.DEV_END]
    print(f"dev gate rows {len(gate_rows)} "
          f"({keys[gate_rows[0]]} .. {keys[gate_rows[-1]]}); "
          f"dev sim rows {len(dev_rows)} ({keys[dev_rows[0]]} .. {keys[dev_rows[-1]]})", flush=True)
    hold_rows = [i for i, m in enumerate(usable)
                 if m.period >= R.HOLD_START and m.exit_date <= R.PANEL_LAST]
    print(f"usable months: {len(usable)} ({keys[0]} .. {keys[-1]}) dev={len(dev_rows)} hold={len(hold_rows)}", flush=True)
    import subprocess
    sha = subprocess.run(["sha256sum", str(panel_path)], capture_output=True, text=True).stdout.split()[0]
    results = {"panel_path": str(panel_path), "panel_sha256": sha, "seed": E.SEED,
               "n_draws": E.N_DRAWS, "cost_per_side": E.COST_PER_SIDE,
               "borrow_per_year": E.BORROW_PER_YEAR,
               "dev_keys": [keys[i] for i in gate_rows],
               "dev_sim_keys_note": "dev gate = 2021-01..2024-12 with exit_date <= 2024-12-31 (47 months, frozen SPLITSCREEN-002 slice)",
               "variants": {}}
    variant, factor = ("MOM_12_1", "MOM")     # VOL not evaluated
    legs_long, legs_short, elig_list, n_leg_list = [], [], [], []
    for md in md_list:
        lg, sh, el = E.select_legs(md, factor)
        legs_long.append(lg); legs_short.append(sh)
        elig_list.append(np.flatnonzero(el)); n_leg_list.append(len(lg))
    md_dev = [md_list[i] for i in gate_rows]
    long_dev = E.simulate_leg(md_dev, [legs_long[i] for i in gate_rows], "long")
    short_dev = E.simulate_leg(md_dev, [legs_short[i] for i in gate_rows], "short")
    net_dev = E.net_series(long_dev, short_dev)
    print(f"{variant}: running {E.N_DRAWS} random draws (dev) ...", flush=True)
    draws_dev = R.random_monthly_nets(md_dev, [elig_list[i] for i in gate_rows],
                                      [n_leg_list[i] for i in gate_rows], E.N_DRAWS, E.SEED)
    rand_dev = draws_dev.mean(axis=0)
    dev_m = R.window_metrics(net_dev, net_dev - rand_dev, rand_dev,
                             long_dev.net - long_dev.cost,
                             -short_dev.net - short_dev.borrow - short_dev.cost,
                             draws_dev.mean(axis=1))
    dev_m["bars"] = R.dev_bars(dev_m)
    results["variants"][variant] = {"regime_filter": None,
                                    "windows": {"dev": dev_m},
                                    "monthly": {"months": [keys[i] for i in gate_rows],
                                                "net": [float(x) for x in net_dev]}}
    print(json.dumps({"variant": variant, "dev_bars": dev_m["bars"],
                      "dev_t": dev_m["t_stat_excess"],
                      "dev_pct": dev_m["percentile_vs_random"]}), flush=True)
    if not R.holdout_allowed(dev_m["bars"]):
        raise SystemExit("FROZEN RULE: dev bars failed on this panel -> holdout not computed")
    # ---------------- HOLDOUT (exactly once) ----------------
    print(f"{variant}: running {E.N_DRAWS} random draws (holdout) ...", flush=True)
    hm, monthly = compute_holdout(md_list, dev_rows, hold_rows, legs_long, legs_short,
                                  elig_list, n_leg_list, keys)
    bars, cut_pct, t_cut = R.holdout_bars(hm, k=1)
    hm["bars"] = bars
    hm["bars_cutoffs"] = {"percentile_cut": cut_pct, "t_cut": t_cut}
    results["variants"][variant]["windows"]["holdout"] = hm
    results["variants"][variant]["monthly_holdout"] = monthly
    results["holdout_keys"] = [keys[i] for i in hold_rows]
    results["months_used"] = {"total": len(usable), "dev": len(dev_rows), "holdout": len(hold_rows)}
    (out / "results.json").write_text(json.dumps(results, indent=1, default=float))
    print(json.dumps({"variant": variant, "holdout": hm}, default=float), flush=True)
    print("wrote", out / "results.json", flush=True)


if __name__ == "__main__":
    main(sys.argv)
