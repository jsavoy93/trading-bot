"""SHORTSIDE-001: short-side mirror of the deployed BUY rule. RESEARCH ONLY. No broker, no orders.

Rule is EXACTLY the frozen PREREGISTRATION.md:
  SHORT when RSI(14) > 65 and SMA(10) < SMA(30) at close t; entry next open;
  exit earliest of RSI(14) < 50 (next open), close >= entry*1.08 (next open), 10 trading days
  (close of day 10 held); costs 0.15%/side + 5%/yr borrow; ADV20>=1e6 and price>=5 as the
  shortable proxy; one position per name.
Random baseline: 1000 draws, seed 20261119; same names, same trade count, entry dates drawn
uniformly from the strategy's own ENTRY dates, hold lengths from the strategy's own
distribution, same costs. Monthly portfolio return = mean trade net entered that month.
"""
import sys, json
import numpy as np, pandas as pd
sys.path.insert(0, "/root/.openclaw/worktrees/LONGSHORT-001/experiments/longshort_001")
import engine as E

SEED = 20261119
N_DRAWS = 1000
COST_SIDE = 0.0015
BORROW = 0.05
TD = 252.0
RSI_BUY_LEVEL = 35            # deployed override (schema default 30)
DEV = ("2021-01-01", "2024-12-31")
HOLD = ("2025-01-01", "2026-08-31")

panel = E.load_panel(E.PANEL_DEFAULT)
close, opn, vol = panel.close_m, panel.open_m, panel.volume_m
T, S = close.shape
dates = pd.DatetimeIndex(panel.dates)

def wilder_rsi(x, n=14):
    out = np.full_like(x, np.nan)
    d = np.diff(x, prepend=np.nan)
    up = np.where(d > 0, d, 0.0); dn = np.where(d < 0, -d, 0.0)
    au = np.full_like(x, np.nan); ad = np.full_like(x, np.nan)
    if x.size <= n + 1: return out
    au[n] = np.nanmean(up[1:n+1]); ad[n] = np.nanmean(dn[1:n+1])
    for i in range(n+1, x.size):
        u = 0.0 if not np.isfinite(up[i]) else up[i]
        dd = 0.0 if not np.isfinite(dn[i]) else dn[i]
        au[i] = (au[i-1]*(n-1) + u) / n
        ad[i] = (ad[i-1]*(n-1) + dd) / n
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = au / ad
    out = 100 - 100/(1+rs)
    out[(ad == 0) & np.isfinite(au)] = 100.0
    return out

rsi = np.full((T, S), np.nan); smaf = np.full((T, S), np.nan)
smas = np.full((T, S), np.nan); adv = np.full((T, S), np.nan)
for j in range(S):
    x = close[:, j]
    if np.isfinite(x).sum() < 60: continue
    rsi[:, j] = wilder_rsi(x)
    s = pd.Series(x)
    smaf[:, j] = s.rolling(10).mean().to_numpy()
    smas[:, j] = s.rolling(30).mean().to_numpy()
    adv[:, j] = pd.Series(vol[:, j]).rolling(20).mean().to_numpy()
print("indicators done", flush=True)

SHORT_LEVEL = 100 - RSI_BUY_LEVEL     # 65
GATES = (rsi > SHORT_LEVEL) & (smaf < smas) & (adv >= 1e6) & (close >= 5)

def run_window(start, end):
    i0 = int(dates.searchsorted(pd.Timestamp(start)))
    i1 = int(dates.searchsorted(pd.Timestamp(end), side="right")) - 1
    trades, open_pos, gates_by_day = [], {}, {}
    for t in range(max(i0, 60), i1 + 1):
        for j in list(open_pos):
            e_i, e_p = open_pos[j]
            held = t - e_i
            c = close[t, j]
            if not np.isfinite(c): continue
            reason = None
            if c >= e_p * 1.08: reason = "stop"
            elif np.isfinite(rsi[t, j]) and rsi[t, j] < 50: reason = "rsi50"
            elif held >= 10: reason = "time"
            if reason:
                if reason == "time":
                    x_i, px = t, c
                else:
                    x_i = t + 1
                    if x_i >= T: continue
                    px = opn[x_i, j] if np.isfinite(opn[x_i, j]) else c
                gross = e_p / px - 1
                cost = 2 * COST_SIDE + BORROW * held / TD
                trades.append(dict(sym=str(panel.symbols[j]), entry_i=e_i, exit_i=x_i,
                                   entry=float(e_p), exit=float(px), days=int(held),
                                   reason=reason, net=float(gross - cost)))
                del open_pos[j]
        gd = np.flatnonzero(GATES[t])
        if gd.size: gates_by_day[t] = gd
        for j in gd:
            if j in open_pos: continue
            if t + 1 > i1: continue
            px = opn[t+1, j]
            if not (np.isfinite(px) and px > 0): continue
            open_pos[j] = (t + 1, float(px))
    return trades, gates_by_day

