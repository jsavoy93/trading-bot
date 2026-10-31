"""STRAT-025 setup research — build the summary CSV/table from results/*.json.

Reads the dev selection (results/_dev_selection.json) and the per-setup dev JSONs and
the optional holdout JSONs (present only for setups that passed dev), and writes:
  results/summary.csv        — one row per setup variant
  results/summary.json       — same content as JSON
"""
from __future__ import annotations
import csv, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

ORDER = ["ema20_pullback", "high20_breakout_vol", "vcp_minervini", "gap_and_go",
         "gap_fill_fade", "rs_leaders_63", "rs_leaders_126", "inside_day", "nr7",
         "rsi2_uptrend_v2"]
LABEL = {
    "ema20_pullback": "(a) EMA20 pullback in uptrend",
    "high20_breakout_vol": "(b) 20d-high breakout on 1.5x volume",
    "vcp_minervini": "(c) Minervini VCP",
    "gap_and_go": "(d1) Gap-and-go",
    "gap_fill_fade": "(d2) Gap-fill fade",
    "rs_leaders_63": "(e) RS leaders vs SPY (3m)",
    "rs_leaders_126": "(e) RS leaders vs SPY (6m)",
    "inside_day": "(f1) Inside day",
    "nr7": "(f2) NR7",
    "rsi2_uptrend_v2": "(g) RSI(2) uptrend, distinct exit",
}


def main():
    sel = json.loads((RESULTS / "_dev_selection.json").read_text())
    rows = []
    for setup in ORDER:
        dev = json.loads((RESULTS / f"{setup}_dev.json").read_text())
        s = sel[setup]["selection"]
        lbl = s["selected"]
        cfg = dev["configs"][lbl]
        st, bs = cfg["strat"], cfg["baseline"]
        hp_path = RESULTS / f"{setup}_holdout.json"
        hold = json.loads(hp_path.read_text()) if hp_path.exists() else None
        row = {
            "setup": setup,
            "label": LABEL[setup],
            "selected_config": lbl,
            "atr_mult": cfg["atr_mult"],
            "time_stop": cfg["time_stop"],
            "dev_trades": st["n"],
            "dev_mean_net_pct": round(st["mean_net"] * 100, 4) if st["mean_net"] is not None else None,
            "dev_t": round(st["t"], 3) if st["t"] is not None else None,
            "dev_win_rate_pct": round(st["win_rate"] * 100, 2) if st["win_rate"] is not None else None,
            "dev_sharpe": round(st["sharpe"], 4) if st["sharpe"] is not None else None,
            "baseline_mean_net_pct": round(bs["mean_of_draw_means"] * 100, 4) if bs["mean_of_draw_means"] is not None else None,
            "baseline_p5_pct": round(bs["p5_draw_mean"] * 100, 4) if bs["p5_draw_mean"] is not None else None,
            "baseline_p95_pct": round(bs["p95_draw_mean"] * 100, 4) if bs["p95_draw_mean"] is not None else None,
            "baseline_draws": bs["n_draws"],
            "edge_vs_base_bps": round((st["mean_net"] - bs["mean_of_draw_means"]) * 10000, 1) if bs["mean_of_draw_means"] is not None else None,
            "dev_pass": s["dev_pass"],
            "dev_reason": s.get("reason"),
            "holdout_run": bool(hold),
            "holdout_trades": None,
            "holdout_mean_net_pct": None,
            "holdout_t": None,
            "holdout_baseline_mean_net_pct": None,
            "holdout_pass": None,
        }
        if hold:
            hc = hold["configs"][lbl]
            hs, hb = hc["strat"], hc["baseline"]
            row.update({
                "holdout_trades": hs["n"],
                "holdout_mean_net_pct": round(hs["mean_net"] * 100, 4) if hs["mean_net"] is not None else None,
                "holdout_t": round(hs["t"], 3) if hs["t"] is not None else None,
                "holdout_baseline_mean_net_pct": round(hb["mean_of_draw_means"] * 100, 4) if hb["mean_of_draw_means"] is not None else None,
                "holdout_pass": hold.get("holdout_pass"),
            })
        rows.append(row)

    with open(RESULTS / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (RESULTS / "summary.json").write_text(json.dumps(rows, indent=1))
    print(f"wrote summary for {len(rows)} setup variants; "
          f"dev_pass={sum(1 for r in rows if r['dev_pass'])}; "
          f"holdout_run={sum(1 for r in rows if r['holdout_run'])}")


if __name__ == "__main__":
    main()
