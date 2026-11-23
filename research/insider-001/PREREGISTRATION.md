# INSIDER-001 — Insider buying (SEC Form 4) on top of MOM 12-1 (PREREGISTRATION)

Status: **FROZEN before any result is computed.** Approved by Josh Nov 22, 2026.
RESEARCH ONLY, DEV window only. No bot code, no tests, no bot changes.

## 1. Scope and non-goals
- Base strategy: MOM 12-1 long-short, monthly rebalance, decile legs, frozen
  construction/costs/panel of SECFILE-001 / REGIMESECTOR-001 / VOLSIZE-001
  (`research/volsize-001/engine_fixed.py`, COST_PER_SIDE 0.15%/side, BORROW 1%/yr on the
  short leg, MIN_PRICE 5, MIN_ADV_SHARES 1e6 (20d), DECILE 0.10, equal weight in each leg,
  12-1 momentum = close(end M-2)/close(end M-13) - 1, decision on the last trading day
  of month M-1, entry at the open of the first trading day of M, exit at the open of the
  first trading day of M+1).
- Not a repeat: RESEARCH_LOG.md has no tested insider-buying row (checked 2026-11-23).
  Not retested here: SECFILE-001, EVENTNEWS-001, REGIMESECTOR-001, VOLSIZE-001,
  SMOOTHMOM-001.
- k = 2 variants (V1, V2), locked below. No tuning after this file is committed.

## 2. Data
- Panel: `/tmp/panel_dev.parquet` (same DEV panel as VOLSIZE-001), read with a parquet
  row filter `2018-01-01 <= date <= 2024-12-31` and asserted after read. No price row
  after 2024-12-31 is ever loaded (the 2025-01..2026-08 holdout is BURNED).
- DEV window: months 2021-01 .. 2024-12 (entry open >= 2021-01-01 and exit open
  <= 2024-12-31; exit after 2024-12-31 excluded).
- Insider filings: SEC "Insider Transactions Data Sets" (Form 3/4/5 quarterly ZIPs,
  sec.gov/dera/data/form-345), quarters 2019Q4 .. 2024Q4, free SEC data only, free-access
  User-Agent `SmartBot research (Josh Savoy)`, <= 5 requests/second, ZIPs/TSVs deleted
  after the filtered extract is written.
- Kept rows: Form 4 (`DOCUMENT_TYPE = 4`), non-derivative transactions
  (NONDERIV_TRANS), transaction code **P** (open-market purchase), acquired (**A**),
  reported by at least one owner with relationship Officer or Director (ten-percent-owner
  only does not qualify), and value = TRANS_SHARES x TRANS_PRICEPERSHARE >= **$10,000**
  (rows with a missing or non-positive price are dropped).
- Ticker: issuer CIK mapped to ticker with the CIK map of SECFILE-001
  (`ticker_cik.json`, current `company_tickers*.json`); unmapped issuers dropped.
- **Known date = the SEC FILING date** (`SUBMISSION.FILING_DATE`), never the transaction
  date (no lookahead).
- Extract: `research/insider-001/data/insider_purchases.parquet`, only rows with
  filing date <= 2024-12-31 (asserted at load).

## 3. Baseline (reference, must reproduce)
DEV reference: mean net **1.708%/mo**, t(excess) **2.294**, max drawdown **-27.7%**.
The script prints the baseline line and compares to 1.7082%/mo and t 2.294 (tolerance:
mean 1e-6, t 1e-2); a mismatch is a blocker, not a number to reinterpret.

## 4. Signal (both variants, at rebalance date d = decision date)
A stock is **insider-bought** at d if, over the prior **90 calendar days — filing dates
strictly before d** (`d - 90d <= filing_date < d`) — at least one qualifying purchase
(section 2, mapped to that ticker) was filed.
A stock is **cluster-bought** at d if at least **2 distinct insiders** (distinct
REPORTINGOWNER CIK) filed qualifying purchases in the same window.
Multiple purchases by the same person count as one insider. The signal uses only filing
dates < d; a filing dated d itself is never used.

## 5. Variants (k = 2, LOCKED)
### V1 — MOM TILT
- Short leg: unchanged MOM bottom decile, equal weight (identical to baseline).
- Long leg: same **size** as the baseline long decile (n_names = floor(0.10 x eligible)).
  Candidate pool = the top **20% by MOM 12-1** of the eligible names (MOM >= 80th
  percentile of eligible MOM; ties by symbol order).
  Take the insider-bought names in the pool **first, ordered by MOM descending**, then
  fill the remaining slots from the pool **by MOM rank** (highest not already taken).
  If the pool holds fewer than n_names insider-bought names, the leg holds n_names names
  from the pool. If the pool itself has fewer than n_names names (tiny universe), take
  the whole pool and fewer names.
- Equal weight in each leg; costs: frozen 0.15%/side charged on names opened and names
  fully closed vs the previous month (the frozen engine convention).
- V1 is measured against plain MOM (baseline) and against its own random draw (§6).

### V2 — STANDALONE CLUSTER
- Long-only portfolio of **all** eligible cluster-bought stocks at d, equal weight,
  held one month (entry/exit identical to the engine). A name must also be tradable
  under the frozen filters (eligible: finite decision close >= 5, ADV 20d >= 1e6 shares,
  finite entry open) — it does NOT need a momentum value.
- Costs: 0.15% per side on opens and full closes.
- Months with < 10 names hold what is available; months with 0 names are cash (0%, no
  cost). All DEV months are counted (including cash months) in the series.
- Excess = V2 monthly net - the equal-weight return of the eligible universe that month
  (same entry-open -> exit-open return, delisting fallback, no trading cost on the
  universe). Returns net of cost vs the costless universe.
- Also reported: the monthly Pearson correlation of V2 with baseline MOM (for a possible
  later blend; not tested here).

## 6. Bars
k = 2, Bonferroni percentile threshold **98.75** (100 - 2.5/2), t threshold **2.24**.
- **(a) DEV bars**:
  - V1: >= 24 months with positions, mean net > 0, mean excess (vs baseline) > 0, t on
    the monthly V1-baseline difference >= 2.24, percentile vs its random draws >= 98.75.
  - V2: >= 24 months, mean net > 0, mean excess (vs universe) > 0, t on excess >= 2.24,
    percentile vs its random draws >= 98.75.
- **(b) improvement bars**:
  - V1: improvement over plain MOM: paired monthly mean (V1 - baseline) > 0 and
    t >= 2.24.
  - V2: mean excess vs the eligible universe > 0 with t >= 2.24.
- **(c) beats random, 1000 draws** (100 in smoke):
  - V1: random same-count swaps into the long leg from the top-20%-MOM pool — i.e. each
    month the long leg is a uniform random n_names-sized subset of the pool (random
    insider tilt replaced by random picks from the same pool), same short leg, costs and
    engine. Percentile of the V1 mean net vs the draw means (percent strictly below).
  - V2: random same-count picks from the eligible universe each month (long-only
    equal-weight, same costs), percentile >= 98.75.
- All bars must pass for a variant to be a PASS; otherwise FAIL. The report also gives
  Sharpe (monthly mean/sd x sqrt(12)), max drawdown of the monthly compounded series,
  the worst 3 months, monthly one-way turnover (sum of names opened + closed / n_names,
  averaged) and names per month for each series.

## 7. Random numbers and determinism
Fixed seeds (`SEED_V1`, `SEED_V2` constants in the script); vectorized draws; the same
seeds reproduce the same results. Script writes `results.json` and `results.md` (local,
git-excluded); it is delivered as a script, results are computed later by the manager.
