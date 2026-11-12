# LONGSHORT-001 — Results: dollar-neutral monthly long/short factor test

**RESEARCH ONLY / PAPER ONLY.** No orders of any kind were placed, modified or
cancelled; no brokerage credentials were used; no trading database was written or
opened with `sqlite3`; no production code, config, `.env`, secret, timer or service
was touched. Nothing was merged.

**CONFLICT WITH FROZEN PLAN: none.** The frozen pre-registration
(`experiments/longshort_001/PREREGISTRATION.md`, anchor commit `8f750ef`) was read
first and **not edited**; every rule below was verified against it item by item.

---

## 1. Run identity

| Item | Value |
|---|---|
| Panel | `/root/.openclaw/worktrees/STRAT-027/experiments/strat_027/data/panel.parquet` (read-only, read in place) |
| Panel sha256 | `31a2b0eaf4e87260fe75897529c0b567f7fc022f7e173bbfc951182df9990930` — **matches the frozen expectation** (`31a2b0ea…0930`) |
| Panel copy in this worktree | **none** (read in place from the STRAT-027 worktree) |
| Seed | `20261112` (`numpy.random.default_rng`, one stream) |
| Random draws | `1000` |
| Cost | 0.15 % per side (0.30 % round trip), charged only on real opens/closes |
| Borrow | 1.0 %/yr on the short notional, prorated `days_held/252` |
| Windows | DEV = 48 decision months 2021-01…2024-12; HOLDOUT = 20 months 2025-01…2026-08 |
| Variants | MOM (12-1 momentum, recent month skipped), VOL (60d realized vol) |
| Bonferroni | family alpha 0.05 / 2 = 0.025 |

---

## 2. Verdicts (frozen bars)

Both variants were **REJECTED at dev**. Because a dev failure means the holdout is
**never computed**, **no holdout window exists for either variant** — see §6.

### DEV window (48 months, 2021-01 … 2024-12)

| Frozen dev bar | MOM | VOL |
|---|---|---|
| n months ≥ 24 | **PASS** (48) | **PASS** (48) |
| mean monthly net > 0 | **PASS** (+0.015699) | **PASS** (+0.011462) |
| mean monthly excess > 0 | **PASS** (+0.021933) | **PASS** (+0.017952) |
| percentile ≥ 97.5 | **PASS** (100.0) | **PASS** (100.0) |
| monthly t on excess ≥ 2.24 | **FAIL** (2.1814) | **FAIL** (1.0633) |
| **DEV VERDICT** | **REJECTED** | **REJECTED** |

### HOLDOUT window

| Variant | Holdout status |
|---|---|
| MOM | **NOT COMPUTED** — variant failed the dev gate (frozen rule §7 of the plan) |
| VOL | **NOT COMPUTED** — variant failed the dev gate (frozen rule) |

`results/results.json` contains **no** `holdout` key and **no** holdout monthly
series for either variant; both carry `holdout_status = "NOT COMPUTED (variant
failed the dev bar; frozen rule)"`. `dev_passers = []`. `final_verdict =
{"MOM": "REJECTED", "VOL": "REJECTED"}`.

---

## 3. Metric table — DEV window (the only computed window)

