# STRAT-027 — Systematic portfolio construction study (research only)

**Question:** does a systematic, monthly-rebalanced, multi-stock portfolio built from a
measurable factor beat simple benchmarks (buy-and-hold SPY, and 1,000 random portfolios
of the same size and rebalancing) after risk and costs — instead of hunting for
individual BUY signals?

**Status:** complete. Research only — nothing here touches the live bot, C4, the RSI(2)
shadow, BUY thresholds, the database, or any broker. The branch is **not merged**;
Josh approves every merge.

## Files

| File | What it is |
|---|---|
| `PREREGISTRATION.md` | **Frozen** plan committed before any backtest was run (the pre-registration anchor) |
| `DATA_AUDIT.md` | Data audit (prices + point-in-time fundamentals), with coverage inside the investable universe |
| `build_panel.py` | Builds the daily price panel from the local DATA-001 Alpaca dataset (read-only source) |
| `build_fundamentals.py` | Builds the fact-level point-in-time SEC fundamentals panel (read-only source) |
| `check_fundamentals_coverage.py` | How many liquid-eligible names actually have usable fundamentals, per month |
| `panel.py` | Price panel, monthly rebalance calendar, factor computation (no look-ahead) |
| `fundamentals.py` | Point-in-time QUALITY (ROE) and VALUE (earnings yield) factors |
| `engine.py` | Portfolio construction, turnover, costs, metrics, delisting rule, cap enforcement |
| `run_study.py` | Main driver: dev window, one-shot holdout, random null, SPY benchmark |
| `run_diagnostics.py` | Post-hoc development-window diagnostics (fresh fundamentals, per-year, concentration) |
| `run_sensitivity.py` | Declared sensitivity grid (portfolio size x cost) |
| `make_report.py` | Renders `RESULTS.md` from the results JSON (no hand-copied numbers) |
| `test_sanity.py` | Engine sanity checks (turnover, costs, no-look-ahead perturbation, delisting, cap) |
| `RESULTS.md` | The numbers |
| `REPORT.md` | Plain-language report |

## How to reproduce

```bash
cd experiments/strat_027
python3 build_panel.py                 # ~20 s  -> data/panel.parquet
python3 build_fundamentals.py          # ~25 s  -> data/fundamentals_pit.parquet
python3 check_fundamentals_coverage.py # ~3 min -> data/fundamentals_coverage.csv
python3 test_sanity.py                 # engine sanity checks
python3 run_study.py --windows all     # dev + ONE-SHOT holdout -> results/_results.json
python3 run_diagnostics.py             # dev diagnostics -> results/_diagnostics_dev.json
python3 run_sensitivity.py             # sensitivity grid -> results/_results_sensitivity.json
python3 make_report.py                 # -> RESULTS.md
```

The derived data directory (`data/`) is git-ignored and regenerable. The source datasets
are read-only and untouched:

- `research_data/alpaca_daily_2016_2026/` (prices, Alpaca paper data API)
- `research_data/sec_fundamentals/_per_quarter/` (SEC XBRL facts + filing headers)

## Headline

See `REPORT.md` (plain language) and `RESULTS.md` (numbers).
