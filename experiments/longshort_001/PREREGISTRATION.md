# LONGSHORT-001 — Pre-Registered Research Plan: Dollar-Neutral Monthly Long/Short Factor Test

> **RESEARCH ONLY.** No production code, config, secret, database, broker or order
> changes. No live trading. No paper-account orders. Nothing is merged; only Josh
> merges. This document is frozen and committed **BEFORE** any strategy or random
> return was computed. The commit that first adds this file is the
> **pre-registration anchor**. No rule in this file may be changed after looking at
> returns.

- **Backlog item:** LONGSHORT-001 (RESEARCH, Josh-approved 2026-11-12)
- **Branch:** `research/longshort-001` (worktree `/root/.openclaw/worktrees/LONGSHORT-001`)
- **Base:** `origin/main` @ `4107762`
- **Approval gate:** Josh approves every merge. This agent never merges.
- **Distinct from:** STRAT-027 (LONG-ONLY, no short leg), STRAT-014/016/019/026.
  LONGSHORT-001 tests **no quality factor and no value factor**, combines **no**
  factors, and adds **no** post-hoc variants.

---

## 1. Objective

Test whether a **dollar-neutral (market-neutral) monthly long/short** portfolio built
from a single price-based factor beats a **random** long/short portfolio of identical
size, schedule, eligibility, costs and borrow, after costs.

Two variants ONLY (Bonferroni, family alpha = 0.05 / 2 = **0.025**):

| ID | Factor | Long leg | Short leg |
|---|---|---|---|
| `MOM` | 12-1 momentum | TOP decile | BOTTOM decile |
| `VOL` | realized volatility (60d) | LOWEST-vol decile | HIGHEST-vol decile |

No quality. No value. No factor combination. No added variants or sensitivity runs
after results are seen.

A REJECT is a valid and expected outcome and will be reported as such.

---

## 2. Data (read-only)

- **Panel:** `/root/.openclaw/worktrees/STRAT-027/experiments/strat_027/data/panel.parquet`
  — Alpaca **paper** data API daily bars, `adjustment="all"` (split + dividend
  adjusted) OHLCV.
  - Coverage: **2019-01-02 → 2026-09-29**, **6,258** symbols with bars
    (US common stock + ADR; 590 Alpaca-inactive/delisted names INCLUDED and KEPT),
    9,157,044 rows. SPY is in the same file and is NOT used by this study
    (the strategy is market-neutral by design; there is no SPY trend filter).
  - **Full sha256 of the panel file (verified 2026-11-12, before this plan was frozen):**
    `31a2b0eaf4e87260fe75897529c0b567f7fc022f7e173bbfc951182df9990930`
  - **The original panel file is NEVER modified.** This study reads it in place from
    the STRAT-027 worktree; no copy is made inside this worktree, so no copy has to be
    deleted. (If a copy had been made it would have been deleted and reported.)
- **Known data limits (carried into every conclusion):**
  - No listing/delisting timestamps → universe is broad-historical, not point-in-time
    index membership. Survivorship bias is reduced (inactive names kept), not absent.
  - Bars end 2026-09-29 → the last usable exit open is 2026-09-01.
  - No intraday data, no order-book depth → costs are a flat per-side rate.
  - Fundamental factors are deliberately OUT OF SCOPE for this study.

---

## 3. Universe and eligibility (point-in-time, no look-ahead)

The **decision day** is the **last trading day of month M-1**; the **entry day** is the
**first trading day of month M**; the **exit day** is the **first trading day of
month M+1**.

A symbol is eligible for month M if ALL of the following hold, where "decision close"
is the close of the decision day (last trading day of M-1):

| Screen | Rule | Data used |
|---|---|---|
| Liquidity | 20-session mean volume through the decision day ≥ **1,000,000 shares** | bars up to and including decision close |
| Price | decision close ≥ **$5.00** | decision close |
| Tradability | a finite **open exists on the entry day** | entry-day bar |
| History (MOM) | valid closes at the decision day, at month-end M-2 and at month-end M-13 | bars up to decision close |
| History (VOL) | ≥ **61** valid closes up to and including the decision day (→ 60 daily returns) | bars up to decision close |

The **identical** eligibility rule set and the **identical** eligible universe is used
for the strategy legs and for every random draw.