def stats(trades, start, end):
    df = pd.DataFrame(trades)
    if df.empty: raise SystemExit("no trades")
    df["m"] = dates[df.entry_i].to_period("M").astype(str)
    m = df.groupby("m").net.mean()
    allm = pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq="M").astype(str)
    m = m.reindex(allm, fill_value=0.0)
    eq = (1 + m).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    return df, m, dict(trades=int(len(df)), months_traded=int((df.groupby("m").size() > 0).sum()),
                       mean_net=float(df.net.mean()), win_rate=float((df.net > 0).mean()),
                       max_drawdown=dd, worst_trade=float(df.net.min()),
                       by_reason={k: int(v) for k, v in df.reason.value_counts().items()})

def random_baseline(df, gates_by_day, rng):
    """Random short entries on the same names, same trade count.

    For each draw every window day that has at least one gate-passing, shortable name is
    equally likely; a candidate is drawn, then a hold length from the strategy's own
    hold-length distribution, and the realised return is that name's return over that hold
    with the same costs. Returns are cached per (symbol, entry day, hold) so a draw is cheap.
    """
    pool_days = [t for t, js in gates_by_day.items() if len(js)]
    holds_pool = df.days.to_numpy()
    n = len(df)
    cache = {}
    out = np.empty(N_DRAWS)
    for d in range(N_DRAWS):
        tot = 0.0; cnt = 0
        for _ in range(n):
            t = pool_days[rng.integers(len(pool_days))]
            js = gates_by_day[t]
            j = int(js[rng.integers(len(js))])
            h = int(holds_pool[rng.integers(len(holds_pool))])
            key = (t, j, h)
            r = cache.get(key)
            if r is None:
                xi = min(t + 1 + h, T - 1)
                e_px = opn[t + 1, j]; x_px = close[xi, j] if h >= 10 else (opn[min(t+2, T-1), j] if False else close[xi, j])
                if not (np.isfinite(e_px) and np.isfinite(x_px) and e_px > 0):
                    r = np.nan
                else:
                    r = e_px / x_px - 1 - (2 * COST_SIDE + BORROW * h / TD)
                cache[key] = r
            if np.isfinite(r):
                tot += r; cnt += 1
        out[d] = tot / cnt if cnt else np.nan
    return out

def bars_for(s, pct, ex, tstat):
    b = {"months>=24": s["months_traded"] >= 24, "mean_net>0": s["mean_net"] > 0,
         "excess>0": ex > 0, "pct>=97.5": pct >= 97.5, "t>=2.24": tstat >= 2.24}
    b["ALL_PASS"] = all(b.values()); return b

if __name__ == "__main__":
    win = sys.argv[1] if len(sys.argv) > 1 else "dev"
    start, end = (DEV if win == "dev" else HOLD)
    tr, gates_by_day = run_window(start, end)
    df, m, s = stats(tr, start, end)
    rng = np.random.default_rng(SEED)
    draws = random_baseline(df, gates_by_day, rng)
    pct = float((draws < s["mean_net"]).mean() * 100)
    ex = s["mean_net"] - float(draws.mean())
    tstat = float(ex / (np.nanstd(draws, ddof=1)))
    b = bars_for(s, pct, ex, tstat)
    out = dict(window=win, start=str(start), end=str(end), params=dict(seed=SEED, draws=N_DRAWS,
               cost_side=COST_SIDE, borrow=BORROW, rsi_short_level=SHORT_LEVEL, hold_days=10, stop=0.08),
               metrics=s, random_mean=float(draws.mean()), random_sd=float(draws.std(ddof=1)),
               percentile=pct, excess=ex, tstat_excess=tstat, bars=b,
               monthly_net={k: float(v) for k, v in m.items()},
               worst5=df.nsmallest(5, "net")[["sym", "reason", "days", "net"]].to_dict("records"))
    json.dump(out, open(f"results_{win}.json", "w"), indent=1)
    print(json.dumps({k: out[k] for k in out if k not in ("monthly_net",)}, indent=1)[:2500])
