"""Independent hand check of sample months (LONGSHORT-001).

Recomputes eligible universes, legs, entry/exit prices, leg returns, trading
costs, borrow and net return DIRECTLY from the parquet panel with plain pandas,
without importing the simulation code in engine.py.

Run:  python3 check_sample_months.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

PANEL = "/root/.openclaw/worktrees/STRAT-027/experiments/strat_027/data/panel.parquet"
OUT = Path(__file__).resolve().parent / "results" / "independent_check.json"

COST = 0.0015
BORROW = 0.01
DECILE = 0.10
DEV_MONTHS = [pd.Period("2021-01", "M") + i for i in range(48)]
SAMPLE = [pd.Period("2021-01", "M"), pd.Period("2022-12", "M"), pd.Period("2024-12", "M")]


def main():
    df = pd.read_parquet(PANEL, columns=["symbol", "date", "open", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    close_wide = df.pivot(index="date", columns="symbol", values="close")
    open_wide = df.pivot(index="date", columns="symbol", values="open")
    vol_wide = df.pivot(index="date", columns="symbol", values="volume")
    dates = close_wide.index

    first_day, last_day = {}, {}
    for p, grp in dates.to_series().groupby(dates.to_period("M")):
        first_day[p] = grp.index[0]
        last_day[p] = grp.index[-1]

    prev_legs = {"MOM": (set(), set()), "VOL": (set(), set())}
    records = []

    for entry_period in DEV_MONTHS:
        dec_period, exit_period = entry_period - 1, entry_period + 1
        dec_date = last_day[dec_period]
        entry_date = first_day[entry_period]
        exit_date = first_day[exit_period]

        dec_close = close_wide.loc[dec_date]
        adv = vol_wide.loc[:dec_date].tail(20).mean()
        entry_open = open_wide.loc[entry_date]
        exit_open = open_wide.loc[exit_date]

        mom = close_wide.loc[last_day[entry_period - 2]] / close_wide.loc[last_day[entry_period - 13]] - 1.0
        # VOL: 60 daily returns ending at the decision close -> needs 61 valid closes
        win = close_wide.loc[:dec_date].tail(61)
        daily = win.pct_change(fill_method=None).iloc[1:]
        vol = daily.std(ddof=1)
        vol_hist_ok = win.notna().all(axis=0) & daily.notna().all(axis=0)

        adv_count = vol_wide.loc[:dec_date].tail(20).notna().sum(axis=0)
        common = (dec_close.notna() & (dec_close >= 5.0) & (adv >= 1_000_000)
                  & (adv_count >= 20)
                  & entry_open.notna() & (entry_open > 0))

        n_days = int(((dates >= entry_date) & (dates < exit_date)).sum())

        def leg_return(names):
            gross = 0.0
            borrow = 0.0
            dead = set()
            per = {}
            for s in names:
                e = float(entry_open[s])
                x = exit_open[s]
                if pd.notna(x) and x > 0:
                    r = float(x) / e - 1.0
                    days = n_days
                else:
                    seg = close_wide.loc[entry_date:exit_date, s].dropna()
                    if len(seg) == 0:
                        r = 0.0
                        days = 0
                    else:
                        r = float(seg.iloc[-1]) / e - 1.0
                        days = int(((dates >= entry_date) & (dates <= seg.index[-1])).sum())
                        dead.add(s)
                per[s] = (r, days)
                gross += r / len(names)
                borrow += BORROW * (days / 252.0) / len(names)
            return gross, borrow, dead, per

        rec = {"month": str(entry_period), "decision_date": str(dec_date.date()),
               "entry_date": str(entry_date.date()), "exit_date": str(exit_date.date()),
               "trading_days_held": n_days}

        for variant in ("MOM", "VOL"):
            if variant == "MOM":
                factor, elig = mom, common & mom.notna()
                long_rank = factor[elig].sort_values(ascending=False, kind="stable")
                short_rank = factor[elig].sort_values(ascending=True, kind="stable")
            else:
                factor, elig = vol, common & vol.notna() & vol_hist_ok
                long_rank = factor[elig].sort_values(ascending=True, kind="stable")
                short_rank = factor[elig].sort_values(ascending=False, kind="stable")
            n_leg = int(np.floor(DECILE * elig.sum()))
            long_names = list(long_rank.index[:n_leg])
            short_names = list(short_rank.index[:n_leg])
            prev_long, prev_short = prev_legs[variant]

            lg, lb, ldead, lper = leg_return(long_names)
            sg, sb, sdead, sper = leg_return(short_names)
            l_open = set(long_names) - prev_long
            l_closed = prev_long - set(long_names)
            s_open = set(short_names) - prev_short
            s_closed = prev_short - set(short_names)
            # cost = 0.15% per side on the traded notional (each name weighs 1/n_leg)
            l_cost = COST * (len(l_open) + len(l_closed) + len(ldead)) / n_leg
            s_cost = COST * (len(s_open) + len(s_closed) + len(sdead)) / n_leg
            long_pnl = lg - l_cost
            short_pnl = -sg - sb - s_cost

            rec[variant] = {
                "n_eligible": int(elig.sum()), "n_leg": n_leg,
                "long_names_head": list(long_names[:5]),
                "short_names_head": list(short_names[:5]),
                "long_names_head_all": list(long_names),
                "short_names_head_all": list(short_names),
                "long_gross": lg, "long_cost": l_cost, "long_pnl": long_pnl,
                "short_gross": sg, "short_borrow": sb, "short_cost": s_cost,
                "short_pnl": short_pnl,
                "net": long_pnl + short_pnl,
                "n_opened_long": len(l_open), "n_closed_long": len(l_closed),
                "n_opened_short": len(s_open), "n_closed_short": len(s_closed),
                "n_dead_long": len(ldead), "n_dead_short": len(sdead),
                "long_detail_head": [
                    [s, round(lper[s][0], 6), lper[s][1], float(entry_open[s]),
                     float(exit_open[s]) if pd.notna(exit_open[s]) else None]
                    for s in long_names[:5]],
                "short_detail_head": [
                    [s, round(sper[s][0], 6), sper[s][1], float(entry_open[s]),
                     float(exit_open[s]) if pd.notna(exit_open[s]) else None]
                    for s in short_names[:5]],
            }
            prev_legs[variant] = (set(long_names) - ldead, set(short_names) - sdead)

        if entry_period in SAMPLE:
            records.append(rec)
            print(json.dumps(rec, indent=2, default=str))

    OUT.write_text(json.dumps(records, indent=2, default=str))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