| Metric | MOM | VOL |
|---|---|---|
| n months | 48 | 48 |
| mean monthly net | 0.01569852 | 0.01146202 |
| **annualized net** ((1+mean)^12−1) | **0.20552942** | **0.14655512** |
| mean monthly expected-random | −0.00623415 | −0.00649000 |
| **annualized expected-random** | **−0.07229734** | **−0.07515934** |
| mean monthly excess | 0.02193267 | 0.01795202 |
| **annualized excess** ((1+mean_excess)^12−1) | **0.29738059** | **0.23802005** |
| monthly vol (net, ddof=1) | 0.06977152 | 0.11708273 |
| annualized vol (net) = monthly vol × √12 | 0.24169562 | 0.40558646 |
| monthly vol (excess, ddof=1) | 0.06965797 | 0.11696915 |
| annualized vol (excess) | 0.24130228 | 0.40519303 |
| **Sharpe, excess-based** = annualized excess / annualized vol of the **monthly excess** series | **1.23239857** | **0.58742385** |
| Sharpe, net-based = annualized net / annualized vol of the **monthly net** series | 0.85036470 | 0.36134126 |
| max drawdown (cumprod(1+r) incl. initial equity 1.0) | −0.27941131 | −0.46586535 |
| monthly win rate (net > 0) | 0.66666667 (32/48) | 0.56250000 (27/48) |
| mean **long-leg** contribution per month | 0.01477072 | 0.00790110 |
| mean **short-leg** contribution per month | 0.00092780 | 0.00356091 |
| monthly t-statistic on excess | 2.18143010 | 1.06331634 |
| percentile vs the 1000-draw random cloud | 100.0 | 100.0 |

Annualization is geometric: `(1 + mean_monthly)^12 − 1` for net, expected-random and
excess; volatility is the sample std (ddof=1) of the monthly series, annualized by
×√12. `t = mean(excess) / (std(excess, ddof=1)/√n)`. Percentile = % of the 1000
full-window draws whose mean net over the same window is **strictly less** than the
strategy's mean net over that window.

Note on the percentile: the strategy's dev mean net beat **all 1000** random
portfolios (100.0) — the *level* of the random cloud is negative (the random
dollar-neutral decile portfolios lost money on average over the panel). The
statistical bar is the t-statistic on excess, and that is where both variants fail.

---

## 4. Frozen-plan verification (item by item)

Legend: **OK** = implementation matches the frozen plan.

