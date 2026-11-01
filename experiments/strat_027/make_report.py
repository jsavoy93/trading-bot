"""STRAT-027 — render RESULTS.md from the results JSON (no hand-copied numbers)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def fmt_pct(x, digits=2):
    return f"{x:.{digits}%}" if x is not None else "n/a"


def fmt_num(x, digits=2):
    return f"{x:.{digits}f}" if x is not None else "n/a"


def block_table(blk: dict, label: str) -> list[str]:
    lines = []
    st = blk["strategy_net"]
    sg = blk["strategy_gross"]
    sp = blk["spy"]
    tg = blk["tstat_vs_random_gross"]
    tn = blk["tstat_vs_random_net"]
    bd = blk.get("beta_diag_vs_spy", {})
    lines.append(f"### {label}")
    lines.append("")
    lines.append("| Metric | Strategy (net of 10bps) | Strategy (gross) | SPY buy&hold | Random cloud (net) |")
    lines.append("|---|---|---|---|---|")
    lines.append(f"| Annualised return | {fmt_pct(st['ann_return'])} | {fmt_pct(sg['ann_return'])} | "
                 f"{fmt_pct(sp['ann_return'])} | mean {fmt_pct(blk['random_net_ann_return_mean'])} "
                 f"(p95 {fmt_pct(blk['random_net_ann_return_p95'])}) |")
    lines.append(f"| Annualised volatility | {fmt_pct(st['ann_vol'])} | {fmt_pct(sg['ann_vol'])} | "
                 f"{fmt_pct(sp['ann_vol'])} | - |")
    lines.append(f"| Sharpe (rf=0) | {fmt_num(st['sharpe'])} | {fmt_num(sg['sharpe'])} | "
                 f"{fmt_num(sp['sharpe'])} | mean {fmt_num(blk['random_net_sharpe_mean'])} |")
    lines.append(f"| Max drawdown | {fmt_pct(st['max_drawdown'])} | {fmt_pct(sg['max_drawdown'])} | "
                 f"{fmt_pct(sp['max_drawdown'])} | mean {fmt_pct(blk['random_net_maxdd_mean'])} |")
    lines.append(f"| Total return | {fmt_pct(st['total_return'])} | {fmt_pct(sg['total_return'])} | "
                 f"{fmt_pct(sp['total_return'])} | - |")
    lines.append(f"| Worst month | {fmt_pct(st['worst_period'])} | {fmt_pct(sg['worst_period'])} | "
                 f"{fmt_pct(sp['worst_period'])} | - |")
    lines.append(f"| Best month | {fmt_pct(st['best_period'])} | {fmt_pct(sg['best_period'])} | "
                 f"{fmt_pct(sp['best_period'])} | - |")
    lines.append(f"| Positive months | {st['positive_periods']}/{st['periods']} | - | "
                 f"{sp['positive_periods']}/{sp['periods']} | - |")
    lines.append(f"| One-way turnover (annualised) | {fmt_num(st.get('ann_turnover'), 2)} | - | - | "
                 f"{fmt_num(blk.get('random_mean_period_turnover', 0) * 12, 2)} |")
    lines.append("")
    lines.append("**Versus the 1,000 random portfolios (same size, same monthly rebalancing, same costs)**")
    lines.append("")
    lines.append(f"- t-stat vs random, gross basis: z = {fmt_num(tg['z_unpaired_mean'])} "
                 f"(strategy mean {fmt_pct(tg['random_mean'] + (tg['z_unpaired_mean'] * tg['random_std']))} per period "
                 f"vs random mean {fmt_pct(tg['random_mean'])} per period) -> "
                 f"{fmt_num(tg['percentile_of_random_means'], 1)}th percentile of the random cloud")
    lines.append(f"- t-stat vs random, net basis: z = {fmt_num(tn['z_unpaired_mean'])}, "
                 f"percentile {fmt_num(tn['percentile_of_random_means'], 1)}")
    lines.append(f"- paired t-stat of period returns vs the random mean path: "
                 f"{fmt_num(tg['t_paired_periods'])} (gross), {fmt_num(tn['t_paired_periods'])} (net)")
    lines.append(f"- paired t-stat vs SPY: {fmt_num(blk['tstat_vs_spy_paired_net'])} (net), "
                 f"{fmt_num(blk['tstat_vs_spy_paired_gross'])} (gross)")
    if bd:
        lines.append(f"- diagnostic OLS vs SPY: beta {fmt_num(bd.get('beta'))}, "
                     f"alpha {fmt_pct(bd.get('alpha_per_period'), 3)}/month "
                     f"(t {fmt_num(bd.get('t_alpha'))}), R^2 {fmt_num(bd.get('r_squared'))} "
                     f"| random cloud mean beta {fmt_num(blk.get('random_beta_mean'))}")
    lines.append("")
    return lines


def criteria(blk: dict) -> dict:
    """The frozen pass bar, evaluated criterion by criterion."""
    st = blk["strategy_net"]
    sp = blk["spy"]
    c1a = st["ann_return"] > sp["ann_return"]
    c1b = st["sharpe"] > sp["sharpe"]
    c2 = blk["tstat_vs_random_gross"]["z_unpaired_mean"] >= 2.0
    c3 = st["ann_return"] > blk["random_net_ann_return_p95"]
    return {
        "C1a_return_vs_spy": bool(c1a),
        "C1b_sharpe_vs_spy": bool(c1b),
        "C1_beats_spy": bool(c1a and c1b),
        "C2_gross_alpha_vs_random_z_ge_2": bool(c2),
        "C3_net_return_above_random_p95": bool(c3),
        "overall": bool(c1a and c1b and c2 and c3),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="_results.json")
    ap.add_argument("--sensitivity", default="_results_sensitivity.json")
    ap.add_argument("--out", default="RESULTS.md")
    args = ap.parse_args()

    data = json.loads((RESULTS / args.results).read_text())
    meta = data["meta"]
    lines = ["# STRAT-027 — Results (numbers)", ""]
    lines.append("Generated by `make_report.py` from the results JSON. Do not hand-edit numbers here.")
    lines.append("")
    lines.append("## Run configuration")
    lines.append("")
    for k in ["panel_min_date", "panel_max_date", "panel_symbols", "dev_start", "dev_end",
              "dev_periods", "holdout_start", "holdout_end", "holdout_periods", "n_names",
              "position_cap", "cost_bps_per_side", "n_random", "seed", "base_eligible_avg_dev",
              "base_eligible_avg_holdout", "fundamentals_available", "windows_run"]:
        if k in meta:
            lines.append(f"- `{k}`: {meta[k]}")
    lines.append("")

    lines.append("## Frozen pass bar, criterion by criterion")
    lines.append("")
    lines.append("| Factor | Window | C1a return > SPY | C1b Sharpe > SPY | C2 gross z vs random >= 2 | C3 net return > random p95 | OVERALL |")
    lines.append("|---|---|---|---|---|---|---|")
    for fname, blk in data["factors"].items():
        for w in ("dev", "holdout"):
            if w in blk:
                c = criteria(blk[w])
                lines.append(
                    f"| `{fname}` | {w} | {'PASS' if c['C1a_return_vs_spy'] else 'FAIL'} | "
                    f"{'PASS' if c['C1b_sharpe_vs_spy'] else 'FAIL'} | "
                    f"{'PASS' if c['C2_gross_alpha_vs_random_z_ge_2'] else 'FAIL'} | "
                    f"{'PASS' if c['C3_net_return_above_random_p95'] else 'FAIL'} | "
                    f"**{'PASS' if c['overall'] else 'FAIL'}** |")
    lines.append("")
    lines.append("C1 = beats SPY on net annualised return AND net Sharpe; "
                 "C2 = gross mean period return >= 2.0 SD above the mean of the 1,000 random portfolios; "
                 "C3 = net annualised return above the 95th percentile of the random cloud. "
                 "The dev bar is the decision rule; the holdout bar is applied to the dev-selected factor only.")
    lines.append("")

    for fname, blk in data["factors"].items():
        lines.append(f"## Factor `{fname}` — {blk['description']}")
        lines.append("")
        lines.append(f"Direction: {blk['direction']}. Factor-eligible universe: "
                     f"dev avg {blk.get('factor_eligible_avg_dev', 0):.0f} names "
                     f"(min {blk.get('factor_eligible_min_dev', 0)}), "
                     f"holdout avg {blk.get('factor_eligible_avg_holdout', 0):.0f} names.")
        lines.append("")
        if "dev" in blk:
            lines += block_table(blk["dev"], "Development 2021-01 -> 2024-12 (48 monthly periods)")
        if "holdout" in blk:
            lines += block_table(blk["holdout"], "Holdout 2025-01 -> 2026-09 (20 monthly periods, ONE SHOT)")
        lines.append("")

    sp = data.get("spy", {})
    if sp:
        lines.append("## SPY buy-and-hold benchmark")
        lines.append("")
        lines.append("| Window | Total return | Ann return | Ann vol | Sharpe | Max DD |")
        lines.append("|---|---|---|---|---|---|")
        for w in ("dev", "holdout"):
            if w in sp:
                m = sp[w]
                lines.append(f"| {w} | {fmt_pct(m['total_return'])} | {fmt_pct(m['ann_return'])} | "
                             f"{fmt_pct(m['ann_vol'])} | {fmt_num(m['sharpe'])} | {fmt_pct(m['max_drawdown'])} |")
        lines.append("")

    sens_path = RESULTS / args.sensitivity
    if sens_path.exists():
        sens = json.loads(sens_path.read_text())
        lines.append("## Declared sensitivity grid (portfolio size x cost) — reported, never used for selection")
        lines.append("")
        lines.append("| Factor | N | Cost (bps) | Window | Ann return | Sharpe | Max DD | Ann turnover | z vs random (gross) |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for key, blk in sens["grid"].items():
            fname, npart, cpart = key.split("|")
            n = npart.replace("N", "")
            c = cpart.replace("cost", "")
            for w in ("dev", "holdout"):
                if w in blk:
                    m = blk[w]["strategy_net"]
                    z = blk[w]["tstat_vs_random_gross"]["z_unpaired_mean"]
                    lines.append(f"| {fname} | {n} | {c} | {w} | {fmt_pct(m['ann_return'])} | "
                                 f"{fmt_num(m['sharpe'])} | {fmt_pct(m['max_drawdown'])} | "
                                 f"{fmt_num(m.get('ann_turnover'), 2)} | {fmt_num(z)} |")
        lines.append("")

    out = HERE / args.out
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
