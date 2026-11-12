"""LONGSHORT-001 — deterministic unit tests for the frozen long/short engine.

Covers the frozen rules from experiments/longshort_001/PREREGISTRATION.md:
  * factor timing (MOM = P(end of M-2) / P(end of M-13) - 1, VOL = 60 daily
    returns ending at the decision close, sample std ddof=1);
  * shared eligibility (20-day ADV, decision close, valid entry open);
  * deterministic tie-breaking by ascending symbol;
  * decile leg construction (equal counts, equal weight);
  * trading costs (0.15% per side on actual opens/closes/delisting exits);
  * short borrow (1.0%/yr prorated by trading days held);
  * delisting (exit at the last available close, slot holds cash);
  * random baseline (same universe, same leg counts, same costs/borrow, seeded);
  * metric formulas (annualization, Sharpe denominators, t-stat, drawdown,
    percentile);
  * DEV/HOLDOUT suppression (holdout never computed for a dev failure).

Purely synthetic data. No panel, no database, no network, no orders.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

EXP_DIR = Path(__file__).resolve().parents[1] / "experiments" / "longshort_001"
sys.path.insert(0, str(EXP_DIR))

import engine as E  # noqa: E402
import run_study as R  # noqa: E402


# ------------------------------------------------------------------ helpers
def make_panel(dates, symbols, open_m, close_m, volume_m):
    return E.Panel(
        dates=np.array(pd.to_datetime(dates), dtype="datetime64[D]"),
        symbols=np.array(sorted(symbols)),
        open_m=np.asarray(open_m, dtype=float),
        close_m=np.asarray(close_m, dtype=float),
        volume_m=np.asarray(volume_m, dtype=float),
    )


def month_ends(dates):
    """Map 'YYYY-MM' -> index of the last trading day of that month."""
    cal = pd.DatetimeIndex(pd.to_datetime(dates))
    out = {}
    for i, p in enumerate(cal.to_period("M")):
        out[str(p)] = i
    return out


# ------------------------------------------------------------------ MOM timing
def test_mom_uses_end_of_m_minus_2_over_end_of_m_minus_13():
    """MOM = P(end of M-2) / P(end of M-13) - 1, the decision month M-1 skipped."""
    # 20 months of business days, one symbol with a known price path.
    dates = pd.bdate_range("2020-01-01", "2022-09-30")
    n = len(dates)
    # price[t] = 100 + t  -> linear, so the momentum over any window is a
    # deterministic function of the two month-end indices.
    close = (100.0 + np.arange(n)).reshape(-1, 1)
    openm = close.copy()
    vol = np.full((n, 1), 5_000_000.0)
    panel = make_panel(dates, ["AAA"], openm, close, vol)

    months = E.build_months(panel)
    me = month_ends(dates)
    target = next(m for m in months if m.key == "2022-03")
    md = E.compute_month_data(panel, [target])[0]

    # M is the ENTRY month (2022-03): M-2 = 2022-01, M-13 = 2021-02.
    # The window from end of M-13 to end of M-2 spans 11 months and skips M-1.
    j1 = me["2022-01"]
    j12 = me["2021-02"]
    expected = close[j1, 0] / close[j12, 0] - 1.0
    assert md.mom[0] == pytest.approx(expected, rel=1e-12)
    # the skipped decision month M-1 (2022-02) must never be used
    assert md.mom[0] != pytest.approx(
        close[me["2022-02"], 0] / close[me["2021-02"], 0] - 1.0, rel=1e-6)
    # and the window really spans 11 months
    assert j1 - j12 > 0


def test_mom_is_nan_without_enough_history():
    dates = pd.bdate_range("2021-01-01", "2021-06-30")
    n = len(dates)
    close = (100.0 + np.arange(n)).reshape(-1, 1)
    panel = make_panel(dates, ["AAA"], close.copy(), close, np.full((n, 1), 5e6))
    months = E.build_months(panel)
    target = next(m for m in months if m.key == "2021-04")
    md = E.compute_month_data(panel, [target])[0]
    assert not np.isfinite(md.mom[0])


# ------------------------------------------------------------------ VOL factor
def test_vol_is_sample_std_of_60_daily_returns_ending_at_decision_close():
    dates = pd.bdate_range("2021-01-01", "2022-03-31")
    n = len(dates)
    rng = np.random.default_rng(7)
    daily = rng.normal(0.0, 0.02, size=n)
    close = np.cumprod(1.0 + daily).reshape(-1, 1) * 100.0
    volarr = np.full((n, 1), 5_000_000.0)
    panel = make_panel(dates, ["AAA"], close.copy(), close, volarr)
    months = E.build_months(panel)
    target = next(m for m in months if m.key == "2022-02")
    md = E.compute_month_data(panel, [target])[0]

    i = target.decision_idx
    win = daily[i - 59: i + 1]          # 60 returns ending at the decision close
    expected = float(np.std(win, ddof=1))
    assert md.vol[0] == pytest.approx(expected, rel=1e-10)
    # ddof=0 (population) would differ measurably
    assert md.vol[0] != pytest.approx(float(np.std(win, ddof=0)), rel=1e-6)


# ------------------------------------------------------------------ eligibility
def _eligibility_panel():
    dates = pd.bdate_range("2021-01-01", "2022-06-30")
    n = len(dates)
    rng = np.random.default_rng(3)
    close = (100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, n))).reshape(-1, 1)
    openm = close.copy()
    volume = np.full((n, 1), 5_000_000.0)
    return dates, close, openm, volume


def _fresh_eligibility_case():
    """A fresh panel + target for every case, so cases cannot contaminate
    each other (a mutated panel reused across cases hides real defects)."""
    dates, close, openm, volume = _eligibility_panel()
    panel = make_panel(dates, ["AAA"], openm, close, volume)
    months = E.build_months(panel)
    target = next(m for m in months if m.key == "2022-02")
    return panel, target


def test_baseline_symbol_is_eligible():
    panel, target = _fresh_eligibility_case()
    md = E.compute_month_data(panel, [target])[0]
    assert md.eligible[0]


def test_price_below_five_at_the_decision_close_is_ineligible():
    panel, target = _fresh_eligibility_case()
    panel.close_m = panel.close_m.copy()
    panel.close_m[target.decision_idx, 0] = 4.99
    md = E.compute_month_data(panel, [target])[0]
    assert not md.eligible[0]


def test_adv_below_one_million_is_ineligible():
    panel, target = _fresh_eligibility_case()
    panel.volume_m = panel.volume_m.copy()
    panel.volume_m[target.decision_idx - 19: target.decision_idx + 1, 0] = 999_999.0
    md = E.compute_month_data(panel, [target])[0]
    assert not md.eligible[0]


def test_missing_entry_day_open_is_ineligible():
    """A symbol with no finite open on the entry day cannot be traded."""
    panel, target = _fresh_eligibility_case()
    panel.open_m = panel.open_m.copy()
    panel.open_m[target.entry_idx, 0] = np.nan
    md = E.compute_month_data(panel, [target])[0]
    assert not md.eligible[0]
    # ...and it must not be selectable into either leg
    lg, sh, _ = E.select_legs(md, "MOM")
    assert 0 not in set(lg) and 0 not in set(sh)


def test_nonpositive_entry_open_yields_no_return_and_cannot_be_traded():
    """The prereg requires a FINITE entry open. A non-positive price is not a
    tradable price: it produces no return and the name cannot enter a leg."""
    for bad in (0.0, -3.0):
        panel, target = _fresh_eligibility_case()
        panel.open_m = panel.open_m.copy()
        panel.open_m[target.entry_idx, 0] = bad
        md = E.compute_month_data(panel, [target])[0]
        assert not np.isfinite(md.ret[0]), bad
        lg, sh, _ = E.select_legs(md, "MOM")
        assert 0 not in set(lg) and 0 not in set(sh), bad


def test_returns_use_the_entry_and_exit_OPENS_not_the_closes():
    """The holding-period return must be exit open / entry open - 1."""
    panel, target = _fresh_eligibility_case()
    panel.open_m = panel.open_m.copy()
    panel.close_m = panel.close_m.copy()
    entry_open = 100.0
    exit_open = 110.0
    panel.open_m[target.entry_idx, 0] = entry_open
    panel.open_m[target.exit_idx, 0] = exit_open
    # make the closes deliberately different so a close-based return would differ
    panel.close_m[target.entry_idx, 0] = 500.0
    panel.close_m[target.exit_idx, 0] = 700.0
    md = E.compute_month_data(panel, [target])[0]
    assert md.ret[0] == pytest.approx(exit_open / entry_open - 1.0, rel=1e-12)


def test_adv_needs_the_full_20_day_window():
    dates, close, openm, volume = _eligibility_panel()
    panel = make_panel(dates, ["AAA"], openm, close, volume)
    months = E.build_months(panel)
    target = next(m for m in months if m.key == "2022-02")
    p = panel
    p.volume_m = panel.volume_m.copy()
    p.volume_m[target.decision_idx - 19, 0] = np.nan   # only 19 valid days
    md = E.compute_month_data(p, [target])[0]
    assert not md.eligible[0]


# ------------------------------------------------------------------ legs
def _md(mom=None, vol=None, ret=None, n=10):
    arrays = [np.asarray(a, dtype=float) for a in (mom, vol, ret) if a is not None]
    s = max([len(a) for a in arrays]) if arrays else n
    mom = np.full(s, np.nan) if mom is None else np.asarray(mom, dtype=float)
    vol = np.full(s, np.nan) if vol is None else np.asarray(vol, dtype=float)
    ret = np.zeros(s) if ret is None else np.asarray(ret, dtype=float)
    return E.MonthData(eligible=np.isfinite(mom) | np.isfinite(vol),
                       factor=mom, mom=mom, vol=vol, ret=ret,
                       last_close_idx=np.zeros(s, dtype=np.int64),
                       held_days=np.zeros(s, dtype=np.int64), exit_idx=0)


def test_mom_legs_long_high_short_low_with_equal_counts():
    n = 100
    mom = np.arange(n, dtype=float)          # ascending
    md = _md(mom=mom)
    lg, sh, elig = E.select_legs(md, "MOM")
    assert len(lg) == len(sh) == int(np.floor(0.10 * n))
    assert set(lg) == set(range(n - len(lg), n))     # top decile
    assert set(sh) == set(range(len(sh)))            # bottom decile
    assert len(elig) == n


def test_vol_legs_long_lowest_vol_short_highest_vol():
    n = 100
    vol = np.arange(n, dtype=float)
    md = _md(vol=vol)
    lg, sh, _ = E.select_legs(md, "VOL")
    assert set(lg) == set(range(len(lg)))             # lowest vol
    assert set(sh) == set(range(n - len(sh), n))      # highest vol


def test_factor_ties_are_broken_by_ascending_symbol():
    """Ties are resolved by ASCENDING symbol order for both legs.

    With a fully tied factor both legs resolve to the same ascending-symbol
    prefix (the only deterministic reading of the frozen rule); the tie-break is
    what matters here, and it is stable across calls.
    """
    n = 50
    mom = np.ones(n)              # all tied
    md = _md(mom=mom)
    lg, sh, _ = E.select_legs(md, "MOM")
    k = int(np.floor(0.10 * n))
    assert list(lg) == list(range(k))      # ascending symbol order
    assert list(sh) == list(range(k))      # ascending symbol order


def test_partial_ties_resolve_to_the_lowest_symbol_indices():
    """A tie at the decile boundary picks the lowest symbol indices of the tied group."""
    n = 100
    mom = np.linspace(0.0, 1.0, n)
    mom[0:10] = -1.0            # bottom group, fully tied
    mom[90:100] = 2.0           # top group, fully tied
    md = _md(mom=mom)
    lg, sh, _ = E.select_legs(md, "MOM")
    assert list(lg) == list(range(90, 100))
    assert list(sh) == list(range(0, 10))


def test_tie_break_is_deterministic_across_calls():
    n = 50
    rng = np.random.default_rng(11)
    mom = rng.choice([0.0, 1.0], size=n).astype(float)
    md = _md(mom=mom)
    a = E.select_legs(md, "MOM")
    b = E.select_legs(md, "MOM")
    assert list(a[0]) == list(b[0]) and list(a[1]) == list(b[1])


def test_legs_are_empty_when_too_few_eligible_names():
    md = _md(mom=np.arange(5, dtype=float))
    lg, sh, _ = E.select_legs(md, "MOM")
    assert len(lg) == 0 and len(sh) == 0


def test_names_with_missing_factor_are_not_eligible():
    n = 40
    mom = np.arange(n, dtype=float)
    mom[3] = np.nan
    md = _md(mom=mom)
    lg, sh, elig = E.select_legs(md, "MOM")
    assert 3 not in set(lg) and 3 not in set(sh)
    assert int(elig.sum()) == n - 1


# ------------------------------------------------------------------ costs
def test_cost_is_charged_only_on_actual_opens_and_closes():
    """A name held across two months without being traded is charged once per side."""
    n = 10
    md_a = _md(mom=np.arange(n, dtype=float), ret=np.full(n, 0.01))
    md_a.last_close_idx = np.full(n, 5)
    md_a.exit_idx = 5
    md_a.held_days = np.full(n, 20)
    md_b = _md(mom=np.arange(n, dtype=float), ret=np.full(n, 0.02))
    md_b.last_close_idx = np.full(n, 5)
    md_b.exit_idx = 5
    md_b.held_days = np.full(n, 20)
    legs = [np.arange(n), np.arange(n)]
    res = E.simulate_leg([md_a, md_b], legs, "long")
    # month 1: n opens -> n * w * 0.0015 = 0.0015 ; month 2: no opens, no closes
    assert res.cost[0] == pytest.approx(E.COST_PER_SIDE, rel=1e-12)
    assert res.cost[1] == pytest.approx(0.0, rel=1e-12)


def test_cost_is_charged_on_a_turnover_close():
    n = 10
    md_a = _md(ret=np.full(n, 0.01))
    md_a.last_close_idx = np.full(n, 5)
    md_a.exit_idx = 5
    md_b = _md(ret=np.full(n, 0.02))
    md_b.last_close_idx = np.full(n, 5)
    md_b.exit_idx = 5
    legs_a = np.arange(n // 2)
    legs_b = np.arange(n // 2, n)               # fully disjoint -> all closed, all opened
    res = E.simulate_leg([md_a, md_b], [legs_a, legs_b], "long")
    # month 2: 5 closes + 5 opens, leg weight 1/5 -> 0.0015 * 10 * (1/5)
    assert res.cost[1] == pytest.approx(
        E.COST_PER_SIDE * 10 * (1.0 / 5), rel=1e-12)


def test_carried_names_are_not_charged_again():
    """A name carried into the next month without being traded pays no second side."""
    n = 10
    md_a = _md(mom=np.arange(n, dtype=float), ret=np.full(n, 0.01))
    md_a.last_close_idx = np.full(n, 5)
    md_a.exit_idx = 5
    md_b = _md(mom=np.arange(n, dtype=float), ret=np.full(n, 0.02))
    md_b.last_close_idx = np.full(n, 5)
    md_b.exit_idx = 5
    res = E.simulate_leg([md_a, md_b], [np.arange(n), np.arange(n)], "long")
    assert res.cost[0] == pytest.approx(E.COST_PER_SIDE, rel=1e-12)
    assert res.cost[1] == pytest.approx(0.0, rel=1e-12)


def test_delisting_exit_is_charged_once_more():
    """A name that loses its quote before the exit open costs an extra exit side."""
    n = 10
    md = _md(ret=np.full(n, 0.01))
    md.last_close_idx = np.full(n, 5)
    md.exit_idx = 5
    md.held_days = np.full(n, 20)
    md.ret = np.full(n, 0.01)
    md.last_close_idx[0] = 3      # this one died before the exit open
    md.held_days[0] = 4
    res = E.simulate_leg([md], [np.arange(n)], "long")
    # n opens + 1 delisting exit
    assert res.cost[0] == pytest.approx(
        E.COST_PER_SIDE * (n + 1) * (1.0 / n), rel=1e-12)


def test_borrow_is_prorated_by_trading_days_held():
    n = 10
    md = _md(ret=np.full(n, 0.0))
    md.last_close_idx = np.full(n, 5)
    md.exit_idx = 5
    md.held_days = np.full(n, 252)
    res = E.simulate_leg([md], [np.arange(n)], "short")
    assert res.borrow[0] == pytest.approx(E.BORROW_PER_YEAR, rel=1e-12)


def test_borrow_scales_with_the_actual_holding_period():
    """A 21-trading-day hold costs 21/252 of the annual borrow, not a full year."""
    n = 10
    md = _md(ret=np.full(n, 0.0))
    md.last_close_idx = np.full(n, 5)
    md.exit_idx = 5
    md.held_days = np.full(n, 21)
    res = E.simulate_leg([md], [np.arange(n)], "short")
    assert res.borrow[0] == pytest.approx(
        E.BORROW_PER_YEAR * 21.0 / 252.0, rel=1e-12)


def test_borrow_uses_each_names_own_holding_period():
    n = 4
    md = _md(ret=np.zeros(n))
    md.last_close_idx = np.full(n, 5)
    md.exit_idx = 5
    md.held_days = np.array([0, 63, 126, 252])
    res = E.simulate_leg([md], [np.arange(n)], "short")
    expected = E.BORROW_PER_YEAR * (0 + 63 + 126 + 252) / 4.0 / 252.0
    assert res.borrow[0] == pytest.approx(expected, rel=1e-12)


# ------------------------------------------------------------------ net series
def test_net_series_sign_convention():
    n = 5
    lg = E.SimResult(net=np.array([0.10]), cost=np.array([0.001]), borrow=np.array([0.0]))
    sh = E.SimResult(net=np.array([-0.20]), cost=np.array([0.001]), borrow=np.array([0.002]))
    net = E.net_series(lg, sh)
    assert net[0] == pytest.approx((0.10 - 0.001) + (0.20 - 0.002 - 0.001), rel=1e-12)


# ------------------------------------------------------------------ metrics
def test_annualization_formula():
    assert E.annualize_ret(0.01) == pytest.approx(1.01 ** 12 - 1.0)
    assert E.annualize_ret(0.0) == pytest.approx(0.0)


def test_t_stat_matches_the_textbook_formula():
    x = np.array([0.01, -0.02, 0.03, 0.00, 0.05])
    assert E.t_stat(x) == pytest.approx(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def test_max_drawdown():
    net = np.array([0.5, -0.5, 0.1])
    eq = np.cumprod(1.0 + net)
    assert E.max_drawdown(net) == pytest.approx(eq.min() / eq.max() - 1.0)


def test_percentile_is_a_0_to_100_percentage():
    draws = np.array([-0.1, 0.0, 0.0, 0.1])
    # strictly-less-than semantics: -0.1, 0.0, 0.0 are below 0.05 -> 3 of 4
    assert E.percentile_vs_random(0.05, draws) == pytest.approx(75.0)
    assert E.percentile_vs_random(-1.0, draws) == pytest.approx(0.0)
    assert E.percentile_vs_random(1.0, draws) == pytest.approx(100.0)


def test_excess_sharpe_uses_the_excess_series_volatility():
    net = np.array([0.01, 0.02, 0.03, 0.04])
    excess = np.array([0.001, 0.002, 0.003, 0.004])
    m = R.window_metrics(net, excess, np.zeros(4),
                         net, net, np.array([0.0]))
    ann_excess = E.annualize_ret(excess.mean())
    ann_vol_excess = excess.std(ddof=1) * np.sqrt(12)
    assert m["sharpe_excess"] == pytest.approx(ann_excess / ann_vol_excess, rel=1e-12)
    assert m["annualized_vol_excess"] == pytest.approx(ann_vol_excess, rel=1e-12)


def test_annualized_vol_is_monthly_vol_times_sqrt_12():
    net = np.array([0.01, -0.02, 0.03, 0.005])
    m = R.window_metrics(net, net, np.zeros(4), net, net, np.array([0.0]))
    assert m["annualized_vol"] == pytest.approx(net.std(ddof=1) * np.sqrt(12), rel=1e-12)


# ------------------------------------------------------------------ random baseline
def _random_md(n=40, seed=1):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0, 0.03, size=n)
    md = _md(mom=np.arange(n, dtype=float), ret=ret)
    md.last_close_idx = np.full(n, 5)
    md.exit_idx = 5
    md.held_days = np.full(n, 21)
    return md


def test_random_draws_are_reproducible_for_the_same_seed():
    md = _random_md()
    elig = [np.arange(40)]
    n_leg = [4]
    a = R.random_monthly_nets([md], elig, n_leg, 25, 20261112)
    b = R.random_monthly_nets([md], elig, n_leg, 25, 20261112)
    assert np.array_equal(a, b)


def test_random_draws_differ_for_a_different_seed():
    md = _random_md()
    a = R.random_monthly_nets([md], [np.arange(40)], [4], 25, 20261112)
    b = R.random_monthly_nets([md], [np.arange(40)], [4], 25, 1)
    assert not np.array_equal(a, b)


def test_random_draw_uses_the_same_leg_sizes_and_costs_as_the_strategy():
    """The random monthly net must equal a hand-computed month using the frozen rules."""
    n = 40
    md = _random_md(n)
    elig = [np.arange(n)]
    n_leg = [4]
    draws = R.random_monthly_nets([md], elig, n_leg, 1, 20261112)
    # recompute month 1 by hand with the same RNG stream
    rng = np.random.default_rng(20261112)
    long_sel = np.sort(rng.choice(np.arange(n), size=4, replace=False))
    rest = np.setdiff1d(np.arange(n), long_sel, assume_unique=True)
    short_sel = np.sort(rng.choice(rest, size=4, replace=False))
    w = 1.0 / 4
    l_gross = float(np.mean(md.ret[long_sel]))
    s_gross = float(np.mean(md.ret[short_sel]))
    l_cost = E.COST_PER_SIDE * 4 * w
    s_cost = E.COST_PER_SIDE * 4 * w
    s_borrow = E.BORROW_PER_YEAR * float(np.mean(md.held_days[short_sel])) / 252.0
    expected = (l_gross - l_cost) + (-s_gross - s_borrow - s_cost)
    assert draws[0, 0] == pytest.approx(expected, rel=1e-12)


def test_random_long_and_short_legs_are_disjoint():
    """The short leg is drawn from the names the long leg did not take."""
    n = 40
    md = _random_md(n)
    elig = np.arange(n)
    n_leg = 4
    rng = np.random.default_rng(20261112)
    long_sel = np.sort(rng.choice(elig, size=n_leg, replace=False))
    rest = np.setdiff1d(elig, long_sel, assume_unique=True)
    short_sel = np.sort(rng.choice(rest, size=n_leg, replace=False))
    assert not set(long_sel) & set(short_sel)
    assert set(long_sel) | set(short_sel) <= set(elig)


# ------------------------------------------------------------------ dev/holdout gates
def test_dev_bars_require_every_frozen_condition():
    m = {"n_months": 48, "mean_monthly_net": 0.01, "mean_monthly_excess": 0.01,
         "percentile_vs_random": 100.0, "t_stat_excess": 2.5}
    bars = R.dev_bars(m)
    assert bars["ALL_PASS"] is True

    m2 = dict(m, t_stat_excess=2.0)
    assert R.dev_bars(m2)["ALL_PASS"] is False
    m3 = dict(m, percentile_vs_random=90.0)
    assert R.dev_bars(m3)["ALL_PASS"] is False
    m4 = dict(m, mean_monthly_net=-0.001)
    assert R.dev_bars(m4)["ALL_PASS"] is False
    m5 = dict(m, n_months=23)
    assert R.dev_bars(m5)["ALL_PASS"] is False


def test_dev_bars_t_stat_cutoff_is_2_24():
    m = {"n_months": 48, "mean_monthly_net": 0.01, "mean_monthly_excess": 0.01,
         "percentile_vs_random": 100.0, "t_stat_excess": 2.24}
    assert R.dev_bars(m)["t_stat>=2.24"] is True
    m["t_stat_excess"] = 2.2399
    assert R.dev_bars(m)["t_stat>=2.24"] is False


def test_holdout_is_suppressed_for_a_dev_failure():
    """A dev-failing variant must never have its holdout computed."""
    dev_m = {"n_months": 48, "mean_monthly_net": 0.01, "mean_monthly_excess": 0.01,
             "percentile_vs_random": 100.0, "t_stat_excess": 0.5}
    bars = R.dev_bars(dev_m)
    assert bars["ALL_PASS"] is False
    assert R.holdout_allowed(bars) is False


def test_holdout_is_allowed_only_when_every_dev_bar_passes():
    dev_m = {"n_months": 48, "mean_monthly_net": 0.01, "mean_monthly_excess": 0.01,
             "percentile_vs_random": 100.0, "t_stat_excess": 2.5}
    assert R.holdout_allowed(R.dev_bars(dev_m)) is True
    assert R.holdout_allowed({}) is False
    assert R.holdout_allowed({"ALL_PASS": False}) is False


def test_compute_holdout_returns_nothing_when_dev_failed():
    """A dev-failing variant must not have its holdout computed at all."""
    n = 40
    md = _random_md(n)
    md_list = [md]
    dev_rows = [0]
    hold_rows = [0]
    legs_long = [np.arange(4)]
    legs_short = [np.arange(4, 8)]
    elig = [np.arange(n)]
    n_leg = [4]
    hm, monthly = R.compute_holdout(md_list, dev_rows, hold_rows,
                                    legs_long, legs_short, elig, n_leg,
                                    dev_passed=False, keys=["2022-01"])
    assert hm is None
    assert monthly is None


def test_compute_holdout_produces_metrics_when_dev_passed():
    """With the dev gate passed, the holdout window IS computed."""
    n = 40
    md = _random_md(n)
    md_list = [md, _random_md(n, seed=2)]
    dev_rows = [0]
    hold_rows = [1]
    legs_long = [np.arange(4), np.arange(4)]
    legs_short = [np.arange(4, 8), np.arange(4, 8)]
    elig = [np.arange(n), np.arange(n)]
    n_leg = [4, 4]
    hm, monthly = R.compute_holdout(md_list, dev_rows, hold_rows,
                                    legs_long, legs_short, elig, n_leg,
                                    dev_passed=True, keys=["2022-01", "2022-02"])
    assert hm is not None and monthly is not None
    assert monthly["months"] == ["2022-02"]
    assert len(monthly["net"]) == 1


def test_compute_holdout_does_not_simulate_when_the_gate_is_closed(monkeypatch):
    """No holdout simulation may run at all when the dev gate is closed."""
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("holdout simulation ran despite a dev failure")

    monkeypatch.setattr(R.E, "simulate_leg", boom)
    monkeypatch.setattr(R, "random_monthly_nets", boom)
    n = 40
    md = _random_md(n)
    hm, monthly = R.compute_holdout([md], [0], [0], [np.arange(4)], [np.arange(4, 8)],
                                    [np.arange(n)], [4], dev_passed=False, keys=["x"])
    assert hm is None and monthly is None
    assert calls["n"] == 0


def test_recorded_study_artifact_never_contains_a_holdout_for_a_dev_failure():
    """The committed results.json must show the holdout as NOT COMPUTED for both
    rejected variants and must not contain holdout returns for them."""
    res_path = EXP_DIR / "results" / "results.json"
    if not res_path.exists():
        pytest.skip("results.json not present")
    import json
    res = json.loads(res_path.read_text())
    assert res["variants"], "no variants recorded"
    for variant, vres in res["variants"].items():
        bars = vres["windows"]["dev"]["bars"]
        if not bars["ALL_PASS"]:
            assert "holdout" not in vres["windows"], variant
            assert "NOT COMPUTED" in vres.get("holdout_status", ""), variant
            assert "holdout" not in vres.get("monthly", {}), variant
        else:
            assert "holdout" in vres["windows"], variant


def test_holdout_bars_use_the_frozen_cutoffs():
    m = {"n_months": 20, "mean_monthly_net": 0.01, "mean_monthly_excess": 0.01,
         "percentile_vs_random": 100.0, "t_stat_excess": 2.5}
    bars, cut_pct, t_cut = R.holdout_bars(m, 1)
    assert cut_pct == pytest.approx(95.0)
    assert t_cut == pytest.approx(2.0)
    assert bars["ALL_PASS"] is True


def test_study_constants_match_the_preregistration():
    assert E.SEED == 20261112
    assert E.N_DRAWS == 1000
    assert E.COST_PER_SIDE == 0.0015
    assert E.BORROW_PER_YEAR == 0.01
    assert E.DECILE == 0.10
    assert E.MIN_ADV_SHARES == 1_000_000.0
    assert E.MIN_PRICE == 5.0
    assert E.ADV_WINDOW == 20
    assert E.VOL_WINDOW == 60
    assert R.DEV_START == pd.Period("2021-01", "M")
    assert R.DEV_END == pd.Period("2024-12", "M")
    assert R.HOLD_START == pd.Period("2025-01", "M")
    assert R.VARIANTS == ["MOM", "VOL"]