| # | Frozen rule | Result | Evidence |
|---|---|---|---|
| 1 | 12-1 momentum, most recent month **skipped**: `MOM = P(end M-2)/P(end M-13) − 1` | **OK** | `engine.py: p1_period = m.period - 2`, `p12_period = m.period - 13`; test `test_mom_uses_end_of_m_minus_2_over_end_of_m_minus_13` (asserts the M-1 value is NOT used); mutation M01 caught |
| 2 | No look-ahead: all factor inputs end at the decision close | **OK** | Factor windows use `decision_idx` and month-end indices `< decision_idx` (`j12 < j1 < i`); mutation M16 (decision close from the entry day) caught by the test suite |
| 3 | Eligibility: 20-session mean volume ≥ 1,000,000 | **OK** | `adv_count >= 20` and `adv >= 1e6`; test `test_adv_needs_the_full_20_day_window`, `test_adv_below_one_million_is_ineligible`; mutation M09 caught |
| 4 | Eligibility: decision close ≥ $5.00 | **OK** | `dec_close >= MIN_PRICE`; test `test_price_below_five_at_the_decision_close_is_ineligible`; mutation M15 caught |
| 5 | Eligibility: **valid finite open on the entry day** | **FIXED (was a violation)** — see §5 | `has_entry_open = isfinite(entry_open) & (entry_open > 0)` added to the base eligibility set; test `test_missing_entry_day_open_is_ineligible` (was failing, now passes); mutation M12 caught |
| 6 | History rules: MOM = valid closes at the decision day, end of M-2 and end of M-13; VOL = ≥ 61 valid closes | **OK** | `mom_ok` requires finite decision close + both month-end closes; VOL uses 60 daily returns (`daily_ret[i-59:i+1]`, i.e. 61 closes) with all-finite requirement; tests `test_mom_is_nan_without_enough_history`, `test_vol_is_sample_std_of_60_daily_returns_ending_at_decision_close` |
| 7 | Dollar-neutral: long +1.0 / short −1.0, gross 2.0 | **OK** | `net_series`: `long_pnl + short_pnl` with the short leg sign-flipped; test `test_net_series_sign_convention`; mutation M02 caught |
| 8 | Equal-count deciles: `n_leg = floor(0.10 × n_eligible)`, min 1, skip a month with < 20 eligible | **OK** | `select_legs`; tests `test_mom_legs_long_high_short_low_with_equal_counts`, `test_legs_are_empty_when_too_few_eligible_names`; mutation M08 caught |
| 9 | Equal weight within each leg (1/n_leg), re-set at each rebalance | **OK** | `w = 1.0 / n` per month, no drift carry; covered by the cost/return tests |
| 10 | Fill at the **open of the first trading day of month M**, exit at the open of the first trading day of M+1 | **OK** | `build_months` (entry_idx = first trading day of M, exit_idx = first trading day of M+1); test `test_returns_use_the_entry_and_exit_OPENS_not_the_closes`; mutation M04 caught |
| 11 | Delisting mid-month: exit at the last available close, slot holds cash, no look-ahead replacement | **OK** | `compute_month_data` falls back to the last finite close inside `[entry, exit)`; the slot earns 0 afterwards and is **not** replaced; the delisting exit is charged one extra cost side; test `test_delisting_exit_is_charged_once_more`; mutation M13 caught |
| 12 | Cost 0.30 % round trip, charged only on real opens/closes, carried names not recharged | **OK** | `simulate_leg`: `COST_PER_SIDE × (opened + closed + delisting-exits) × w`; tests `test_cost_is_charged_only_on_actual_opens_and_closes`, `test_carried_names_are_not_charged_again`, `test_cost_is_charged_on_a_turnover_close`; mutations M03/M19 caught |
| 13 | Borrow 1.0 %/yr on the short notional, prorated by trading days held | **OK** | `BORROW_PER_YEAR × (days_held/252) × w`; tests `test_borrow_is_prorated_by_trading_days_held`, `test_borrow_scales_with_the_actual_holding_period`, `test_borrow_uses_each_names_own_holding_period`; mutations M10/M20 caught |
| 14 | Identical eligible universe and identical leg counts for every random draw | **OK** | the same `elig` / `n_leg` arrays produced by `select_legs` feed `random_monthly_nets`; test `test_random_draw_uses_the_same_leg_sizes_and_costs_as_the_strategy` |
| 15 | 1000 draws, seed 20261112, order d→months→long→short, one `default_rng` stream | **OK** | `random_monthly_nets`; tests `test_random_draws_are_reproducible_for_the_same_seed`, `test_study_constants_match_the_preregistration`; mutation M05 caught |
| 16 | Percentile = % of the 1000 draws strictly below the strategy's window mean | **OK** | `percentile_vs_random` uses `draw_means < strategy_mean`; test `test_percentile_is_a_0_to_100_percentage`; mutation M11 caught |
| 17 | Bonferroni dev bars (n≥24, net>0, excess>0, pct≥97.5, t≥2.24) | **OK** | `dev_bars`; tests `test_dev_bars_require_every_frozen_condition`, `test_dev_bars_t_stat_cutoff_is_2_24`; mutation M18 caught |
| 18 | Holdout gate: holdout computed ONCE and only for dev passers | **OK** | `holdout_allowed` + `compute_holdout` returns `(None, None)` before simulating anything; tests `test_holdout_is_suppressed_for_a_dev_failure`, `test_compute_holdout_does_not_simulate_when_the_gate_is_closed`, `test_recorded_study_artifact_never_contains_a_holdout_for_a_dev_failure`; mutation M06 caught |
| 19 | Windows: DEV 2021-01…2024-12 (48 m, Jan-2025 exit included); HOLDOUT 2025-01…2026-08 (20 m) | **OK** | `DEV_START/DEV_END/HOLD_START` + `exit_date <= 2026-09-29`; `months_used = {total 79, dev 48, holdout 20}`; mutation M17 caught |
| 20 | Annualization / Sharpe / t / max-drawdown definitions | **OK** | `annualize_ret`, `t_stat`, `max_drawdown`; tests `test_annualization_formula`, `test_excess_sharpe_uses_the_excess_series_volatility`, `test_annualized_vol_is_monthly_vol_times_sqrt_12`, `test_max_drawdown`; mutations M14 caught |
| 21 | No SPY filter, no market timing, no beta/sector neutralization, no quality/value factor, no factor combination, no extra variants | **OK** | only `MOM` and `VOL` exist in the code (`VARIANTS`); SPY appears only as an ordinary eligible name (it can be traded by the rules, like any other symbol) |
| 22 | Delisted / inactive names are kept, not dropped | **OK** | the panel is used whole (6,258 symbols incl. 590 inactive); no symbol filter is applied |

