"""STRAT-025 setup research — dev tuning + one-shot holdout runner.

Usage:
  python3 run_setup.py dev        # all 9 setups, dev window, frozen selection + dev bar
  python3 run_setup.py holdout    # ONLY dev-passing setups' selected configs, run once
Writes results/<setup>_<period>.json (+ summary CSV).
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

import engine as E
import setups as S

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
SEED = 20261031
N_DRAWS = 1000
MIN_TRADES = 100          # PREREG §9
T_BAR = 2.0               # PREREG §10 (STRAT-022 §10 evidence bar)
EDGE_OVER_BASE = 0.0010   # 10 bps over baseline mean

DEV = (pd.Timestamp("2021-01-01"), pd.Timestamp("2024-12-31"))
HOLD = (pd.Timestamp("2025-01-01"), pd.Timestamp("2026-10-28"))

# setup -> grid of (label, atr_mult, time_stop); rsw handled for rs_leaders
GRIDS = {
    "ema20_pullback":  [("g1", 2.5, 10), ("g2", 2.5, 20), ("g3", 3.0, 10), ("g4", 3.0, 20)],
    "high20_breakout_vol": [("g1", 2.5, 10), ("g2", 2.5, 20), ("g3", 3.0, 10), ("g4", 3.0, 20)],
    "vcp_minervini":   [("g1", 2.5, 10), ("g2", 2.5, 20), ("g3", 3.0, 10), ("g4", 3.0, 20)],
    "gap_and_go":      [("g1", 2.5, 5), ("g2", 2.5, 10), ("g3", 3.0, 5), ("g4", 3.0, 10)],
    "gap_fill_fade":   [("g1", 2.5, 5), ("g2", 2.5, 10), ("g3", 3.0, 5), ("g4", 3.0, 10)],
    "rs_leaders_63":   [("g1", 3.0, 20)],
    "rs_leaders_126":  [("g1", 3.0, 20)],
    "inside_day":      [("g1", 2.5, 5), ("g2", 2.5, 10), ("g3", 3.0, 5), ("g4", 3.0, 10)],
    "nr7":             [("g1", 2.5, 5), ("g2", 2.5, 10), ("g3", 3.0, 5), ("g4", 3.0, 10)],
    "rsi2_uptrend_v2": [("g1", 2.5, 5), ("g2", 2.5, 10), ("g3", 3.0, 5), ("g4", 3.0, 10)],
}
SETUPS = list(GRIDS.keys())


def load_all():
    frames = {}
    for f in sorted(DATA.glob("*.csv")):
        df = E.load_symbol(f)
        if df is not None:
            frames[f.stem] = df
    return frames


def period_mask(df, win):
    return (df["date"] >= win[0]) & (df["date"] <= win[1])


def build_signals(frames):
    """Returns {setup: {sym: sorted signal index list}} for the non-cross-sectional setups."""
    sig = {s: {} for s in SETUPS}
    for sym, df in frames.items():
        sig["ema20_pullback"][sym] = df.index[S.ema20_pullback(df)].tolist()
        sig["high20_breakout_vol"][sym] = df.index[S.high20_breakout_vol(df)].tolist()
        sig["vcp_minervini"][sym] = df.index[S.vcp_minervini(df)].tolist()
        sig["gap_and_go"][sym] = df.index[S.gap_and_go(df)].tolist()
        sig["gap_fill_fade"][sym] = df.index[S.gap_fill_fade(df)].tolist()
        sig["inside_day"][sym] = df.index[S.inside_day(df)].tolist()
        sig["nr7"][sym] = df.index[S.nr7(df)].tolist()
        sig["rsi2_uptrend_v2"][sym] = df.index[S.rsi2_uptrend_v2(df)].tolist()
    # cross-sectional RS leaders (top-decile entry events vs SPY)
    spy = frames.get("SPY")
    if spy is None:
        raise SystemExit("SPY missing — cannot build RS setups")
    spy_c = spy.set_index("date")["close"]
    for label, rsw in (("rs_leaders_63", 63), ("rs_leaders_126", 126)):
        rs_vals, elig = {}, {}
        for sym, df in frames.items():
            if sym == "SPY":
                continue
            rs = S.rs_line(df, spy_c, rsw)
            rs_vals[sym] = rs
            elig[sym] = E.liq_ok(df) & df["sma200"].notna() & df["atr14"].notna() & rs.notna()
        by_date = {}
        for sym, rs in rs_vals.items():
            df = frames[sym]
            m = elig[sym]
            for t in df.index[m]:
                by_date.setdefault(df["date"].iloc[t], []).append((rs.iloc[t], sym))
        top_syms_today, in_dec = {}, {s: set() for s in rs_vals}
        events = {s: [] for s in rs_vals}
        for d in sorted(by_date):
            lst = sorted(by_date[d], reverse=True)
            k = max(1, len(lst) // 10)
            todays = {sym for _, sym in lst[:k]}
            for sym in todays:
                df = frames[sym]
                idx = df.index[df["date"] == d]
                if len(idx) and idx[0] not in in_dec[sym] and sym not in ("SPY",):
                    events[sym].append(int(idx[0]))
                in_dec[sym].add(idx[0] if len(idx) else -1)
        sig[label] = {s: sorted(v) for s, v in events.items()}
    return sig


def eligible_days(df, win, exclude: set[int]) -> list[int]:
    m = E.liq_ok(df) & df["sma200"].notna() & df["atr14"].notna() & period_mask(df, win)
    return [int(i) for i in df.index[m] if int(i) not in exclude]


def run_setup_period(frames, sig, setup, win, only_cfg=None, seed_offset=0):
    """Runs all grid configs of one setup for one window. Returns results dict."""
    res = {"setup": setup, "window": [str(win[0].date()), str(win[1].date())], "configs": {}}
    for label, atr_m, tstop in GRIDS[setup]:
        if only_cfg and label != only_cfg:
            continue
        days_by_sym = {}
        for sym, ts in sig[setup].items():
            df = frames[sym]
            days_by_sym[sym] = [t for t in ts if win[0] <= df["date"].iloc[t] <= win[1]]
        # strategy trades
        strat = []
        for sym, days in days_by_sym.items():
            if not days:
                continue
            hyp = E.hypothetical_trades(frames[sym], np.array(days), setup, atr_m, tstop)
            strat.extend(E.anti_overlap(hyp, days))
        strat_m = E.metrics(strat)
        # baseline: eligible days excluding this config's signal days; hyp for ALL eligible
        rng = np.random.default_rng(SEED + seed_offset + hash((setup, label)) % 100000)
        elig_by_sym, hyp_cache, k_by_sym = {}, {}, {}
        for sym, days in days_by_sym.items():
            df = frames[sym]
            elig = eligible_days(df, win, set(days))
            if not elig and not days:
                continue
            elig_by_sym[sym] = elig
            hyp_cache[sym] = E.hypothetical_trades(df, np.array(elig), setup, atr_m, tstop)
            k_by_sym[sym] = len(days)
        base = E.baseline_draws(hyp_cache, elig_by_sym, k_by_sym, rng, N_DRAWS)
        res["configs"][label] = {
            "atr_mult": atr_m, "time_stop": tstop,
            "strat": strat_m, "baseline": base,
        }
        print(f"  {setup}/{label}: n={strat_m['n']} mean={None if strat_m['mean_net'] is None else round(strat_m['mean_net']*100,3)}% "
              f"t={None if strat_m['t'] is None else round(strat_m['t'],2)} "
              f"base={None if base['mean_of_draw_means'] is None else round(base['mean_of_draw_means']*100,3)}%")
    return res


def select_and_bar_dev(res: dict) -> dict:
    """PREREG §9 selection + §10 dev bar for one setup's dev results."""
    cands = [(lbl, c) for lbl, c in res["configs"].items() if c["strat"]["n"] >= MIN_TRADES]
    if not cands:
        return {"selected": None, "dev_pass": False, "reason": "insufficient trades (<100)"}
    lbl, cfg = max(cands, key=lambda kv: kv[1]["strat"]["sharpe"] or -9e9)
    st, bs = cfg["strat"], cfg["baseline"]
    ok_t = st["mean_net"] > 0 and st["t"] is not None and st["t"] >= T_BAR
    ok_edge = bs["mean_of_draw_means"] is not None and \
        st["mean_net"] - bs["mean_of_draw_means"] >= EDGE_OVER_BASE
    return {"selected": lbl, "params": {k: cfg[k] for k in ("atr_mult", "time_stop")},
            "dev_pass": bool(ok_t and ok_edge),
            "t": st["t"], "mean_net": st["mean_net"],
            "baseline_mean": bs["mean_of_draw_means"],
            "reason": f"t_bar={ok_t} edge_bar={ok_edge}"}


