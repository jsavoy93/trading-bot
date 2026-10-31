"""STRAT-025 setup research — SUPPLEMENTARY sensitivity run (post-hoc, labeled).

Rationale: STRAT-025 Part 1 established that the running bot's actual watchlist is the
key set of `analysis_status.json` (200 symbols), 191 of which are outside the frozen
prereg §3 universe. The pre-registered run used the prereg universe only.

This script re-runs the DEV phase for the setups that came CLOSEST to the dev bar
(with their frozen selection rule) on the UNION universe (prereg universe + the 191 extra
watchlist names) to test whether the extra micro-cap names change any dev verdict.
It is explicitly POST-HOC and supplementary: it cannot promote a setup into a PASS and
cannot trigger a holdout run (the holdout remains untouched unless the frozen dev run
passed).

Usage: python3 run_extra_sensitivity.py [setup ...]
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

import engine as E
import setups as S
import run_setup as R

HERE = Path(__file__).resolve().parent
EXTRA = HERE / "data_watchlist_extra"
DEV = R.DEV


def load_union():
    frames = {}
    for d in (R.DATA, EXTRA):
        for f in sorted(d.glob("*.csv")):
            if f.stem in frames:
                continue
            df = E.load_symbol(f)
            if df is not None:
                frames[f.stem] = df
    return frames


def main(setup_names):
    frames = load_union()
    print(f"union symbols: {len(frames)}")
    sig = R.build_signals(frames)
    for setup in setup_names:
        res = R.run_setup_period(frames, sig, setup, DEV, seed_offset=4242)
        sel = R.select_and_bar_dev(res)
        (R.RESULTS / f"{setup}_dev_union_supplement.json").write_text(
            json.dumps({"result": res, "selection": sel}, indent=1, default=str))
        print(f"{setup}: selected={sel.get('selected')} dev_pass={sel['dev_pass']} ({sel.get('reason')})")


if __name__ == "__main__":
    args = sys.argv[1:] or ["rsi2_uptrend_v2", "gap_fill_fade", "rs_leaders_126"]
    main(args)