---

## 5. The one violation found and fixed

**Violation (frozen plan §3, "Tradability"):** the eligibility set computed by
`engine.compute_month_data` did **not** include the preregistered *entry-day open*
screen ("a finite open exists on the entry day"). The screen was only applied
implicitly downstream, because `select_legs` drops names whose holding-period
return is not finite. The plan lists the screen as part of the eligibility rule
set, and the preregistered eligible universe is what defines `n_leg` and what the
random draws are sampled from, so it must be explicit in the eligibility set.

**Fix (code only; the plan was not touched):**

```python
# Tradability screen (frozen prereg section 3): a finite, strictly
# positive open must exist on the ENTRY day.
entry_open = panel.open_m[e]
has_entry_open = np.isfinite(entry_open) & (entry_open > 0)
base_eligible = ( ... & has_entry_open)
```

**Re-run evidence:** the effective eligible universe is unchanged (the downstream
filter was equivalent), so the study was re-run end-to-end and the artifacts are
**byte-identical to the pre-fix run** (verified field-by-field, `sample_months`
excluded because it is regenerated):

```
python3 experiments/longshort_001/run_study.py     # 1m57s
# identical(minus sample_months): True
# MOM t_stat_excess 2.181430099500538 -> 2.181430099500538
# VOL t_stat_excess 1.063316337756552 -> 1.063316337756552
```

The unit test `tests/test_longshort_001.py::test_missing_entry_day_open_is_ineligible`
was **failing before the fix** and passes after it — the fix is covered by a test,
and mutation **M12** (re-removing the screen) is caught by that same test.

---

## 6. Holdout discipline (proof)

- `run_study.py` computes the holdout only inside `compute_holdout(...)`, which
  returns `(None, None)` **before any simulation** when `dev_passed` is False
  (`holdout_allowed(dev_bars)` requires `ALL_PASS`).
- Both variants failed dev → `dev_passers = []` → `holdout` key absent from
  `results["variants"][MOM]["windows"]` and `... [VOL]["windows"]`, and absent from
  `results["variants"][*]["monthly"]`.
- Tests proving this: `test_holdout_is_suppressed_for_a_dev_failure`,
  `test_holdout_is_allowed_only_when_every_dev_bar_passes`,
  `test_compute_holdout_returns_nothing_when_dev_failed`,
  `test_compute_holdout_does_not_simulate_when_the_gate_is_closed` (monkeypatches
  `simulate_leg` and `random_monthly_nets` to raise if the holdout path is entered)
  and `test_recorded_study_artifact_never_contains_a_holdout_for_a_dev_failure`
  (reads the committed `results.json`).
- Mutation **M06** (holdout gate removed) is caught by the gate tests.
- **No holdout return exists or is reported for any variant.**

---

## 7. Independent recomputation of sample months

`experiments/longshort_001/check_sample_months.py` recomputes the dev window with
**plain pandas** (wide pivots of the raw parquet — no import of the simulation
code): eligible universe, MOM/VOL factors, leg membership, entry/exit prices,
per-name returns, trading days held, trading costs, borrow and the monthly net.
`experiments/longshort_001/verify_sample_months.py` then compares that independent
result against the engine path, name by name and number by number.

```
python3 experiments/longshort_001/check_sample_months.py   # -> results/independent_check.json
python3 experiments/longshort_001/verify_sample_months.py  # -> results/sample_month_verification.json
```

Output:

```
MOM 2021-01 MATCH | net engine 0.155658942357 independent 0.155658942357
MOM 2022-12 MATCH | net engine 0.014438859220 independent 0.014438859220
MOM 2024-12 MATCH | net engine -0.053514975725 independent -0.053514975725
VOL 2021-01 MATCH | net engine -0.420452850449 independent -0.420452850449
VOL 2022-12 MATCH | net engine 0.005971219125 independent 0.005971219125
VOL 2024-12 MATCH | net engine -0.013677799762 independent -0.013677799762
ALL SAMPLE MONTHS MATCH: True
```

Three sample months (first, middle, last of the dev window) × 2 variants = 6
independent recomputations, all matching to < 1e-9, including leg membership sets,
leg gross returns, costs, borrow and the monthly net.

Spot check (MOM 2021-01): 947 eligible names, n_leg = 94; long leg gross +0.318570,
cost −0.001500, long P&L +0.317070; short leg gross +0.159145 → short P&L
−0.159145 − 0.000750 borrow − 0.001516 cost = −0.161411; **net +0.155659** — the
same figure from both paths. Long leg head: NVAX entry open 112.70 → exit open
242.52 (+115.19 %), VXRT 5.7726 → 14.00, BTBT 25.70 → 19.13. Short leg head: UCO
9.085 → 10.48, NOVNQ 8.90 → 11.90, PBF 6.42 → 7.903.

The VOL 2021-01 long leg also exercises the delisting rule: **TIF** has no exit open
and is exited at its last available close after 3 trading days (its slot then holds
cash); the independent checker reproduces that name and price independently.

---

## 8. Mutation suite

Harness: `/tmp/ls001_mut/run_mutations.py`. For each mutation the pristine
`engine.py` / `run_study.py` / `results/` are copied into a **scratch tree**
(`/tmp/ls001_mut/scratch`), ONE frozen rule is deliberately broken there, and the
**committed** test suite is run against the mutant. The committed source is never
modified and no mutant is ever left behind. Baseline (pristine) run: **green**.

| ID | Mutation | Caught by |
|---|---|---|
| M01 | 12-1 skip removed (MOM uses month M-1 instead of M-2) | `test_mom_uses_end_of_m_minus_2_over_end_of_m_minus_13` |
| M02 | Short-leg sign flipped in `net_series` | `test_net_series_sign_convention` |
| M03 | Trading cost constant set to 0 | `test_study_constants_match_the_preregistration` |
| M04 | Holding return computed from closes instead of opens | `test_returns_use_the_entry_and_exit_OPENS_not_the_closes` |
| M05 | Seed changed (20261112 → 20261113) | `test_study_constants_match_the_preregistration` |
| M06 | Holdout gate removed (holdout computed for a dev failure) | `test_holdout_is_allowed_only_when_every_dev_bar_passes`, `test_holdout_is_suppressed_for_a_dev_failure` |
| M07 | VOL leg direction flipped | `test_vol_legs_long_lowest_vol_short_highest_vol` |
| M08 | Leg size = whole eligible universe (not a decile) | `test_factor_ties_are_broken_by_ascending_symbol`, `test_mom_legs_long_high_short_low_with_equal_counts`, `test_partial_ties_resolve_to_the_lowest_symbol_indices` |
| M09 | ADV window 20 → 10 sessions | `test_adv_needs_the_full_20_day_window`, `test_study_constants_match_the_preregistration` |
| M10 | Borrow fee not prorated (full year every month) | `test_borrow_scales_with_the_actual_holding_period`, `test_borrow_uses_each_names_own_holding_period` |
| M11 | Percentile comparison direction inverted | `test_percentile_is_a_0_to_100_percentage` |
| M12 | Entry-day open screen removed | `test_missing_entry_day_open_is_ineligible` |
| M13 | Delisting exit no longer charged a cost side | `test_delisting_exit_is_charged_once_more` |
| M14 | Annualization linear instead of geometric | `test_annualization_formula` |
| M15 | Minimum price screen removed | `test_price_below_five_at_the_decision_close_is_ineligible`, `test_study_constants_match_the_preregistration` |
| M16 | Look-ahead: decision close taken from the entry day | `test_price_below_five_at_the_decision_close_is_ineligible` |
| M17 | Dev window shortened (2024-12 → 2024-06) | `test_study_constants_match_the_preregistration` |
| M18 | Dev t-stat bar loosened (2.24 → 1.0) | `test_dev_bars_require_every_frozen_condition`, `test_dev_bars_t_stat_cutoff_is_2_24` |
| M19 | Trading-cost charge zeroed while the constant stays | `test_carried_names_are_not_charged_again`, `test_cost_is_charged_on_a_turnover_close`, `test_cost_is_charged_only_on_actual_opens_and_closes`, `test_delisting_exit_is_charged_once_more` |
| M20 | Borrow charge zeroed while the constant stays | `test_borrow_is_prorated_by_trading_days_held`, `test_borrow_scales_with_the_actual_holding_period`, `test_borrow_uses_each_names_own_holding_period` |