def main(period: str):
    assert period in ("dev", "holdout")
    frames = load_all()
    print(f"loaded {len(frames)} symbols")
    sig = build_signals(frames)
    out_all = {}
    for setup in SETUPS:
        if period == "dev":
            res = run_setup_period(frames, sig, setup, DEV)
            (RESULTS / f"{setup}_dev.json").write_text(json.dumps(res, indent=1, default=str))
            sel = select_and_bar_dev(res)
            print(f"{setup}: selected={sel.get('selected')} dev_pass={sel['dev_pass']} ({sel.get('reason')})")
            out_all[setup] = {"selection": sel}
        else:  # holdout: only dev passers, their selected config, run ONCE
            devres_p = RESULTS / f"{setup}_dev.json"
            selection_p = RESULTS / "_dev_selection.json"
            selmap = json.loads(selection_p.read_text())
            sel = selmap.get(setup, {})
            if not sel.get("dev_pass"):
                print(f"{setup}: skip holdout (did not pass dev)")
                continue
            lbl = sel["selected"]
            res = run_setup_period(frames, sig, setup, HOLD, only_cfg=lbl, seed_offset=777)
            cfg = res["configs"][lbl]
            st, bs = cfg["strat"], cfg["baseline"]
            hp = st["mean_net"] is not None and bs["mean_of_draw_means"] is not None and \
                st["mean_net"] > bs["mean_of_draw_means"] and st["t"] is not None and st["t"] > 0
            res["holdout_pass"] = bool(hp)
            (RESULTS / f"{setup}_holdout.json").write_text(json.dumps(res, indent=1, default=str))
            print(f"{setup}: holdout_pass={hp}")
            out_all[setup] = {"holdout_pass": hp}
    if period == "dev":
        (RESULTS / "_dev_selection.json").write_text(json.dumps(out_all, indent=1, default=str))
    print("DONE")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dev")
