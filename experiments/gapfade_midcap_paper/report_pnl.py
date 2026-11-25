"""GAPFADE-MIDCAP-PAPER — real vs shadow PnL report (paper-trial records only).

Usage (from the repo root):
  python experiments/gapfade_midcap_paper/report_pnl.py            # text
  python experiments/gapfade_midcap_paper/report_pnl.py --json     # JSON
  python experiments/gapfade_midcap_paper/report_pnl.py --cost-bps 10
      # --cost-bps is PER SIDE, default 27.0 (the research used 10)
  python experiments/gapfade_midcap_paper/report_pnl.py --state-dir DIR

Sources: ONLY this experiment's own paper-trial records —
  state.json written by StateStore under /root/trading-data/
  gapfade-midcap-paper/ (override with --state-dir or
  GAPFADE_MIDCAP_PAPER_STATE_DIR):
    * closed[]          real realised trades
    * shadows[]         shadow trades (kind random_clean / vol_matched)
    * intents[]         real sell fills, for realised proceeds
Never the live trading_bot.db; this script opens no database at all.

Money model (identical for real trades and both shadows, so the edge is
apples to apples):
  gross  = (exit_price - entry_price) * qty
  cost   = --cost-bps on BOTH entry and exit notional
           (rules.round_trip_cost)
  net    = gross - cost
A trade with no recorded entry or exit price is reported as `open` and is
excluded from every average (never silently counted as zero).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):        # allow `python .../report_pnl.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.gapfade_midcap_paper import rules  # noqa: E402
from experiments.gapfade_midcap_paper import state as state_mod  # noqa: E402

GROUPS = ("real", "random_clean", "vol_matched")
EXIT_UNKNOWN = 0


def _num(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trades(state) -> list[dict]:
    """Every real/shadow realised trade with its entry/exit prices."""
    trades: list[dict] = []
    for rec in state.data.get("closed") or []:
        entry = _num(rec.get("entry_price"))
        exit_ = _num(rec.get("exit_price"))
        qty = _num(rec.get("qty")) or 0.0
        trades.append({
            "group": "real", "symbol": rec.get("symbol"),
            "entry_day": rec.get("entry_day"), "exit_day": rec.get("exit_day"),
            "entry_price": entry, "exit_price": exit_, "qty": qty,
            "exit_reason": rec.get("exit_reason"),
            "filled": entry is not None and exit_ is not None and qty > 0})

    for sh in state.data.get("shadows") or []:
        entry = _num(sh.get("entry_price"))
        exit_ = _num(sh.get("exit_price"))
        qty = _num(sh.get("qty")) or 0.0
        done = (sh.get("status") == "closed") or exit_ is not None
        trades.append({
            "group": sh.get("kind"), "symbol": sh.get("symbol"),
            "real_symbol": sh.get("real_symbol"),
            "entry_day": sh.get("entry_day"), "exit_day": sh.get("exit_day"),
            "entry_price": entry, "exit_price": exit_, "qty": qty,
            "exit_reason": sh.get("exit_reason"),
            "filled": bool(done and entry is not None and exit_ is not None
                           and qty > 0)})
    return trades


def summarise(trades: list[dict], cost_bps: float) -> dict[str, dict]:
    """Per-group trade count, win rate, mean net/trade and cumulative edge."""
    out: dict[str, dict] = {}
    for group in GROUPS:
        rows = [t for t in trades if t["group"] == group]
        done = [t for t in rows if t["filled"]]
        nets = []
        for t in done:
            gross = (t["exit_price"] - t["entry_price"]) * t["qty"]
            notional = t["entry_price"] * t["qty"]
            nets.append(gross - rules.round_trip_cost(notional, cost_bps))
        n = len(nets)
        wins = sum(1 for x in nets if x > 0)
        cum = round(sum(nets), 4)
        out[group] = {
            "trades": len(rows),
            "filled": n,
            "open": len(rows) - n,
            "wins": wins,
            "win_rate": round(wins / n, 4) if n else None,
            "mean_net_per_trade": round(sum(nets) / n, 4) if n else None,
            "cumulative_net": cum,
            "cost_bps_per_side": cost_bps,
        }
    real = out["real"]["cumulative_net"]
    out["edge_vs_random_clean"] = round(real - out["random_clean"]
                                        ["cumulative_net"], 4)
    out["edge_vs_vol_matched"] = round(real - out["vol_matched"]
                                       ["cumulative_net"], 4)
    return out


def _fmt(value, places: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.{places}f}"
    return str(value)


def render_text(summary: dict, cost_bps: float) -> str:
    lines = [f"GAPFADE-MIDCAP-PAPER PnL report "
             f"(cost {cost_bps:g} bps/side, both sides)",
             f"{'group':<14}{'trades':>7}{'closed':>7}{'open':>6}"
             f"{'win%':>8}{'mean net':>12}{'cum net':>14}"]
    for group in GROUPS:
        s = summary[group]
        lines.append(f"{group:<14}{s['trades']:>7}{s['filled']:>7}"
                     f"{s['open']:>6}{_fmt(s['win_rate']):>8}"
                     f"{_fmt(s['mean_net_per_trade']):>12}"
                     f"{_fmt(s['cumulative_net']):>14}")
    lines.append(f"edge vs random_clean  (real - random_clean): "
                 f"{_fmt(summary['edge_vs_random_clean'])}")
    lines.append(f"edge vs vol_matched   (real - vol_matched):  "
                 f"{_fmt(summary['edge_vs_vol_matched'])}")
    return "\n".join(lines)


def build_report(state, cost_bps: float) -> dict:
    trades = _trades(state)
    summary = summarise(trades, cost_bps)
    return {"pairing": "real_vs_shadows", "cost_bps_per_side": cost_bps,
            "groups": {g: summary[g] for g in GROUPS},
            "edge_vs_random_clean": summary["edge_vs_random_clean"],
            "edge_vs_vol_matched": summary["edge_vs_vol_matched"],
            "trades": [{k: t[k] for k in ("group", "symbol", "entry_day",
                                          "exit_day", "exit_price", "qty",
                                          "exit_reason", "filled")}
                       for t in trades]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Real vs shadow PnL for the GAPFADE midcap paper trial "
                    "(reads only this experiment's own paper-trial state).")
    ap.add_argument("--state-dir", default=None,
                    help="paper-trial state dir (default "
                         "/root/trading-data/gapfade-midcap-paper)")
    ap.add_argument("--cost-bps", type=float,
                    default=rules.DEFAULT_COST_BPS_PER_SIDE,
                    help="cost in bps PER SIDE (default 27)")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    args = ap.parse_args(argv)
    if args.cost_bps < 0:
        print("report_pnl: --cost-bps must be non-negative", file=sys.stderr)
        return EXIT_UNKNOWN
    store = state_mod.StateStore(args.state_dir)
    store.load()
    report = build_report(store, args.cost_bps)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text({**report["groups"],
                           "edge_vs_random_clean":
                               report["edge_vs_random_clean"],
                           "edge_vs_vol_matched":
                               report["edge_vs_vol_matched"]},
                          args.cost_bps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
