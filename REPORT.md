# EVENTNEWS-001 — event-date / news filters on MOM 12-1, DEV only

**VERDICT: V1 FAIL (0/3 bars). V2 BLOCKED (no historical headlines). V3 = V1 by construction -> FAIL.**
The earnings filter made MOM 12-1 WORSE on DEV, not better.

- **V1 EARNINGS — FAIL.** mean net 1.36%/mo (vs 1.71%), t on excess 1.87 (<2.24, bar a), improvement mean -0.35%/mo with t -0.95 (bar b), percentile vs random same-size removals 32.5 (bar c needs 99.17). 22 of 47 months improved. maxDD -27.89% (vs -27.74%).
- **V2 NEWS — BLOCKED, not run.** The configured free Alpaca plan serves NO historical news: every DEV-window request returns 0 items (probe: AAPL/MSFT/TSLA/NVDA/WOLF/CAPR, 2021-06 and 2024-06 windows, data/v2_probe.json), while no-date requests return 2026 items. No keyword list was ever applied to data; no results are reported for V2.
- **V3 BOTH** = V1 OR unavailable-V2 = V1; identical numbers, FAIL.

## Data coverage
| item | value |
|---|---|
| earnings dates source | Nasdaq earnings calendar (per-day, 2020-12..2024-12) + yfinance fallback |
| symbols with earnings dates | 2094 / 2234 candidates (93.7%); 94.79% of DEV leg-name-months covered |
| news headlines pulled | 0 usable (historical unavailable on the free plan); probe stored |
| avg names removed/month | 31.5 long, 30.4 short (of ~170 per leg) — the filter removed ~18% of each leg |

## Bars vs unfiltered MOM (DEV 2021-01..2024-11, 47 months)
| metric | unfiltered MOM | V1 earnings | V3 (==V1) | bar |
|---|---|---|---|---|
| mean net/month | 1.710% | 1.365% | 1.365% | >0 |
| mean excess/month | — | 1.99% | 1.99% | >0 |
| percentile vs random | 100.0 | 100.0 | 100.0 | >=97.5 |
| t on excess | 2.2944 | 1.8700 | 1.8700 | >=2.24 FAIL |
| (b) improvement mean / t | — | -0.352% / -0.95 | same | >0 / >=2.24 FAIL |
| (c) percentile vs random filters | — | 32.5 | 32.5 | >=99.17 FAIL |
| max drawdown | -27.74% | -27.89% | -27.89% | — |

## 3 worst DEV months, before vs after (V1)
| month | unfiltered | V1 filtered |
|---|---|---|
| 2023-01 | -17.74% | -17.71% |
| 2021-02 | -15.67% | -16.90% |
| 2022-06 | -12.26% | -12.51% |

## Method / caveats
- Frozen prereg (commit 1153b15) was pushed BEFORE any result; mechanics, keywords and bars unchanged. Rule, costs, borrow, seed and panel are HOLDOUT-MOM-002's (panel sha256 4387206b..c071 asserted).
- Only DEV 2021-01..2024-12 was used; nothing dated after 2024-12-31 was requested or read. No holdout, no PR, no orders, no database.
- Plumbing fix: earnings-date availability came out at 94.8% coverage, so the removal is large; no-lookahead assumption (Nasdaq dates are pre-announced) is documented in the prereg.
- A pass would only have been a DEV candidate; confirmation would need a separate Josh-approved forward test.
- Runner: scripts_eventnews/{fetch_v1_earnings.py, fetch_v2_news.py (probe), run_eventnews.py}; results: scripts_eventnews/results/results_eventnews.json.

MAP.md: unchanged because research-only run in its own worktree, no repo code or conventions changed.
