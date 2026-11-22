# EVENTNEWS-001 — pre-registration (written and committed BEFORE any result is computed)

Task (Josh-approved 2026-11-21): test whether skipping names with an upcoming scheduled
earnings date (V1) and/or a negative news headline in the 30 days before the rebalance (V2)
improves MOM 12-1 **on DEV only**. RESEARCH ONLY, paper only. No orders, no PR, no merge.

## 0. Scope, window, data firewall
- DEV window: months 2021-01..2024-12 with exit_date <= 2024-12-31 (the panel-month set
  used by HOLDOUT-MOM-002 `dev`, 47 months; the 12-1 lookback months before 2021-01 are
  used for the factor only).
- **NO data dated after 2024-12-31 is read or used for anything**, including earnings dates,
  news timestamps and calendar requests (requests are end-bounded at 2024-12-31T23:59:59Z).
  The 2025-01..2026-08 holdout is BURNED and is not computed.
- Baseline: frozen MOM 12-1 exactly as in HOLDOUT-MOM-002 (branch base commit 9c92d76):
  PREREGISTRATION rules, engine_fixed.py, panel_splits.parquet (sha256 4387206b..c071,
  asserted), costs 0.15%/side on open and full close, borrow 1.0%/yr prorated, seed
  20261112, 1000 random draws, no re-derivation, no rule change.
- Unfiltered MOM DEV reference (HOLDOUT-MOM-002): 47 months, mean net 1.708%/mo, t on
  excess 2.2944, percentile 100.0, maxDD -27.74%.

## 1. Filter mechanics (frozen)
At each monthly rebalance (decision date = last trading day of M-1) a name is FLAGGED if it
meets the variant rule below. Flagged names are dropped from the leg (long or short) they
would belong to. The removed weight is redistributed EQUALLY across the remaining names on
that leg (re-simulated at weight 1/n_remaining). If a leg would have fewer than 0 names
remaining the month is left unfiltered (counted and reported; expected never).
- Selection order: run the frozen MOM `select_legs` first, then apply the filter to the
  chosen decile names (do NOT re-rank deeper into the decile; a drop shrinks the leg).
- Costs/borrow/delisting: unchanged engine rules; a dropped name is simply not opened, and
  a previously held name that becomes flagged is closed at the exit open and counts as a
  close (0.15%) exactly like an ordinary rotation trade.
- Flag source is evaluated strictly at the decision date (as-of, no lookahead).

## 2. V1 EARNINGS
Flag a name if a scheduled reported earnings date falls inside the coming holding window
(first trading day of M .. last trading day of M], i.e. entry_date <= date < next entry_date.
- Source: free only. Primary: Nasdaq earnings-calendar history per symbol
  (api.nasdaq.com calendar/earnings endpoint), request bounded to the holding month.
  Fallback order if Nasdaq fails for a symbol: yfinance earnings dates; then the panel's
  realized dates only if a documented realized-date file is available.
- Known-before-rebalance: Nasdaq publishes scheduled dates weeks ahead. If a source only
  yields realized (report) dates, the assumption "the report date was knowable at the
  decision date" is used (mild, stated; public firms pre-announce). Every source used is
  recorded per flag; coverage % (names with a known earnings date in the window / names
  with any date in the window) is reported.

## 3. V2 NEWS — Alpaca News API (historical Benzinga headlines)
- Read-only `GET https://data.alpaca.markets/v1beta1/news`, keys from the server's paper
  account configuration (never printed/committed; cached raw JSON only), rate-limit
  respected with backoff.
- Flag a name if any headline **timestamped strictly before the rebalance decision close**
  and **on or after decision_date - 30 calendar days** contains, case-insensitive, one of
  these FROZEN keywords (# may be punctuated):
  offering, dilution, S-1, resale, FDA, complete response, advisory committee, PDUFA,
  guidance cut, lowers guidance, going concern, bankruptcy, delisting, investigation,
  downgrade.
  ("guidance cut"/"lowers guidance" are matched as phrases; the rest as substrings on word
  boundaries where sensible, documented in the runner.)
- Only symbols in the panel's decision-date top/bottom decile need queries (plus any name
  the variant holds); reqwest window end exclusive at decision_date+0 days.
- Headlines dated after 2024-12-31T23:59:59Z must never be requested; assert it.

## 4. V3 = V1 OR V2 (flag = union).

## 5. Pass bars (ALL must hold), k=3 (multiple testing across V1,V2,V3)
(a) FILTERED strategy passes DEV bars: >=24 months, mean net > 0, mean excess > 0,
    percentile vs random >= 97.5, t on excess >= 2.24 (identical to `dev_bars`).
(b) IMPROVEMENT: d = filtered monthly net - unfiltered monthly net (same months);
    mean(d) > 0 AND t(d) >= 2.24 (one-sample t, n-1 df).
(c) BEATS RANDOM FILTERS: filtered mean net's percentile >= 99.17 (97.5 Bonferroni k=3)
    against 1000 draws of an UNFILTERED MOM portfolio in which, each month, the SAME NUMBER
    of names removed by the variant are removed from each leg at RANDOM.
    Seed scheme (frozen): per draw d (0..999) and month r, seed = SEED2 + 1000*d + r with
    SEED2 = 20261121, `np.random.default_rng(seed)`; long drop count = number of flagged
    names removed from the long leg that month, short likewise; the survivors are the
    remaining leg names, immediately re-weighted equally and simulated with the engine's
    cost/borrow/delisting rules (carry logic via the same previous-set bookkeeping as
    `random_monthly_nets`). Percentile = 100 * mean(draw_mean < filtered_mean) (strict,
    same as `percentile_vs_random`).
- Percentiles use the strict per-month random MOM draws (seed 20261112) for bar (a),
  exactly as HOLDOUT-MOM-002, so filtered and unfiltered share the same random benchmark.
- A variant that passes all three bars is ONLY a DEV candidate — not a strategy to trade.
  Confirmation requires a separate forward paper test Josh must approve (the 2025+ data is
  burned and cannot confer confirmation).
- No change to rules, keywords, mechanics or bars after results are seen. Plumbing fixes
  only, and they must be stated in the report.