---

## 4. Factor definitions (all inputs end at the decision close; no look-ahead)

Let `P(x)` = the last adjusted close on or before date `x` (no forward fill beyond the
last available bar).

- **`MOM` (12-1 momentum, skip REQUIRED):**
  `MOM = P(end of month M-2) / P(end of month M-13) − 1`.
  The month just ending (month M-1, the decision month itself) is **skipped**, so the
  momentum window covers the 11 months before it. The recent month is never used.
- **`VOL` (realized volatility):** standard deviation (`ddof=1`) of the **60 daily
  simple returns** `close_t / close_{t-1} − 1` ending at the decision close. **60
  trading days, not 12 months.** Not annualized for ranking (ranking is invariant to
  the scaling factor).

Ranking: descending for `MOM` (highest → long), ascending for `VOL` (lowest → long).
Ties are broken by ascending symbol (deterministic).

---

## 5. Portfolio construction (identical for both variants)

- **Monthly rebalance.** Rank at the decision close; enter at the **open of the first
  trading day of month M**; exit/rebalance at the **open of the first trading day of
  month M+1**.
- **Dollar-neutral.** Long notional = `+1.0`, short notional = `−1.0` (gross exposure
  2.0). Long notional always equals short notional at each rebalance.
- **Deciles = equal-count slices** of the eligible universe:
  `n_leg = floor(0.10 × n_eligible)` per leg (minimum 1). The top `n_leg` ranked
  eligible names form the long leg, the bottom `n_leg` form the short leg. A month
  with fewer than 20 eligible names is skipped for that variant.
- **Equal weight within each leg**: each name carries weight `1/n_leg` of that leg's
  notional. Weights are re-set to equal weight at each rebalance (no drift carry).
- **Untradeable at rebalance:** a name without a valid entry-day open is not eligible
  and is simply excluded; if a name becomes untradeable at a later rebalance it is
  closed and its slot is redistributed equally over the remaining names of that leg
  (weights re-normalized within the leg).
- **Delisting mid-month:** a held name with no bar at the exit open is exited at its
  **last available close** (last finite close at or before the exit open) and its slot
  **holds cash (0% return) until the next rebalance**. It is **not** replaced
  retroactively and no look-ahead replacement is performed.
- **No SPY trend filter**, no market timing, no beta/sector neutralization.

---

## 6. Costs (FROZEN)

Applied **identically** to the strategy and to every random portfolio:

1. **Round-trip trading cost: 0.30% per name round trip = 0.15% per side.**
   - 0.15% of the traded notional is charged when a name is **OPENED** (entry rebalance).
   - 0.15% of the traded notional is charged when a name is **FULLY CLOSED** (exit
     rebalance, or mid-month delisting exit).
   - A name carried from one month into the next and **not traded** is **not** charged
     again until it actually trades.
   - Notional used for the charge is the name's weight in its leg (long leg notional
     1.0, short leg notional 1.0); the charge always reduces the net return.
2. **Short-borrow fee: 1.0% PER YEAR on the short notional**, prorated by the number
   of trading days actually held in the month: `borrow_charge = 0.01 × (days_held /
   252)`, applied to the short leg's notional (1.0). Days held = the number of trading
   days from the entry open up to (excluding) the exit open.

**Sign conventions (explicit):**

- Long leg: `long_pnl = Σ_i w_i · r_i − 0.0015·(opened long weight) − 0.0015·(closed long weight)`
- Short leg: `short_pnl = −Σ_i w_i · r_i − borrow_charge − 0.0015·(opened short weight) − 0.0015·(closed short weight)`
  (the short leg's P&L is the negative of the underlying return, minus the borrow
  fee, minus its own trading costs)
- **Monthly net return = long_pnl + short_pnl** (net = long leg P&L + short leg P&L).

---

## 7. Windows

- **DEV:** decision months `M` from **2021-01** through **2024-12** (48 months). The
  January-2025 exit produced by the December-2024 decision is still DEV.
- **HOLDOUT:** decision months `M` from **2025-01** onward, restricted to months whose
  **full hold completes by 2026-09-29** (the last exit open in the panel is
  2026-09-01, so the last usable holdout month is **2026-08**; 20 months).
