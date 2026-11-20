"""LONGSHORT-001 — dollar-neutral monthly long/short engine.

Implements the FROZEN rules in PREREGISTRATION.md:
  * decision on the LAST TRADING DAY of month M-1, entry at the OPEN of the first
    trading day of month M, exit at the OPEN of the first trading day of month M+1;
  * dollar-neutral legs (long notional +1, short notional -1), equal-count deciles,
    equal weight inside each leg;
  * 0.30% round-trip trading cost (0.15% per side, charged only when a name is
    actually opened / fully closed) and 1.0%/yr short-borrow fee prorated by trading
    days actually held;
  * delisting: exit at the last available close, slot holds cash until next rebalance.

RESEARCH ONLY. No broker, no database, no orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- constants
MIN_ADV_SHARES = 1_000_000.0
MIN_PRICE = 5.0
ADV_WINDOW = 20
VOL_WINDOW = 60            # trading days for realized volatility
MOM_SKIP_MONTHS = 1       # 12-1 momentum: the month just ending is skipped
MOM_LOOKBACK_MONTHS = 12
COST_PER_SIDE = 0.0015     # 0.30% round trip
BORROW_PER_YEAR = 0.01     # 1.0% per year on short notional
TRADING_DAYS_PER_YEAR = 252.0
DECILE = 0.10
MIN_ELIGIBLE = 20
SEED = 20261112
N_DRAWS = 1000

PANEL_DEFAULT = Path(
    "/root/.openclaw/worktrees/STRAT-027/experiments/strat_027/data/panel.parquet"
)


# ---------------------------------------------------------------- panel
@dataclass
class Panel:
    dates: np.ndarray          # datetime64[D]
    symbols: np.ndarray        # (S,)
    open_m: np.ndarray         # (T, S) float64
    close_m: np.ndarray        # (T, S) float64
    volume_m: np.ndarray       # (T, S) float64


def load_panel(path: Path | None = None) -> Panel:
    path = Path(path or PANEL_DEFAULT)
    df = pd.read_parquet(path, columns=["symbol", "date", "open", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    dates = np.sort(df["date"].unique())
    symbols = np.sort(df["symbol"].unique())
    di = pd.Index(dates).get_indexer(df["date"])
    si = pd.Index(symbols).get_indexer(df["symbol"])
    n_t, n_s = len(dates), len(symbols)

    def build(col: str) -> np.ndarray:
        m = np.full((n_t, n_s), np.nan, dtype=np.float64)
        m[di, si] = df[col].to_numpy(dtype=np.float64)
        return m

    return Panel(dates, symbols, build("open"), build("close"), build("volume"))


# ---------------------------------------------------------------- calendar
@dataclass
class MonthRow:
    key: str            # 'YYYY-MM' of the entry month M
    period: pd.Period
    entry_idx: int      # first trading day index of month M
    exit_idx: int       # first trading day index of month M+1
    decision_idx: int   # last trading day index of month M-1
    decision_date: pd.Timestamp
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp


def build_months(panel: Panel) -> list[MonthRow]:
    cal = pd.DatetimeIndex(panel.dates)
    periods = cal.to_period("M")
    firsts = {}
    lasts = {}
    for i, p in enumerate(periods):
        if p not in firsts:
            firsts[p] = i
        lasts[p] = i
    months = []
    keys = sorted(firsts)
    for k in range(1, len(keys) - 1):
        p, p_next, p_prev = keys[k], keys[k + 1], keys[k - 1]
        months.append(
            MonthRow(
                key=str(p),
                period=p,
                entry_idx=firsts[p],
                exit_idx=firsts[p_next],
                decision_idx=lasts[p_prev],
                decision_date=pd.Timestamp(panel.dates[lasts[p_prev]]),
                entry_date=pd.Timestamp(panel.dates[firsts[p]]),
                exit_date=pd.Timestamp(panel.dates[firsts[p_next]]),
            )
        )
    return months


def month_end_index(panel: Panel, period: pd.Period) -> int:
    """Index of the last trading day of `period` (-1 if the period is not in the panel)."""
    cal = pd.DatetimeIndex(panel.dates)
    idx = np.flatnonzero(cal.to_period("M") == period)
    return int(idx[-1]) if len(idx) else -1


# ---------------------------------------------------------------- factors
@dataclass
class MonthData:
    eligible: np.ndarray            # (S,) bool
    factor: np.ndarray              # (S,) float64 (nan where not eligible)
    mom: np.ndarray                 # (S,) float64
    vol: np.ndarray                 # (S,) float64
    ret: np.ndarray                 # (S,) return entry open -> exit (nan if not held)
    last_close_idx: np.ndarray      # (S,) last finite close index in [entry, exit]
    held_days: np.ndarray           # (S,) trading days actually held in the month
    exit_idx: int = -1              # exit open index of this month


def compute_month_data(panel: Panel, months: list[MonthRow]) -> list[MonthData]:
    n_s = panel.symbols.size
    close = panel.close_m
    with np.errstate(divide="ignore", invalid="ignore"):
        prev = np.vstack([np.full((1, n_s), np.nan), close[:-1]])
        daily_ret = close / prev - 1.0

    out = []
    for m in months:
        i = m.decision_idx
        e, x = m.entry_idx, m.exit_idx

        dec_close = close[i]
        adv_win = panel.volume_m[i - ADV_WINDOW + 1: i + 1]
        adv_count = np.isfinite(adv_win).sum(axis=0)
        with np.errstate(invalid="ignore"):
            adv = np.nanmean(adv_win, axis=0)
        # Tradability screen (frozen prereg section 3): a finite, strictly
        # positive open must exist on the ENTRY day.
        entry_open = panel.open_m[e]
        has_entry_open = np.isfinite(entry_open) & (entry_open > 0)
        base_eligible = (
            np.isfinite(dec_close)
            & (dec_close >= MIN_PRICE)
            & (adv_count >= ADV_WINDOW)
            & np.isfinite(adv)
            & (adv >= MIN_ADV_SHARES)
            & has_entry_open
        )

        # ---- 12-1 momentum (skip the month just ending) ----
        # 12-1 momentum: price at the end of month M-2 over the price at the end of
        # month M-13. The decision month (M-1) is skipped.
        # frozen formula: MOM = P(end of month M-2) / P(end of month M-13) - 1
        p1_period = m.period - 2   # end of month M-2
        p12_period = m.period - 13  # end of month M-13
        j1 = month_end_index(panel, p1_period)
        j12 = month_end_index(panel, p12_period)
        mom = np.full(n_s, np.nan)
        if j1 > 0 and j12 > 0 and j12 < j1 < i:
            c1 = close[j1]
            c12 = close[j12]
            ok = np.isfinite(c1) & np.isfinite(c12) & (c1 > 0) & (c12 > 0)
            mom = np.where(ok, c1 / c12 - 1.0, np.nan)
        mom_ok = base_eligible & np.isfinite(mom) & np.isfinite(dec_close)
        mom = np.where(mom_ok, mom, np.nan)

        # ---- 60-day realized volatility of daily returns ending at the decision close ----
        vol = np.full(n_s, np.nan)
        if i >= VOL_WINDOW:
            win = daily_ret[i - VOL_WINDOW + 1: i + 1]
            ok = np.isfinite(win).all(axis=0)
            with np.errstate(invalid="ignore"):
                sd = np.nanstd(win, axis=0, ddof=1)
            vol = np.where(ok & np.isfinite(sd), sd, np.nan)
        vol_ok = base_eligible & np.isfinite(vol)
        vol = np.where(vol_ok, vol, np.nan)

        # ---- holding-period returns (entry open -> exit open, delisting fallback) ----
        p0 = panel.open_m[e]
        p1 = panel.open_m[x]
        ret = np.full(n_s, np.nan)
        last_close_idx = np.full(n_s, -1, dtype=np.int64)
        held_days = np.zeros(n_s, dtype=np.int64)
        normal = np.isfinite(p0) & (p0 > 0) & np.isfinite(p1) & (p1 > 0)
        ret[normal] = p1[normal] / p0[normal] - 1.0
        last_close_idx[normal] = x
        held_days[normal] = x - e
        # delisting / halt: no open at the exit -> exit at last available close
        need = np.isfinite(p0) & (p0 > 0) & ~normal
        if need.any():
            seg = close[e:x, :]
            finite = np.isfinite(seg)
            any_fin = finite.any(axis=0)
            last_pos = np.where(any_fin, finite.shape[0] - 1 - np.argmax(finite[::-1, :], axis=0), -1)
            j = e + last_pos
            good = need & any_fin & (last_pos >= 0)
            lc = np.where(good, close[np.clip(j, 0, close.shape[0] - 1), np.arange(n_s)], np.nan)
            ret = np.where(good, lc / p0 - 1.0, ret)
            last_close_idx = np.where(good, j, last_close_idx)
            held_days = np.where(good, np.maximum(j - e + 1, 0), held_days)
        # no data at all after entry -> flat (cash) and the name is dead
        dead = np.isfinite(p0) & (p0 > 0) & ~np.isfinite(ret)
        ret = np.where(dead, 0.0, ret)

        out.append(
            MonthData(
                eligible=mom_ok | vol_ok,
                factor=mom,
                mom=mom,
                vol=vol,
                ret=ret,
                last_close_idx=last_close_idx,
                held_days=held_days,
                exit_idx=x,
            )
        )
    return out


# ---------------------------------------------------------------- legs
def select_legs(md: MonthData, factor: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (long_idx, short_idx, eligible_mask) for the given factor."""
    if factor == "MOM":
        elig = np.isfinite(md.mom) & np.isfinite(md.ret)
        values = md.mom
        long_first = True          # long the highest momentum
    elif factor == "VOL":
        elig = np.isfinite(md.vol) & np.isfinite(md.ret)
        values = md.vol
        long_first = False         # long the lowest volatility
    else:
        raise ValueError(factor)
    idx = np.flatnonzero(elig)
    n_leg = int(np.floor(DECILE * len(idx)))
    if len(idx) < MIN_ELIGIBLE or n_leg < 1:
        return np.array([], dtype=int), np.array([], dtype=int), elig
    # idx is ascending by symbol index, so a stable sort on the factor value keeps
    # ties in ascending symbol order (frozen tie-break rule).
    asc = idx[np.argsort(values[idx], kind="stable")]     # ascending factor, ties by symbol
    desc = idx[np.argsort(-values[idx], kind="stable")]    # descending factor, ties by symbol
    if factor == "MOM":
        long_idx = desc[:n_leg]                # highest momentum
        short_idx = asc[:n_leg]                # lowest momentum
    else:
        long_idx = asc[:n_leg]                 # lowest vol
        short_idx = desc[:n_leg]               # highest vol
    return np.sort(long_idx), np.sort(short_idx), elig


