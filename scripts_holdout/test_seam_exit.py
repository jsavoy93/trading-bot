"""HOLDOUT-MOM-002 unit test: a held name across a block seam exits at a REAL price.

Builds a date-major parquet layout whose symbol blocks are interleaved (the
layout that produced the padded tail exits) where "AAA" has no quote on the exit
day, and asserts the delisting/halt fallback uses AAA's own last real close.
The panel is assembled with a chronological sort of the interleaved row order and
one missing day-0 row (68199 rows vs 68200 slots: textually the real panel's
layout), which leaves the symbol's earlier row resident in the tail slot; the
fallback must read the symbol's own real last close, never that resident value.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine_fixed as E

n_days, n_syms = 310, 220
dates = pd.to_datetime(pd.bdate_range("2021-01-04", periods=n_days))
rows = []
for d_i, d in enumerate(dates):
    for sym in [f"S{k:03d}" for k in range(n_syms - 2)] + ["BBB", "AAA"]:
        if d_i == n_days and sym == "S000":
            continue                                   # one row missing
        if d_i == n_days:
            continue                                   # panel ends at the exit day
        if sym == "AAA":
            p = np.nan if d_i >= n_days - 1 else 10.0 * (1 + 0.001 * d_i)  # no AAA quote from exit day
        elif sym == "BBB":
            p = 53.0 if d_i == n_days - 1 else 50.0
        else:
            p = 1.0
        rows.append((sym, d, p))

df = pd.DataFrame(rows, columns=["symbol", "date", "price"])
# data rows in chronological order = what the frozen order-statistic mask produces
order = np.argsort(df["date"].values.astype("datetime64[ns]"), kind="stable")
data = df.iloc[order].reset_index(drop=True)
syms = np.sort(df["symbol"].unique())                 # S000.. AAA
n_t = len(dates)
di = np.sort(data["date"].unique()).searchsorted(data["date"].values.astype("datetime64[ns]"))
si = np.searchsorted(syms.astype(str), data["symbol"].values.astype(str))
open_m = np.full((n_t, n_syms), np.nan)
open_m[di, si] = data["price"].to_numpy(float)

months = [E.MonthRow(key="2021-10", period=pd.Period("2021-10", "M"),
                     entry_idx=n_days - 4, exit_idx=n_days - 1,
                     decision_idx=n_days - 5, decision_date=pd.Timestamp(dates[n_days - 5]),
                     entry_date=pd.Timestamp(dates[n_days - 4]),
                     exit_date=pd.Timestamp(dates[n_days - 1]))]
panel = E.Panel(dates, syms, open_m, open_m.copy(), np.ones_like(open_m))
md = E.compute_month_data(panel, months)[0]
a, b = list(syms).index("AAA"), list(syms).index("BBB")
tgt = 10.0 * (1 + 0.001 * (n_days - 2)) / (10.0 * (1 + 0.001 * (n_days - 4))) - 1.0
assert abs(md.ret[a] - tgt) < 1e-12, (md.ret[a], "seam fallback read a padded price")
assert md.last_close_idx[a] == n_days - 2, md.last_close_idx[a]
assert md.held_days[a] == 3, md.held_days[a]  # entry -> last real close
assert abs(md.ret[b] - (53.0 / 50.0 - 1.0)) < 1e-12, md.ret[b]
# the fixture really contains a stale resident row: the last day's row was
# skipped, so the slot still holds an earlier price (that is the padded value)
unfilled = int(np.isnan(open_m).sum())
assert unfilled >= 1, "fixture is dense - no seam hole present"
print(f"PASS test_seam_exit: AAA fallback exit at its own last real close "
      f"{10.0 * (1 + 0.001 * (n_days - 2)):.3f} ({tgt:+.4%}), not a padded tail price; "
      f"BBB normal exit {md.ret[b]:+.4%} ok; {unfilled} empty panel cells -> seam hole present")
