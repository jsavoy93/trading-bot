"""SECFILE-001: run V1/V2 once on DEV. Mechanics frozen in PREREGISTRATION.md (commit 72e9333)."""
import json, hashlib, time
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path("/root/.openclaw/worktrees/SECFILE-001")
import sys
sys.path.insert(0, str(HERE / "scripts_holdout")); sys.path.insert(0, "/root/research/splitscreen-002/scripts")
import engine_fixed as E
import run_study as R
from scipy import stats
RES = Path("/root/research/secfile-001"); DATA = RES / "data"
t0 = time.time()
PANEL = Path("/root/research/holdout-mom-001/scripts_holdout/panel_holdout.parquet")
sha = hashlib.sha256(PANEL.read_bytes()).hexdigest()
assert sha.startswith("4387206b") and sha.endswith("c071"), sha
panel = E.load_panel(PANEL); months = E.build_months(panel); mds = E.compute_month_data(panel, months)
DEV_START, DEV_END = pd.Period("2021-01", "M"), pd.Period("2024-12", "M")
rows = [i for i, m in enumerate(months) if DEV_START <= m.period <= DEV_END and m.exit_date <= pd.Timestamp("2024-12-31")]
md_rows = [mds[i] for i in rows]; keys = [months[i].key for i in rows]
dec = [months[i].decision_date for i in rows]
assert max(dec) < pd.Timestamp("2025-01-01"), "lookahead window"
syms = list(panel.symbols)
legs = [E.select_legs(md, "MOM") for md in md_rows]
legs_long = [lg for lg, sh, el in legs]; legs_short = [sh for lg, sh, el in legs]
elig = [np.flatnonzero(el) for lg, sh, el in legs]; nleg = [len(lg) for lg in legs_long]
idx = json.load(open(RES / "results_index.json"))
SEED2 = 20261121
draws_mom = R.random_monthly_nets(md_rows, elig, nleg, E.N_DRAWS, E.SEED)
rand = draws_mom.mean(axis=0)
def metrics(net): return R.window_metrics(net, net - rand, rand, [0]*len(net), [0]*len(net), draws_mom.mean(axis=1))
def simulate(L, S):
    E.compute_month_data.FALLBACK_STATS.clear()
    E.compute_month_data.FALLBACK_STATS.update({"held_legs": 0, "fallback_last_real_close": 0})
    return E.net_series(E.simulate_leg(md_rows, L, "long"), E.simulate_leg(md_rows, S, "short"))
def filtered_legs(removed_names):
    L, S, nrem = [], [], []
    for i, names in enumerate(removed_names):
        lg, sh = legs_long[i], legs_short[i]
        names = set(names)
        drop = {s for s in lg if syms[s] in names}
        keep = np.array([s for s in lg if s not in drop], dtype=int)
        if len(keep) == 0:
            keep = lg; nrem.append(0)
        else:
            nrem.append(len(lg) - len(keep))
        L.append(keep); S.append(sh)
    return L, S, np.array(nrem)
def random_filter_nets(nrem, n_draws=1000):
    out = np.zeros((n_draws, len(md_rows)))
    for d in range(n_draws):
        Ls = []
        for r in range(len(md_rows)):
            base = legs_long[r]; k = int(nrem[r])
            if k <= 0 or len(base) - k < 1: Ls.append(base); continue
            rng = np.random.default_rng(SEED2 + 1000*d + r)
            Ls.append(np.sort(rng.choice(base, size=len(base) - k, replace=False)))
        out[d] = simulate(Ls, legs_short)
    return out
out = {"panel_sha256": sha, "keys": keys, "prereg": "72e9333", "index": idx}
net0 = simulate(legs_long, legs_short); m0 = metrics(net0)
ref = {k: float(v) for k, v in m0.items() if isinstance(v, (int, float)) or k == "n_months"}
ref["monthly"] = dict(zip(keys, map(float, net0)))
out["unfiltered"] = ref
print("unfiltered mean", round(m0["mean_monthly_net"]*100, 3), "t", round(m0["t_stat_excess"], 3),
      "maxDD", round(m0["max_drawdown"]*100, 2), flush=True)
# descriptive: removed vs kept next-month return (equal-weight, before costs)
desc = {}
for name in ("V1", "V2"):
    rm = idx["removed_lists"][name]; assert any(set(x) & {syms[s] for s in legs_long[i]} for i, x in enumerate(rm)); r_rets, k_rets = [], []
    for i, names in enumerate(rm):
        md = md_rows[i]; alive = md.last_close_idx == md.exit_idx
        rs = set(names)
        for s in legs_long[i]:
            if not alive[s]: continue
            (r_rets if syms[s] in rs else k_rets).append(float(md.ret[s]))
    desc[name] = {"removed_mean_ret": float(np.mean(r_rets)) if r_rets else None, "n_removed_obs": len(r_rets),
                  "kept_mean_ret": float(np.mean(k_rets)), "n_kept_obs": len(k_rets)}
    print(name, "desc", desc[name], flush=True)
out["descriptive"] = desc
for name in ("V1", "V2"):
    L, S, nrem = filtered_legs(idx["removed_lists"][name])
    net = simulate(L, S); m = metrics(net); dd = net - net0
    td = float(np.mean(dd) / (np.std(dd, ddof=1) / np.sqrt(len(dd))))
    rf = random_filter_nets(nrem)
    pct_rf = E.percentile_vs_random(float(net.mean()), rf.mean(axis=1))
    bars = R.dev_bars(m)
    r = {k: float(v) for k, v in m.items() if isinstance(v, (int, float))}
    r.update({"n_months": m["n_months"], "bar_a": bars,
              "bar_b": {"mean_improvement": float(dd.mean()), "t_improvement": td,
                        "months_improved": int((dd > 0).sum()), "pass": bool(dd.mean() > 0 and td >= 2.24)},
              "bar_c": {"percentile_vs_random_filters": pct_rf, "pass": bool(pct_rf >= 98.75),
                        "draw_mean_pct": float(np.mean(pct_rf))},
              "n_removed": nrem.tolist(), "avg_removed": float(nrem.mean()),
              "monthly": dict(zip(keys, map(float, net))),
              "top3_worst": sorted(zip(keys, map(float, net)), key=lambda x: x[1])[:3]})
    r["PASS_ALL"] = bool(bars["ALL_PASS"] and r["bar_b"]["pass"] and r["bar_c"]["pass"])
    out[name] = r
    print(name, "A", bars["ALL_PASS"], "mean", round(m["mean_monthly_net"]*100, 3), "t", round(m["t_stat_excess"], 3),
          "pct", round(m["percentile_vs_random"], 2), "maxDD", round(m["max_drawdown"]*100, 2),
          "| b mean", round(dd.mean()*100, 3), "t", round(td, 3), "| c pct", round(pct_rf, 2),
          "| PASS", r["PASS_ALL"], "| secs", round(time.time()-t0), flush=True)
pd.Series(net0, index=keys).to_frame("unfiltered").join(
    pd.Series(out["V1"]["monthly"]).rename("V1")).join(pd.Series(out["V2"]["monthly"]).rename("V2")
    ).to_csv(RES / "monthly_nets.csv")
(Path(HERE) / "scripts_secfile" / "results_secfile.json").write_text(json.dumps(out, indent=1, default=float))
json.dump({"unfiltered": ref["monthly"], "V1": out["V1"]["monthly"], "V2": out["V2"]["monthly"],
           "n_removed_V1": out["V1"]["n_removed"], "n_removed_V2": out["V2"]["n_removed"]},
          open(RES / "results_secfile.json", "w"), indent=1)
print("done", round(time.time()-t0), flush=True)
