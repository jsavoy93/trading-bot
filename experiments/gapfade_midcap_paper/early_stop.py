"""GAPFADE-MIDCAP-PAPER — early-stop FLAG (paper-trial configuration only).

This module decides whether the paper trial SHOULD stop, and records that
decision. It is deliberately powerless:

  * it only WRITES a JSON flag record into this experiment's own
    state.json (StateStore.add_early_stop) — never an order, never a
    kill-switch file, never a signal, never a process action;
  * it never touches another bot: its only inputs are this experiment's
    own paper-trial trades and its own threshold constants, and its only
    output is True/False plus a record under this experiment's state dir;
  * nothing here installs, enables, starts, stops or restarts a unit, and
    the runner's systemd units stay disabled. A human reads the flag and
    decides.

Rule (frozen for this trial): after at least EARLY_STOP_MIN_REAL_FILLS
real fills (default 150), the trial is flagged when its cumulative net is
below either shadow's cumulative net (edge vs vol-matched < 0, the
primary signal, or edge vs random-clean < 0). Once tripped on a day the
flag is recorded, so a later report can show exactly when and why.

CLI (read-only unless --record):
  python experiments/gapfade_midcap_paper/early_stop.py            # dry
  python experiments/gapfade_midcap_paper/early_stop.py --record   # flag
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):        # allow `python .../early_stop.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.gapfade_midcap_paper import report_pnl, rules  # noqa: E402
from experiments.gapfade_midcap_paper import state as state_mod  # noqa: E402

TRIAL = "gapfade_midcap_paper"
# after ~150 real fills the edge is judged; below this it is noise
EARLY_STOP_MIN_REAL_FILLS = 150
# a tripped flag is only meaningful once this many shadow fills exist too
EARLY_STOP_MIN_SHADOW_FILLS = 100
COST_BPS_PER_SIDE = rules.DEFAULT_COST_BPS_PER_SIDE


def evaluate(state, cost_bps: float = COST_BPS_PER_SIDE) -> dict:
    """Return the early-stop decision for THIS trial (side-effect free)."""
    report = report_pnl.build_report(state, cost_bps)
    groups = report["groups"]
    real_filled = groups["real"]["filled"]
    shadow_filled = min(groups["random_clean"]["filled"],
                        groups["vol_matched"]["filled"])
    edge_vol = report["edge_vs_vol_matched"]
    edge_rnd = report["edge_vs_random_clean"]
    enough = (real_filled >= EARLY_STOP_MIN_REAL_FILLS
              and shadow_filled >= EARLY_STOP_MIN_SHADOW_FILLS)
    reasons = []
    if enough and edge_vol is not None and edge_vol < 0:
        reasons.append("edge_vs_vol_matched_negative")
    if enough and edge_rnd is not None and edge_rnd < 0:
        reasons.append("edge_vs_random_clean_negative")
    return {
        "trial": TRIAL,
        "status": "tripped" if reasons else "running",
        "real_filled": real_filled,
        "shadow_filled": shadow_filled,
        "min_real_fills": EARLY_STOP_MIN_REAL_FILLS,
        "min_shadow_fills": EARLY_STOP_MIN_SHADOW_FILLS,
        "edge_vs_vol_matched": edge_vol,
        "edge_vs_random_clean": edge_rnd,
        "cost_bps_per_side": cost_bps,
        "reasons": reasons,
        "action": "none (flag only: a human decides; units stay disabled)",
    }


def record_if_tripped(state, decision: dict) -> dict | None:
    """Write ONE flag record to this trial's state; never acts on it."""
    if decision["status"] != "tripped":
        return None
    for prev in state.early_stops():
        if prev.get("trial") == decision["trial"]:
            return prev                      # already recorded
    rec = dict(decision)
    rec["flagged_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    state.add_early_stop(rec)
    state.save()
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="GAPFADE midcap paper-trial early-stop flag "
                    "(configuration only; never stops anything).")
    ap.add_argument("--state-dir", default=None,
                    help="paper-trial state dir (default "
                         "/root/trading-data/gapfade-midcap-paper)")
    ap.add_argument("--cost-bps", type=float, default=COST_BPS_PER_SIDE)
    ap.add_argument("--record", action="store_true",
                    help="record a tripped flag in THIS trial's state.json "
                         "(default: print only, change nothing)")
    args = ap.parse_args(argv)
    st = state_mod.StateStore(args.state_dir)
    st.load()
    decision = evaluate(st, args.cost_bps)
    if args.record:
        rec = record_if_tripped(st, decision)
        if rec:
            decision = {**decision, "recorded": True,
                        "flagged_at": rec.get("flagged_at")}
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0 if decision["status"] != "tripped" else 1


if __name__ == "__main__":
    sys.exit(main())