# ---------------------------------------------------------------- simulation
@dataclass
class SimResult:
    net: list = field(default_factory=list)
    long_gross: list = field(default_factory=list)
    short_gross: list = field(default_factory=list)
    cost: list = field(default_factory=list)
    borrow: list = field(default_factory=list)
    n_long: list = field(default_factory=list)
    n_short: list = field(default_factory=list)
    detail: list = field(default_factory=list)


def simulate_leg(md_list: list[MonthData], legs: list[np.ndarray], label: str = "") -> SimResult:
    """Simulate one leg (long or short) over a list of months.

    Cost convention (frozen): 0.15% of the leg notional is charged when a name is
    OPENED (month entry open) and 0.15% when it is FULLY CLOSED (next month entry
    open, or its last available close if it delisted mid-month). A name carried into
    the next month without being traded is not charged again.
    """
    res = SimResult()
    prev_set: set[int] = set()
    for r, (md, leg) in enumerate(zip(md_list, legs)):
        cur_set = set(int(s) for s in leg)
        n = len(cur_set)
        if n == 0:
            res.net.append(0.0)
            res.long_gross.append(0.0)
            res.short_gross.append(0.0)
            res.cost.append(0.0)
            res.borrow.append(0.0)
            res.n_long.append(0)
            res.n_short.append(0)
            res.detail.append({"month": r, "names": [], "ret": [], "weights": [],
                               "cost": 0.0, "borrow": 0.0, "opened": [], "closed": [],
                               "held_days": []})
            prev_set = set()
            continue
        w = 1.0 / n
        opened = cur_set - prev_set
        closed = prev_set - cur_set
        # names held this month that lost their quote before the exit open:
        # exited at their last available close (slot holds cash afterwards)
        dead_cur = {s for s in cur_set if md.last_close_idx[s] != md.exit_idx}
        cost = COST_PER_SIDE * (len(opened) + len(closed) + len(dead_cur)) * w
        gross = 0.0
        borrow = 0.0
        names, rets, days = [], [], []
        for s in sorted(cur_set):
            rr = float(md.ret[s])
            gross += w * rr
            names.append(int(s))
            rets.append(rr)
            days.append(int(md.held_days[s]))
            borrow += BORROW_PER_YEAR * (int(md.held_days[s]) / TRADING_DAYS_PER_YEAR) * w
        res.net.append(gross)
        res.long_gross.append(gross)
        res.short_gross.append(gross)
        res.cost.append(cost)
        res.borrow.append(borrow)
        res.n_long.append(len(cur_set))
        res.n_short.append(len(cur_set))
        res.detail.append({
            "month": r,
            "names": names,
            "ret": rets,
            "weights": [w] * len(names),
            "cost": cost,
            "borrow": borrow,
            "opened": sorted(opened),
            "closed": sorted(closed),
            "dead": sorted(dead_cur),
            "held_days": days,
        })
        prev_set = cur_set - dead_cur
    res.net = np.array(res.net)
    res.long_gross = np.array(res.long_gross)
    res.short_gross = np.array(res.short_gross)
    res.cost = np.array(res.cost)
    res.borrow = np.array(res.borrow)
    return res


def net_series(long_res: SimResult, short_res: SimResult) -> np.ndarray:
    """net = long_pnl + short_pnl with the frozen sign conventions."""
    long_pnl = long_res.net - long_res.cost
    short_pnl = -short_res.net - short_res.borrow - short_res.cost
    return long_pnl + short_pnl


# ---------------------------------------------------------------- metrics
def t_stat(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2:
        return float("nan")
    s = x.std(ddof=1)
    if s == 0:
        return float("inf") if x.mean() > 0 else float("-inf")
    return float(x.mean() / (s / np.sqrt(n)))


def annualize_ret(mean_monthly: float) -> float:
    return float((1.0 + mean_monthly) ** 12 - 1.0)


def max_drawdown(net: np.ndarray) -> float:
    eq = np.cumprod(1.0 + net)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return float(np.min(eq / peak - 1.0))


def percentile_vs_random(strategy_mean: float, draw_means: np.ndarray) -> float:
    """PERCENT (0-100) of full-period random draws whose mean net is STRICTLY LESS
    than the strategy's mean net over the same window."""
    return float(100.0 * np.mean(draw_means < strategy_mean))
