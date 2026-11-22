"""EVENTNEWS-001 runner — exact mechanics are fixed in PREREGISTRATION.md (commit 1153b15).
V1 (earnings) and V3 (V1 OR V2) are runnable; V2 needs historical news (probe: unavailable).
No data after 2024-12-31 is touched. RESEARCH ONLY."""
import json, re, sys, hashlib
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts_holdout"))
sys.path.insert(0, "/root/research/splitscreen-002/scripts")
import engine_fixed as E
import run_study as R

PANEL = HERE / "panel_splits.parquet"
DATA = HERE / "data"; OUT = HERE / "scripts_eventnews" / "results"; OUT.mkdir(parents=True, exist_ok=True)
sha = hashlib.sha256(PANEL.read_bytes()).hexdigest()
# Panel: the HOLDOUT-MOM-002 study panel is panel_holdout.parquet (sha256 frozen in its
# prereg/runner). The DEV window 2021-01..2024-12 sits well before 2025-01-01, so the DEV
# months built from it are identical to a 2024-12-31-truncated panel (plumbing note).
STUDY_PANEL = Path("/root/research/holdout-mom-001/scripts_holdout/panel_holdout.parquet")
sha = hashlib.sha256(STUDY_PANEL.read_bytes()).hexdigest()
assert sha == "4387206b0c4b1ad396596b6f84874b0ad44901237c5ae30adc530e1b8ae5c071", "panel changed"
DEV_START, DEV_END = pd.Period("2021-01", "M"), pd.Period("2024-12", "M")
SEED2, N_DRAWS = 20261121, 1000
KEYWORDS = ["offering", "dilution", "S-1", "resale", "FDA", "complete response", "advisory committee",
            "PDUFA", "guidance cut", "lowers guidance", "going concern", "bankruptcy", "delisting",
            "investigation", "downgrade"]

panel = E.load_panel(STUDY_PANEL)
months = E.build_months(panel)
mds = E.compute_month_data(panel, months)
syms = list(panel.symbols)
rows = [i for i, m in enumerate(months) if DEV_START <= m.period <= DEV_END
        and m.exit_date <= pd.Timestamp("2024-12-31")]
md_rows = [mds[i] for i in rows]
keys = [months[i].key for i in rows]
dec_dates = [months[i].decision_date for i in rows]
ent = [months[i].entry_date for i in rows]; ext = [months[i].exit_date for i in rows]
print("DEV months", len(rows), keys[0], "..", keys[-1], flush=True)

legs_long, legs_short, elig, nleg = [], [], [], []
for md in md_rows:
    lg, sh, el = E.select_legs(md, "MOM")
    legs_long.append(lg); legs_short.append(sh)
    elig.append(np.flatnonzero(el)); nleg.append(len(lg))

# ---------------- flags
earn = json.loads((DATA / "v1_earnings_dates.json").read_text())
earn = {s: sorted(pd.Timestamp(d) for d in v) for s, v in earn.items()}
def v1_flag(sym, i):
    ds = earn.get(sym)
    if not ds: return False
    lo, hi = ent[i], ext[i]
    return any(lo <= d < hi for d in ds)

KW = re.compile("|".join(r"\b" + re.escape(k).replace(r"\ ", r"\s+") for k in KEYWORDS), re.I)
news_dir = DATA / "v2_news_raw"
def v2_flag(sym, i):
    if not news_dir.exists(): raise SystemExit("V2: no news cache -> blocked")
    f = news_dir / sym / f"{dec_dates[i] - pd.Timedelta(days=30):%Y-%m-%d}_{dec_dates[i]:%Y-%m-%d}.json"
    if not f.exists(): raise SystemExit(f"V2: missing window {sym}")
    for it in json.loads(f.read_text()):
        t = pd.Timestamp(it["t"]).tz_localize(None)
        if t < dec_dates[i] and t >= dec_dates[i] - pd.Timedelta(days=30) and KW.search(it["h"] or ""):
            return True
    return False

VARIANTS = {"V1_earnings": v1_flag}
VARIANTS["V3_both"] = lambda s, i: v1_flag(s, i) or (v2_flag(s, i) if (DATA / "v2_news_raw").exists() else False)  # V2 unavailable -> V3 == V1

# random-benchmark draws (bar (a)); identical machinery to HOLDOUT-MOM-002 dev
draws_mom = R.random_monthly_nets(md_rows, elig, nleg, E.N_DRAWS, E.SEED)
rand = draws_mom.mean(axis=0)

def simulate(legs_l, legs_s):
    E.compute_month_data.FALLBACK_STATS.clear()
    E.compute_month_data.FALLBACK_STATS.update({"held_legs": 0, "fallback_last_real_close": 0})
    lo = E.simulate_leg(md_rows, legs_l, "long"); sh = E.simulate_leg(md_rows, legs_s, "short")
    return E.net_series(lo, sh)

