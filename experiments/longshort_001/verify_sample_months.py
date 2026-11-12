"""Cross-check the independent hand recomputation against the engine.

Recomputes the three sample months with engine.py and compares name-level and
month-level numbers against results/independent_check.json (computed by
check_sample_months.py with plain pandas).

Run:  python3 verify_sample_months.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import engine as E

HERE = Path(__file__).resolve().parent
CHECK = HERE / "results" / "independent_check.json"
OUT = HERE / "results" / "sample_month_verification.json"

DEV_START = pd.Period("2021-01", "M")
DEV_END = pd.Period("2024-12", "M")
TOL = 1e-9


def main():
    panel = E.load_panel()
    months = E.build_months(panel)
    usable = [m for m in months if m.exit_date <= pd.Timestamp("2026-09-29")
              and E.month_end_index(panel, m.period - 13) >= 0]
    md_list = E.compute_month_data(panel, usable)
    dev = [(i, m) for i, m in enumerate(usable) if DEV_START <= m.period <= DEV_END]
    dev_idx = [i for i, _ in dev]
    md_dev = [md_list[i] for i in dev_idx]

    check = {c["month"]: c for c in json.loads(CHECK.read_text())}
    report = {}

    for variant in ("MOM", "VOL"):
        legs = [E.select_legs(md_list[i], variant)[0 if variant == "MOM" else 0]
                for i in dev_idx]
        short_legs = []
        for i in dev_idx:
            lg, sh, _ = E.select_legs(md_list[i], variant)
            short_legs.append(sh)
        long_res = E.simulate_leg(md_dev, legs, "long")
        short_res = E.simulate_leg(md_dev, short_legs, "short")
        net = E.net_series(long_res, short_res)

        vres = {}
        for pos, (i, m) in enumerate(dev):
            key = m.key
            if key not in check:
                continue
            c = check[key][variant]
            lg, sh, _ = E.select_legs(md_list[i], variant)
            l_names = [str(panel.symbols[s]) for s in lg]
            s_names = [str(panel.symbols[s]) for s in sh]
            l_gross = float(long_res.net[pos])
            l_cost = float(long_res.cost[pos])
            s_gross = float(short_res.net[pos])
            s_cost = float(short_res.cost[pos])
            s_borrow = float(short_res.borrow[pos])
            l_pnl = l_gross - l_cost
            s_pnl = -s_gross - s_borrow - s_cost
            rows = {
                "n_eligible": [int(E.select_legs(md_list[i], variant)[2].sum()), c["n_eligible"]],
                "n_leg": [int(len(lg)), c["n_leg"]],
                # the engine returns each leg sorted by symbol, the independent
                # checker returns it in factor-rank order -> compare as sets
                "long_leg_set_match": sorted(l_names) == sorted(c["long_names_head_all"]),
                "short_leg_set_match": sorted(s_names) == sorted(c["short_names_head_all"]),
                "long_gross": [l_gross, c["long_gross"]],
                "long_cost": [l_cost, c["long_cost"]],
                "long_pnl": [l_pnl, c["long_pnl"]],
                "short_gross": [s_gross, c["short_gross"]],
                "short_borrow": [s_borrow, c["short_borrow"]],
                "short_cost": [s_cost, c["short_cost"]],
                "short_pnl": [s_pnl, c["short_pnl"]],
                "net": [float(net[pos]), c["net"]],
            }
            numeric_ok = True
            for k, v in rows.items():
                if k in ("long_leg_set_match", "short_leg_set_match"):
                    continue
                a, b = v
                if k in ("n_eligible", "n_leg"):
                    numeric_ok &= (a == b)
                else:
                    numeric_ok &= abs(a - b) < 1e-9
            names_ok = rows["long_leg_set_match"] and rows["short_leg_set_match"]
            rows["engine"] = {k: v[0] for k, v in rows.items() if isinstance(v, list)}
            rows["independent"] = {k: v[1] for k, v in rows.items() if isinstance(v, list)}
            rows["names_match"] = names_ok
            rows["numbers_match"] = bool(numeric_ok)
            rows["MATCH"] = bool(names_ok and numeric_ok)
            vres[key] = rows
        report[variant] = vres

    OUT.write_text(json.dumps(report, indent=2, default=str))
    for v, months in report.items():
        for k, row in months.items():
            print(v, k, "MATCH" if row["MATCH"] else "MISMATCH",
                  "| net engine %.12f independent %.12f" % (row["engine"]["net"], row["independent"]["net"]))
    all_match = all(r["MATCH"] for v in report.values() for r in v.values())
    print("ALL SAMPLE MONTHS MATCH:", all_match)
    return 0 if all_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