- The holdout is run **ONCE** and **ONLY** for variants that PASS dev. A variant that
  FAILS dev is reported as **REJECTED at dev** and **its holdout returns are never
  computed**.

---

## 8. Random baseline (FROZEN)

- For each rebalance month, from the **same eligible universe**, draw a random long
  decile and a random short decile with the **same counts** as the strategy's legs
  (`n_leg` each), sampled **without replacement** from the eligible names (the short
  sample excludes the names already drawn for the long leg).
- Same entry, exit, delisting, cost and borrow rules as the strategy.
- **Number of draws: 1000.**
- **SEED = 20261112** (stated here, before the run). One `numpy.random.default_rng(20261112)`
  stream drives all draws; the draw order is: for draw d = 0..999, for each month in
  chronological order, sample the long leg then the short leg from that month's
  eligible universe.
- **Expected random monthly net return for month t** = mean of the 1000 draws' monthly
  net returns in month t.
- **Excess (monthly)** = strategy net return − expected random net return for that month.
- **Percentile** = the fraction of the 1000 **full-period** draws whose **mean net
  return over the same window** is **strictly less** than the strategy's **mean net
  return over the same window**. (For holdout, the full-period means are computed over
  the holdout months only, using the same 1000 draws restricted to those months.)

---

## 9. Frozen pass bars

**DEV** — all five must hold:

1. at least **24** months,
2. mean monthly **net > 0**,
3. mean monthly **excess > 0**,
4. **percentile ≥ 97.5**,
5. monthly **t-statistic on excess ≥ 2.24**.

**HOLDOUT** (k = number of dev passers; run once) — all five must hold:

1. at least **12** months,
2. mean monthly **net > 0**,
3. mean monthly **excess > 0**,
4. **percentile ≥ 1 − 0.05/k** (for k=1 → 95.0; for k=2 → 97.5),
5. monthly **t ≥ 2.0** if k = 1, else the **two-sided 0.05/k cutoff** on the t
   distribution with n−1 degrees of freedom. The cutoff **value used** is stated in
   `RESULTS.md` (for k=2, n=20 months → t_{0.975,19} ≈ 2.093).

A variant **PASSES** only if it passes **both** dev and holdout. A variant that fails
dev is **REJECTED** at dev and its holdout is never computed.

---

## 10. Annualization convention

- Annualized return: **(1 + mean_monthly_net)^12 − 1** (geometric compounding of the
  mean monthly net return).
- Annualized expected-random return: **(1 + mean_monthly_random)^12 − 1**.
- Annualized excess: **(1 + mean_monthly_excess)^12 − 1**.
- Monthly volatility: sample std (ddof=1) of monthly net returns.
- Annualized volatility: **monthly_vol × sqrt(12)**.
- **Sharpe = annualized excess return / annualized volatility of the monthly excess
  series** (excess-based Sharpe; the net-based Sharpe is also reported).
- Monthly t-statistic on excess: `t = mean(excess) / (std(excess, ddof=1)/sqrt(n))`.
- Max drawdown: on the compounded monthly net equity curve (`cumprod(1+r) - 1`),
  including the initial equity of 1.0.

---

## 11. Reporting metric list (per variant, per window)

n months; mean monthly net; annualized net; annualized expected-random; annualized
excess; monthly volatility (and annualized); Sharpe (annualization stated); max
drawdown; monthly win rate; long-leg vs short-leg contribution split; monthly
t-statistic on excess; percentile vs the random cloud; explicit **PASS/FAIL verdict
per bar**. Plus: panel sha256, seed, exact commands, and an independent hand
recomputation of 2–3 sample months (names, entry/exit prices, leg returns, costs,
borrow, net).

---

## 12. Safety (non-negotiable)

- PAPER / RESEARCH ONLY. No live trading, no brokerage credentials, no order of any
  kind (no place / modify / cancel).
- No write to `live_paper.db`, `trading_bot.db` or any trading database; no `sqlite3`
  against anything under `/root/.openclaw`.
- No touching `/root/strat-swing` or `/root/.openclaw/workspace/trading-bot`.
- No modification of production code, config, `.env`, secrets, timers or systemd units.
- No merge, no push to `main`.
- No deletion, move or archival of any existing file.
