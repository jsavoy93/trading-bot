# SPLITSCREEN-001 — Pre-registration (written BEFORE any scan)

Task: remove unadjusted-split artifacts from the research panel and re-check
LONGSHORT-001 (MOM 12-1, VOL 60d) and CRASHFILTER-001 on DEV (2021-2024).

Base evidence: SHORTSIDE-AUDIT-002 (/root/research/shortside-001/AUDIT.md,
commit 53bfb19). Panel `/root/.openclaw/worktrees/STRAT-027/experiments/strat_027/data/panel.parquet`
sha256 31a2b0eaf4e87260fe75897529c0b567f7fc022f7e173bbfc951182df9990930,
9,157,044 rows, 6,258 symbols, 2019-01-02 .. 2026-09-29. It is split-only
adjusted, so any split not present in the adjustment vendor's split table
appears as a raw price jump.

**This is a data fix, not a strategy change.** No pre-registered rule, factor
definition, bar, cost, borrow, eligibility rule, seed or draw count changes.
Only the panel input differs between the "before" and "after" runs.

## Artifact definition (fixed now, used verbatim by the scan)

For each symbol, order the rows by date and compute the 1-day close ratio
`r = close_t / close_{t-1}`.

A symbol-day is a **candidate split artifact** when either clause holds:
1. **Ratio clause:** `r` is within 3% (relative on the ratio) of a common
   split ratio in either direction: 1:2, 1:3, 1:4, 1:5, 1:10, 1:20 (r ~ 0.5,
   0.3333, 0.25, 0.2, 0.1, 0.05) or 2:1, 3:1 (r ~ 2, 3). "Within 3%" means
   `|r/target - 1| <= 0.03`.
2. **Big-move clause:** `|r - 1| > 0.40` *and* no reversal-consistent price
   path, i.e. the move is not undone by the following day in the opposite
   direction by at least 60% of the size of the move
   (`|1 - close_{t+1}/close_t| < 0.6 * |r - 1|` or no next row), so ordinary
   crash/spike snapbacks are not flagged.

Scope of the scan: rows dated 2019-01-02..2024-12-31 only (all dates before
the 2025-01-01 holdout boundary). Flag counts are reported for 2021-2024.

## Treatment (back-adjustment, applied to the cleaned copy only)

For every flagged day `t` of a symbol, replace the earlier prices so that the
price level is continuous across `t`:
- factor `f = close_{t-1} / close_t` (the raw jump ratio, NOT snapped to the
  nominal split ratio, so partly vendor-adjusted jumps like BHVN's
  8.30/151.80 are also repaired);
- for every row `s` with `date_s < date_t`, multiply `open`, `close`, `high`,
  `low` (if present) by `f` and divide `volume` by `f`.
Multiple flags for one symbol are applied from the latest flag backwards.
When a symbol has more than 6 flagged days in one direction, or the implied
adjustment factor is implausible (`f` outside [0.005, 200]), the symbol is
**dropped** entirely from the cleaned panel instead (safer than a wrong
factor; dropped symbols are listed).

The cleaned panel is written to
`/root/research/splitscreen-001/panel_clean.parquet` containing only rows
with `date <= 2024-12-31`. The original panel file is never modified.

## Re-runs (frozen)

- LONGSHORT-001 MOM 12-1 and VOL 60d: `experiments/longshort_001/engine.py`
  + `run_study.py` unchanged, run with `--panel <cleaned>`.
- CRASHFILTER-001: `run.py` unchanged except the panel input path; seed
  20261118, 1000 draws, same costs/borrow/eligibility.
- Frozen bars: >=24 months, mean net > 0, mean excess > 0, percentile >= 97.5,
  t on excess >= 2.24. DEV = 2021-01-02..2024-12-31 (i0 = 505..1509).

## Rules

Paper only, DEV only. Never read or compute on rows dated >= 2025-01-01. If
any strategy passes all dev bars, STOP and report; the holdout needs Josh's
approval. No changes to the main bot.