**Survivors: none (20/20 caught).** No test was weakened, skipped or deleted to
make this pass; the suite is exactly the committed one.

---

## 9. Exact commands run and their outputs

```
$ cd /root/.openclaw/worktrees/LONGSHORT-001
$ sha256sum /root/.openclaw/worktrees/STRAT-027/experiments/strat_027/data/panel.parquet
31a2b0eaf4e87260fe75897529c0b567f7fc022f7e173bbfc951182df9990930  .../panel.parquet

$ python3 -m pytest tests/test_longshort_001.py -v      # 45 passed, 2 warnings in ~4s
$ python3 experiments/longshort_001/run_study.py        # ~2 min -> results/results.json
$ python3 experiments/longshort_001/check_sample_months.py   # ~61 s -> results/independent_check.json
$ python3 experiments/longshort_001/verify_sample_months.py  # ALL SAMPLE MONTHS MATCH: True
$ python3 /tmp/ls001_mut/run_mutations.py              # 20/20 mutants caught, 0 survivors
```

`run_study.py` console summary:

```
panel: <T> trading days, 6258 symbols
usable months: 79 (… .. 2026-08)
=== variant MOM === running 1000 random draws (dev window) ...
dev FAILED -> holdout NOT COMPUTED (frozen rule)
=== variant VOL === running 1000 random draws (dev window) ...
dev FAILED -> holdout NOT COMPUTED (frozen rule)
wrote .../results/results.json
{"MOM": "REJECTED", "VOL": "REJECTED"}
```

---

## 10. Artifacts

| File | Content |
|---|---|
| `experiments/longshort_001/PREREGISTRATION.md` | frozen plan — **unchanged** (not in the commit diff) |
| `experiments/longshort_001/engine.py` | frozen-rule engine (entry-day-open screen fixed, §5) |
| `experiments/longshort_001/run_study.py` | study runner with the holdout gate |
| `experiments/longshort_001/check_sample_months.py` | independent (plain-pandas) recomputation |
| `experiments/longshort_001/verify_sample_months.py` | engine vs independent cross-check |
| `experiments/longshort_001/results/results.json` | full study output (dev only) |
| `experiments/longshort_001/results/independent_check.json` | independent recomputation |
| `experiments/longshort_001/results/sample_month_verification.json` | comparison output |
| `tests/test_longshort_001.py` | 45 deterministic unit tests |

---

## 11. Conclusion

Neither the 12-1 momentum (MOM) nor the 60-day low-volatility (VOL) dollar-neutral
monthly long/short portfolio **beats its own random baseline significantly** over
the dev window: both make money and both sit at the 100th percentile of the random
cloud, but the monthly t-statistic on excess is 2.18 (MOM, bar 2.24) and 1.06
(VOL, bar 2.24). Under the frozen pre-registration **both variants are REJECTED at
dev**, their holdout windows were never computed, and no further variant may be
tried without a new pre-registration.

Known data limits carried from the frozen plan: no listing/delisting timestamps
(broad-historical universe, survivorship bias reduced but not absent), no intraday
data or order-book depth (costs are a flat per-side rate), flat 1.0 %/yr borrow with
no hard-to-borrow differentiation, and a panel that ends 2026-09-29.
