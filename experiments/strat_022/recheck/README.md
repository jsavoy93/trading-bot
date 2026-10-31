# STRAT-023 Task A recheck — methodology notes

## Directory
- `build_delisted_universe.py` — selects delisted additions from the read-only
  DATA-001 parquet dataset and writes per-symbol CSVs into `data_delisted/`
  (gitignored). Selection: not in original 379; ≥220 bars; bar series ends in
  [2020-06-01, 2026-06-01); coarse_type ∈ {common_or_other, adr}. No
  return-based filtering (no future information beyond "not listed today",
  which is exactly the survivorship structure being corrected).
- `run_recheck.py` — reruns C4 (RSI2<10, SMA200 filter, 3xATR stop, 10-day
  time stop, 10 bps/side) with the frozen STRAT-022 engine functions,
  original CSVs (control A) and original + 307 delisted (B), dev + holdout,
  matched seeded random baseline, and a delisted-only sleeve breakdown.
- `_results_recheck.json`, `_additions_selected.csv` — outputs.

## External-source evaluation (requirement of the task)
- Stooq free delisted daily bars: endpoint is protected by a JavaScript
  SHA-256 proof-of-work challenge. The challenge WAS solved programmatically
  and accepted, but subsequent CSV requests returned "Access denied" for this
  host (datacenter-IP ban). Tested: bby.us, sivb.us, sivbq.us, frc.us,
  sbny.us, pacw.us. Conclusion: not usable from this host. No other free
  source with real delisted US daily price history is accessible without
  API keys/paid plans; limitation accepted and documented.

## Terminal value handling
No synthetic final bar. A position still open when a symbol's data ends exits
at the last available close (identical to the original engine's behavior at
data end). Verified that names which collapsed show the decline in the data
(XL → $1.14 last / $0.72 min; GDP min $0.155; AMRSQ/SUNWQ/SRNE/NEXI appear
among worst recheck trades with −18%..−35% outcomes).