def metrics(net):
    return R.window_metrics(net, net - rand, rand, [0]*len(net), [0]*len(net), draws_mom.mean(axis=1))

def filtered_legs(flag):
    L, S, n_rem = [], [], {"long": [], "short": []}
    for i, md in enumerate(md_rows):
        lg, sh = legs_long[i], legs_short[i]
        keep_l = np.array([s for s in lg if not flag(syms[s], i)], dtype=int)
        keep_s = np.array([s for s in sh if not flag(syms[s], i)], dtype=int)
        if len(keep_l) == 0 or len(keep_s) == 0:
            keep_l, keep_s = lg, sh
        n_rem["long"].append(len(lg) - len(keep_l)); n_rem["short"].append(len(sh) - len(keep_s))
        L.append(keep_l); S.append(keep_s)
    return L, S, {k: np.array(v) for k, v in n_rem.items()}

def random_filter_nets(base_L, base_S, n_rem, n_draws=1000):
    out = np.zeros((n_draws, len(md_rows)))
    for d in range(n_draws):
        Ls, Ss = [], []
        for r in range(len(md_rows)):
            for base, dest, side in ((base_L[r], Ls, "long"), (base_S[r], Ss, "short")):
                k = int(n_rem[side][r])
                if k <= 0 or len(base) - k < 1:
                    dest.append(base)
                else:
                    rng = np.random.default_rng(SEED2 + 1000 * d + r + (0 if side == "long" else 500000))
                    dest.append(np.sort(rng.choice(base, size=len(base) - k, replace=False)))
        out[d] = simulate(Ls, Ss)
    return out

result = {"panel_sha256": sha, "dev_months": len(rows), "keys": keys}
net0 = simulate(legs_long, legs_short)
m0 = metrics(net0)
result["unfiltered"] = {k: float(v) for k, v in m0.items() if not isinstance(v, (list, dict))}
result["unfiltered"]["monthly"] = dict(zip(keys, map(float, net0)))
print("unfiltered", json.dumps({k: round(result["unfiltered"][k], 4) for k in
      ("mean_monthly_net", "t_stat_excess", "percentile_vs_random", "max_drawdown")}), flush=True)

for name, flag in VARIANTS.items():
    L, S, n_rem = filtered_legs(flag)
    net = simulate(L, S)
    m = metrics(net)
    d = net - net0
    from scipy import stats
    td = float(np.mean(d) / (np.std(d, ddof=1) / np.sqrt(len(d)))) if np.std(d, ddof=1) > 0 else 0.0
    rf = random_filter_nets(legs_long, legs_short, n_rem)
    pct_rf = E.percentile_vs_random(float(net.mean()), rf.mean(axis=1))
    bars = R.dev_bars(m)
    res = {k: float(v) for k, v in m.items() if not isinstance(v, (list, dict))}
    res.update({"bar_a": bars, "bar_b": {"mean_improvement": float(d.mean()), "t_improvement": td,
                "months_improved": int((d > 0).sum()), "pass": bool(d.mean() > 0 and td >= 2.24)},
                "bar_c": {"percentile_vs_random_filters": pct_rf, "pass": bool(pct_rf >= 99.17)},
                "avg_removed_long": float(n_rem["long"].mean()), "avg_removed_short": float(n_rem["short"].mean()),
                "monthly": dict(zip(keys, map(float, net))),
                "top3_worst": sorted(zip(keys, map(float, net)), key=lambda x: x[1])[:3],
                "PASS_ALL": bool(bars["ALL_PASS"] and res["bar_b"].get("pass") and pct_rf >= 99.17)})
    result[name] = res
    print(name, "A", bars["ALL_PASS"], "t", round(m["t_stat_excess"], 4), "mean", round(m["mean_monthly_net"], 4),
          "b t", round(td, 4), "c pct", round(pct_rf, 2), "removed L/S", round(n_rem["long"].mean(), 2),
          round(n_rem["short"].mean(), 2), "PASS", res["PASS_ALL"], flush=True)

# coverage
acc = tot = 0
for i in range(len(rows)):
    for s in np.concatenate([legs_long[i], legs_short[i]]):
        tot += 1; acc += syms[s] in earn
result["earnings_coverage_in_legs_pct"] = 100.0 * acc / tot
print("earnings coverage in DEV leg-months", round(100*acc/tot, 2), flush=True)
(OUT / "results_eventnews.json").write_text(json.dumps(result, indent=1, default=float))
print("wrote", OUT / "results_eventnews.json", flush=True)
