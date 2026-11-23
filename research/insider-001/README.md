# INSIDER-001 — insider buying (SEC Form 4) on top of MOM 12-1

Research only, DEV window only. The spec is frozen: `PREREGISTRATION.md`
(committed before any result is computed). Nothing here touches bot code.

## What is here
| File | Purpose |
|---|---|
| `PREREGISTRATION.md` | FROZEN spec: data, panel, signal, V1/V2, bars, seeds. |
| `build_insider_data.py` | Builds `data/insider_purchases.parquet` from the free SEC "Insider Transactions Data Sets" (2019Q4..2024Q4). |
| `run_insider.py` | Implements the prereg on the DEV panel; writes `results.json` + `results.md` (git-excluded). |
| `data/insider_purchases.parquet` | Filtered Form 4 P-code purchases (issuer, insider, filing date, value). ~1 MB. |
| `data/coverage.json` | Row count, filing-date range, ticker-match coverage. |

## Full run (from the repository root)
```
python3 research/insider-001/run_insider.py
```
Defaults: 1000 random draws per variant, all 47 DEV months (2021-01..2024-11),
panel `/tmp/panel_dev.parquet` (DEV rows only, row-filtered by date and asserted
after read — no price row after 2024-12-31 is ever loaded). Smoke (~14 s)
returns in a fraction of a minute; the full 47-month x 1000-draw run is well under
20 minutes (measured: V2 draw pass 0.2 s, V1 0.9 s, panel load 15 s).
Options: `--n-random N`, `--panel PATH`, `--insider PATH`, `--out-dir DIR`.

## Smoke / self-test
```
python3 research/insider-001/run_insider.py --smoke        # 3 DEV months, 20 draws, ~14 s
python3 research/insider-001/run_insider.py --self-test    # synthetic no-lookahead + cost check
```
The self-test proves a purchase filed ON or AFTER the rebalance date is not used
(`d-90 <= filing_date < d`), that two distinct insiders are needed for a cluster,
and that the vectorized simulator reproduces `engine_fixed.simulate_leg` exactly.

## Rebuilding the insider data
```
python3 research/insider-001/build_insider_data.py
```
Downloads 21 quarterly ZIPs (~14 MB each) at <= 5 requests/s with
`User-Agent: SmartBot research (Josh Savoy)`, keeps only Form 4 non-derivative
code-P acquired purchases >= $10,000 by an officer or director, maps issuer CIK
to ticker via the SECFILE-001 map, writes the parquet, then deletes every ZIP.
Expected: 53,237 rows, 2019-10-01..2024-12-31, 100% ticker coverage (3,399 tickers).

## Engine
`run_insider.py` imports `research/volsize-001/engine_fixed.py` verbatim from the
repo (no copy). Baseline must reproduce the public DEV reference: **1.708%/mo,
t 2.294, max drawdown -27.7%**.

## Not done here
No results are committed or reported — the manager runs the full study. The
2025-01..2026-08 holdout is BURNED.
