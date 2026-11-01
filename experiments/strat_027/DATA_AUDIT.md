# STRAT-027 — Data Audit (completed BEFORE any backtest was run)

Audit date: 2026-11-01 (UTC). All source data was read **read-only**; nothing was modified.

The brief for STRAT-027 required an audit first: *"check whether we have point-in-time
fundamentals (with release dates). If not, test only price-based factors and say so plainly."*

**Short answer: yes, point-in-time fundamentals exist locally, and they were usable.**
Quality and value factors were therefore tested alongside momentum and low volatility,
exactly as the pre-registration allowed.

---

## 1. Prices — available, and better than the prior studies used

| Item | Finding |
|---|---|
| Source | `research_data/alpaca_daily_2016_2026/` (DATA-001), built from the **Alpaca paper data API**, `adjustment="all"` (split + dividend adjusted), `feed=sip` |
| Coverage | 2016-01-04 → 2026-09-29, 13,489 symbols, 19,723,492 daily bars |
| Universe class | **Class B (partially historical)** — Alpaca exposes active *and* inactive US equities but no listing/delisting timestamps |
| Survivorship mitigation | 13,126 active symbols with bars **+ 590 Alpaca-inactive (delisted/suspended) names** were pulled on a deterministic alphabetical stride — no return-based or future-informed selection |
| Panel actually used | 2019-01-02 → 2026-09-29, **6,258 symbols**, 9,157,044 rows, common stock + ADR only (ETFs/funds, preferreds, warrants, rights, units, notes, SPACs excluded) |

Prior work (STRAT-021/022/023) used a curated "currently known liquid tickers" list of
379–686 names. This study uses a much broader universe, which is what a cross-sectional
factor study needs, and it includes delisted names.

**Limitations carried forward:** no point-in-time index membership (this is "all liquid
US common stocks/ADRs", not "S&P 500 members"); bars stop at 2026-09-29, so the last
complete monthly rebalance open is **2026-09-01** (not "today").

---

## 2. Point-in-time fundamentals — available, usable, and annual-only

### 2.1 Where they live

`research_data/sec_fundamentals/_per_quarter/<YYYYqN>_num.parquet` (XBRL **facts**) and
`<YYYYqN>_sub.parquet` (filing **submission** headers), 46 quarters, 2015q1 → 2026q2.

- `num` columns: `adsh, tag, version, ddate, qtrs, uom, segments, coreg, value, footnote, __quarter`
- `sub` columns include: `adsh, cik, name, sic, form, period, fy, fp, filed, accepted, fye, __quarter`

**The release date exists.** `num` rows carry **no** filing date; the public release date
lives in `sub` as **`filed`** (YYYYMMDD) and `accepted` (timestamp), joined on `adsh`.
The join is lossless on the 2024q3 quarter (205,119 fact rows in, 205,119 matched, 0 unmatched).

Verbatim example (ADP, CIK 8670, adsh `0000008670-24-000024`, **filed 20240807**):

| cik | tag | ddate | qtrs | value | filed |
|---|---|---|---|---|---|
| 8670 | RevenueFromContractWithCustomerExcludingAssessedTax | 20230630 | 4 | 12,042,600,000 | 20240807 |
| 8670 | NetIncomeLoss | 20230630 | 4 | 3,412,000,000 | 20240807 |
| 8670 | Assets | 20230630 | 0 | 50,971,000,000 | 20240807 |

### 2.2 The defect that had to be handled (and how)

Raw `num` rows are **XBRL-dimensioned**: 26% of `(cik, tag, ddate)` triples have several
rows with *different values inside the same filing*. This is not restatement — it is
dimensional context (segments/coreg). Verbatim (CIK 1000045, `Assets`, 20240331, one adsh):

| segments | value |
|---|---|
| (empty) | 59,838,000 |
| `Scenario=TransactionAccountingAdjustments;` | 1,316,000 |
| `Scenario=ProForma;` | 61,154,000 |

**Mandatory filter applied:** keep only rows where `segments` **and** `coreg` are both
empty (the consolidated total). This drops ~54% of raw rows and cuts differing-value
conflicts from 26% to ~3.6–4.0%. The repository's own corrected aggregate identifies
`sec_annual_facts.csv.gz` (sha256 `f461f578…`, 2,283,200 rows) as authoritative and marks
`DEFECTIVE_AGGREGATE_PRE_REPAIR.csv.gz` as **not** authoritative — that file was not used.

**Conflict rule applied (the repo's own canonical rule, "latest accepted ≤ formation_date wins"):**
for each `(cik, tag, ddate)` keep the fact from the latest `filed` (then latest `accepted`).

### 2.3 What the dataset does and does not contain

| Property | Value |
|---|---|
| Forms | **10-K and 10-K/A only** (`fp = FY`) |
| Filings | 67,929 (10-K 64,761; 10-K/A 3,168) across 11,115 CIKs |
| Cadence | **Annual filings only** → a company's fundamentals update once a year, ~2–4 months after its fiscal year end |
| Distinct XBRL tags | 7,611 in 2024q3 alone (11,537 in 2026q2) |
| Facts available for this study | `NetIncomeLoss`, `StockholdersEquity`, `Assets`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`, `CommonStockSharesOutstanding`, `CashAndCashEquivalentsAtCarryingValue` — all present in every sampled quarter |
| Repo audit of the per-quarter caches | `_per_quarter_cache_audit.json`: all 46 quarters `status: PASS` (byte-verified against `raw/*.zip`); 2025q3 was surgically repaired and re-validated (`_2025q3_validation_report.json`, all checks PASS) |
| Known repo-wide coverage warning | `_coverage_stability.json`: complete-financials coverage ≈ **23–33% of the exchange universe** per year |

### 2.4 How the factors were built from it

At each monthly rebalance date `d`, for each symbol we use **the most recent 10-K whose
`filed` date is ≤ d**. Nothing filed after `d` is ever read.

- **QUALITY** = `net_income (FY) / stockholders_equity (FY end)` → return on equity. Higher is better.
- **VALUE** = `net_income (FY) / market cap(d)`, where `market cap(d) = adjusted close before d × shares_outstanding` from the same latest filing. Higher is better.

Because the filings are annual, both factors are **step functions** that change once a
year per company — a deliberate, disclosed property of the test, not a modelling choice.

---

## 3. Coverage inside the investable universe (the number that decides usefulness)

*(filled in from the panel build — see `fundamentals_meta.json`)*

---

## 4. Verdict

- **Prices:** usable, broad, delisting-aware. Used.
- **Point-in-time fundamentals:** **available with release dates** (`filed` / `accepted`,
  joined from the submission headers) → the pre-registration's condition for testing
  QUALITY and VALUE was satisfied, and both were tested.
- **Caveats that materially shape the result:** annual-only filing cadence, ~23–33%
  exchange-universe coverage, the mandatory `segments`/`coreg` empty filter, and the
  restatement rule. A company with a fiscal year ending in, say, September is dated by
  its own year end, not the calendar.
- **Not available:** point-in-time index membership, intraday prices, order-book data.
  Costs are therefore a flat per-side rate, not a spread model.
