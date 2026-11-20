"""PREREG SECTION 11 VERIFICATION (non-result artifact, round 2 of HOLDOUT-MOM-001).

Independent hand recomputation of 3 sample HOLDOUT months for MOM_12_1.

Scope (hard limits honoured here):
  * does NOT re-build the panel and does NOT re-run the evaluation;
  * does NOT modify any rule, cost, seed, parameter or file under
    scripts_holdout/results_holdout/;
  * reads the FROZEN panel parquet and the FROZEN results.json (result net is
    only READ, never recomputed by the engine here).

What is recomputed by plain arithmetic, from raw panel prices:
  entry/exit prices per name, leg returns, opened/closed/dead name counts,
  costs (0.15%/side), borrow (1%/yr, held_days/252), long leg net, short leg
  net, month net = (L_gross - L_cost) + (S_gross - S_borrow - S_cost).
The frozen MOM 12-1 ranking screen (prereg section 3 + the 12-1 formula) is
reused via engine.select_legs, because re-deriving the tie-break order is not
what section 11 asks for; every price/money step below is hand arithmetic.

Run:  python3 verify_prereg_section11.py   (from the worktree root)
Writes nothing; prints a table. Exit code 0 when all 3 months match.
"""
import sys, json
sys.path.insert(0, "research/splitscreen-001")
import numpy as np, pandas as pd
import engine as E, run_study as R

PANEL = "scripts_holdout/panel_holdout.parquet"
RES = "scripts_holdout/results_holdout/results.json"
MONTHS = ["2025-03", "2025-09", "2026-01"]
COST, BORROW, TDY = 0.0015, 0.01, 252.0

panel = E.load_panel(PANEL)
months = E.build_months(panel)
usable = [m for m in months if m.exit_date <= R.PANEL_LAST
          and E.month_end_index(panel, m.period - 13) >= 0]
md = E.compute_month_data(panel, usable)
keys = [m.key for m in usable]
hold = [i for i, m in enumerate(usable)
        if m.period >= R.HOLD_START and m.exit_date <= R.PANEL_LAST]
res = json.load(open(RES))["variants"]["MOM_12_1"]["monthly_holdout"]
stored = dict(zip(res["months"], res["net"]))

raw = pd.read_parquet(PANEL)
dates = np.sort(raw["date"].unique())
opens_on = {d: raw.loc[raw["date"] == d].set_index("symbol")["open"] for d in
            set(dates[usable[i].entry_idx] for i in hold)
            | set(dates[usable[i].exit_idx] for i in hold)}

def hand_leg(mi, syms, prev_syms, sign):
    """gross, cost, borrow for one leg, recomputed by arithmetic."""
    m = usable[mi]
    e, x = m.entry_idx, m.exit_idx
    o_e, o_x = opens_on[dates[e]], opens_on[dates[x]]
    cur, prev = set(syms), set(prev_syms)
    n = len(cur)
    if n == 0:
        return 0.0, 0.0, 0.0
    w = 1.0 / n
    seg = raw.loc[(raw["date"] >= dates[e]) & (raw["date"] <= dates[x])]
    dead = {s for s in cur if md[mi].last_close_idx[s] != md[mi].exit_idx}
    gross = borrow = 0.0
    for s in sorted(cur):
        sym = str(panel.symbols[s])
        p0 = float(o_e.loc[sym])
        p1 = o_x.get(sym, np.nan)
        if np.isfinite(p1) and p1 > 0:
            rr, days = float(p1) / p0 - 1.0, x - e
        else:                       # delisting: last finite close in window
            cl = seg.loc[seg["symbol"] == sym, "close"].to_numpy(dtype=float)
            cl = cl[np.isfinite(cl)]
            rr = float(cl[-1]) / p0 - 1.0 if cl.size else 0.0
            days = int(cl.size)
        # frozen sign convention: a short earns -return, i.e. the short leg
        # P&L summed below is -sum(w*ret) minus its borrow and cost.
        gross += w * rr * (1.0 if sign > 0 else -1.0)
        if sign < 0:
            borrow += BORROW * (days / TDY) * w
    cost = COST * (len(cur - prev) + len(prev - cur) + len(dead)) * w
    return gross, cost, borrow

print("PREREG-11 verification, MOM_12_1 (HOLDOUT-MOM-001 round 2)")
print(f"panel      : {PANEL}")
print(f"engine net : read from {RES} (frozen result, not re-simulated)")
print(f"costs      : {COST:.4%}/side, borrow {BORROW:.2%}/yr on {TDY:.0f} days; net = (L_gross-L_cost)+(S_gross-S_borrow-S_cost)")
print()
hdr = (f"{'month':8s} {'entry':10s} {'exit':10s} {'nL':>4s} {'nS':>4s} "
       f"{'L_gross':>9s} {'L_cost':>8s} {'S_gross':>9s} {'S_borrow':>9s} "
       f"{'S_cost':>8s} {'hand_net':>10s} {'engine':>10s} {'diff':>9s}")
print(hdr)
worst = 0.0
for k in MONTHS:
    mi = [i for i in hold if keys[i] == k][0]
    lg = [int(s) for s in E.select_legs(md[mi], "MOM")[0]]
    sh = [int(s) for s in E.select_legs(md[mi], "MOM")[1]]
    lg_p = [int(s) for s in E.select_legs(md[mi - 1], "MOM")[0]]
    sh_p = [int(s) for s in E.select_legs(md[mi - 1], "MOM")[1]]
    Lg, Lc, _ = hand_leg(mi, lg, lg_p, +1)
    Sg, Sc, Sb = hand_leg(mi, sh, sh_p, -1)
    net = (Lg - Lc) + (Sg - Sb - Sc)   # Sg already carries the short sign
    diff = net - float(stored[k]); worst = max(worst, abs(diff))
    print(f"{k:8s} {str(pd.Timestamp(dates[usable[mi].entry_idx]).date()):10s} "
          f"{str(pd.Timestamp(dates[usable[mi].exit_idx]).date()):10s} "
          f"{len(lg):4d} {len(sh):4d} {Lg:9.6f} {Lc:8.6f} {Sg:9.6f} {Sb:9.6f} "
          f"{Sc:8.6f} {net:10.6f} {float(stored[k]):10.6f} {diff:9.2e}")
print()
print(f"max |hand - engine| = {worst:.2e}")
print("RESULT:", "MATCH (3/3 months reproduce the engine)" if worst < 1e-12
      else "MISMATCH")
sys.exit(0 if worst < 1e-12 else 1)
