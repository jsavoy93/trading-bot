# SPLITSCREEN-002 — Pre-registration (written BEFORE any split data was downloaded)

Parent: SPLITSCREEN-001/001B (commit b033c19, REPORT.md). That task repaired split
artifacts by a heuristic (nominal ratios + big-move clause) that dropped symbols and
sometimes applied the wrong factor. This task replaces the heuristic with **recorded
split records only**. A data fix, not a strategy change: no rule, factor, bar, cost,
borrow, eligibility, seed or draw count changes.

## Primary panel

Panel = the original panel (`panel.parquet`, sha256 31a2b0ea…0930) with **only**
recorded splits / reverse splits applied:
- For every recorded split with ratio q (e.g. 1-for-20 => q = 1/20, 2-for-1 => q = 2)
  and ex-date d, back-adjust every row dated **before d**: open/close/high/low *= q,
  volume /= q. Prices are continuous across d.
- Source of records: free split history, `yfinance.Ticker(sym).splits`; SEC/Nasdaq free
  split lists only as backup. Raw records cached to disk (`splits_raw.json`).
- **No other smoothing, no symbol drops, no nominal-ratio heuristics, no big-move repair.**
- A recorded split is applied **only if** the panel's one-day close ratio on/near the
  ex-date (within +/-2 trading days, the split day chosen as the candidate with ratio
  closest to q) is within ~10% relative of q (`|r/q - 1| <= 0.10`). Otherwise the split is
  treated as already adjusted and skipped (guards against double adjustment).
- Unmatched big moves remain as real moves. Output `panel_splits.parquet` (also limited to

  rows dated <= 2024-12-31 so the holdout is never written; the DEV reruns would ignore
  later rows anyway).
- Scope: only DEV dates 2021-01..2024-12 are read for results. **The holdout (2025-01+)
  is never read or computed.** If anything passes, report and stop.

## Diagnostics (descriptive only, not pass/fail)

Counts: (a) panel symbols with >=1 split record; (b) splits applied; (c) splits skipped
as already-adjusted; (d) >40% one-day DEV moves that remain unexplained, top 10 by size;
(e) symbols with no split lookup at all (e.g. delisted / unknown to Yahoo). BHVN
2022-10-04 (1-for-20 reverse, raw 151.80 -> 8.30) is spot-checked for continuity.

## Re-runs (frozen)

MOM 12-1, VOL 60d and CRASHFILTER-001 on DEV only, with the frozen
`engine.py` / `run_study.py` / `run_mom_vol_only.py` / `crashfilter_run_clean_v2.py`
copied byte-identical from SPLITSCREEN-001 (only the panel path changes), same seed
20261112 / 1000 draws (CF seed 20261118), 0.30% round trip, 1.0%/yr borrow, DEV masking
exactly as 001B. Dev bars unchanged, including t-on-excess >= 2.24.

## Success / failure

MOM and CRASHFILTER "hold" if they pass all five dev bars on the split-records panel
(>=24 months, mean net > 0, mean excess > 0, percentile >= 97.5, t >= 2.24); otherwise no.
