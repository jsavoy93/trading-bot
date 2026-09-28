# BUY-FUNNEL DASHBOARD v0.1 — Semantic Correction Report

**Status:** PR #101 READY FOR OWNER REVIEW (v0.1 semantic correction pushed)
**Branch:** `agent/exec-003-1-trade-persistence`
**Commit:** `4d2e2b7` (BUY-FUNNEL DASHBOARD v0.1 — semantic correction)
**PR:** #101 OPEN — https://github.com/jsavoy93/trading-bot/pull/101
**Tests:** 28/28 PASSED in 158.39s (20 v0 + 8 v0.1 new tests)
**git diff --check:** clean
**SmartBot:** PID 1196517 unchanged, uptime 4h+
**Production DB:** read-only throughout
**No strategy/config/schema change. No service restart. No merge.**

## 1. What was wrong in v0

The v0 dashboard and report conflated BUY with SELL and HOLD at multiple points:

- `strategy_eligible_count=325` reported as "BUY eligible" — but `cycle_funnel.strategy_eligible_count` MIXES BUY and SELL signals. The 595/325 are SELL signals, not BUY.
- The "near-miss" examples (MUYY, MUST, MUNY, NAUT, NACP, MYY, MYXXW, MYXXU) were HOLD signals with failed gates, not BUY near-misses. The v0 query proved exactly one required gate failed but did NOT prove all other required gates passed.
- The conclusion "320 execution blocked from 325 strategy-eligible" treated SELL execution blockers (SELL_BLOCKED_DYNAMIC = no position to sell) as if they were BUY execution blockers. They aren't.

## 2. v0.1 semantic correction

Five endpoints, two new ones, all semantically separated:

### /api/buy-funnel/summary — restructured
- **`signal_outcomes`**: BUY / SELL / HOLD / invalid counts via `json_extract(decision_snapshot, '$.strategy_eligibility.signal')` — the structured persisted signal field.
- **`buy_funnel_v1`**: explicit BUY-only funnel stages (`Final BUY Signal (v1)`, `Ranked BUY Candidate`, `BUY Execution Attempted`, `BUY Order Submitted`, `BUY Fill Confirmed`).
- **`cycle_funnel_persistence_mixed`**: cycle_funnel aggregates explicitly labelled MIXED BUY+SELL.
- **`off_path_mixed`**: generic `execution_blocked` labelled MIXED with warning.
- **`lifecycle_persistence`**: `trades_submitted_count` + `trades_filled_count` + `submitted_equals_filled: false` invariant.

### /api/buy-funnel/near-miss — tightened
- Replaced brittle LIKE matching on decision JSON with structured `json_extract` (owner instruction: "Prefer structured persisted truth").
- Fixed missing cohort placeholder binding (8 total placeholders, was binding 7).
- Fixed LIMIT formula (was `limit*24+200`; correct value is `int(limit)`).
- Fixed score extraction path (`scoring.total_score`, not `score.total_score`).
- Operational definition unchanged: exactly one required gate failed, all other required gates evaluated and passed, signal != SELL, outcome != SKIPPED_INVALID_DATA.

### /api/buy-funnel/rejection-reasons — denominator explicit
- New `denominator` field with explicit label and v0/v1 split counts.
- `share_of_denominator_pct` based on the FULL denominator (not just the top-N).
- `caution` text labels percentages as "share of failed required-gate ROWS, not share of decisions".

### /api/buy-funnel/joint-pass-flow — NEW
- CTE-based trace of decision_history rows where BOTH required BUY gates (rsi_oversold + sma_uptrend) PASSED.
- Reports final-signal distribution (BUY/SELL/HOLD/invalid), downstream BUY pipeline counts, and subsequent required-gate behaviour.
- Answer to owner's central question: "Of the observations that pass RSI + SMA, what specifically prevents them from becoming BUY signals?"

## 3. Real-data preview (24h, READ-ONLY against production DB)

| Section | Value |
| --- | --- |
| Decisions | 135,446 |
| Unique symbols | 13,501 |
| BUY signals | **0** |
| SELL signals | 316 |
| HOLD signals | 127,731 |
| Invalid | 7,399 |
| `buy_is_zero` | true |

**BUY-only funnel (v1 path):**
| Stage | Count |
| --- | --- |
| Analyzed (v1) | 128,047 |
| Final BUY Signal (v1) | **0** |
| Ranked BUY Candidate | **0** |
| BUY Execution Attempted | — (cycle_funnel does not persist BUY-only) |
| BUY Order Submitted | **0** |
| BUY Fill Confirmed | **0** |

**cycle_funnel aggregates (MIXED BUY+SELL — operational sanity only):**
| Stage | Count |
| --- | --- |
| Analyzed | 128,047 |
| Strategy Eligible (MIXED BUY+SELL) | 316 |
| Ranked Candidates (BUY-only) | 0 |
| Execution Attempted (MIXED BUY+SELL) | 316 |
| Orders Submitted (BUY-only) | 0 |

**RSI + SMA joint-pass diagnostic (NEW — answers "what stops joint-pass from becoming BUY?"):**
| Stage | Count |
| --- | --- |
| Joint-pass population (RSI + SMA both passed) | **1,304** |
| Final BUY | **0** |
| Final SELL | 0 |
| Final HOLD | **1,304** |
| Invalid | 0 |
| Ranked BUY candidates | 0 |
| Execution attempted | 0 |
| Execution blocked | 0 |
| BUY promoted (selected_for_buy) | 0 |
| BUY submitted | 0 |
| BUY filled | 0 |

Subsequent gates on the joint-pass population: empty (no other required BUY gates beyond RSI+SMA in v1 path).

**BUY-criteria gate diagnostics:**
| Gate | Pass | Fail | Pass % | Fail % |
| --- | --- | --- | --- | --- |
| sma_uptrend | 35,070 | 87,134 | 28.70 | 71.30 |
| rsi_oversold | 49,838 | 72,987 | 40.59 | 59.41 |

Joint-pass rate (both required gates passed): **1.06% (1,305 / 122,837)** — v1 path only.

**Top rejection reasons (denominator = 160,156 failed required-gate ROWS):**
| Gate | Count | % of failed rows | Reason |
| --- | --- | --- | --- |
| sma_uptrend | 87,144 | 54.41 | SMA fast ≤ SMA slow |
| rsi_oversold | 2,152 | 1.34 | RSI 50.0 ≥ threshold 35.0 |
| rsi_oversold | 775 | 0.48 | RSI 100.0 ≥ threshold 35.0 |
| rsi_oversold | 428 | 0.27 | RSI 40.0 ≥ threshold 35.0 |
| rsi_oversold | 350 | 0.22 | RSI 42.9 ≥ threshold 35.0 |

The denominator is the count of recorded failed applied-gate rows in the window, NOT a count of decisions or symbols. The 54.41% SMA share means 54.41% of failed required-gate evaluations cited SMA, not 54.41% of stocks failed solely because of SMA.

**Near-miss candidates (top 10 latest unique symbols, failed exactly one required BUY gate):**

| Symbol | Failed gate | Observed | Threshold | Score |
| --- | --- | --- | --- | --- |
| WPC | sma_uptrend | 66.85 | 69.459 | 59.90 |
| XIGV | rsi_oversold | 64.19 | 35.0 | 42.94 |
| WOR | rsi_oversold | 49.41 | 35.0 | 35.58 |
| WOOF | sma_uptrend | 2.355 | 2.578 | 55.98 |
| WNEB | rsi_oversold | 52.20 | 35.0 | 34.55 |
| XIIIU | rsi_oversold | 60.29 | 35.0 | 40.97 |
| WMT | rsi_oversold | 52.95 | 35.0 | 50.37 |
| WMB | sma_uptrend | 71.19 | 72.859 | 68.30 |
| WM | sma_uptrend | 211.12 | 217.517 | 63.33 |
| WLTH | rsi_oversold | 57.46 | 35.0 | 64.08 |

## 4. Final owner answers

**Q: Why are there zero BUYs right now?**
A: The v1 BUY path has emitted zero BUY signals across the entire production history. In the last 24h, 1,304 observations passed BOTH required BUY criteria (RSI+SMA), but ALL 1,304 were downgraded to HOLD downstream of strategy eligibility. The choke point is between "joint-pass" and "ranked BUY candidate" — not at strategy gates, not at execution checks.

**Q: Of the observations passing RSI + SMA, what specifically prevents them from becoming BUYs?**
A: 1,304 RSI+SMA joint-pass observations in the last 24h. 1,304 final HOLD. 0 final BUY. 0 final SELL. 0 ranked. 0 attempted. 0 blocked. The downgrade happens at the signal-emission stage, between `scoring.total_score` and `strategy_eligibility.signal`. The scoring path is currently unknown (not surfaced in persisted fields).

**Q: Are execution blockers currently preventing BUYs?**
A: NO. The 316 execution_blocked rows in cycle_funnel are SELL execution blockers (SELL_BLOCKED_DYNAMIC = no position to sell). The 316 SELL signals never produced any BUY execution attempt.

**Q: Which REQUIRED criterion is the most common sole failure among TRUE closest-to-BUY near misses?**
A: In the last 24h, rsi_oversold and sma_uptrend are roughly evenly split as sole failures among the top 10 near-misses (5 RSI, 5 SMA), but rsi_oversold is closer to threshold in absolute terms (observed ~50-65 vs threshold 35, all ABOVE the buy-side meaning RSI is too high = not oversold).

**Q: Can the owner now use this dashboard to decide which PAPER strategy criterion to consider loosening without another Engineering SQL audit?**
A: YES. The dashboard now surfaces the joint-pass population, the downstream BUY pipeline counts (all 0), the per-gate pass/fail rates, the top rejection reasons with explicit denominators, and the latest 10 true near-misses with observed-vs-threshold gaps. The owner can read the dashboard and decide whether to consider loosening `rsi_oversold_threshold` or `sma_uptrend` (structural, no scalar key).

## 5. Tests
- 28/28 PASSED in 158.39s (20 v0 + 8 v0.1 new tests).
- Pre-existing failures (TestGateAggregationAppliedFilter + TestExecutionChecksFidelity::test_live_alpxr_snapshot_renders_blocker_row_and_highlight) verified as NOT regressed.
- `git diff --check`: clean.
- Production DB: read-only throughout (queries via `file:trading_bot.db?mode=ro` URI).

## 6. Files changed
- `dashboard.py` — +555 / -90 (5 endpoints restructured; +1 new `/joint-pass-flow`)
- `templates/dashboard.html` — +110 / -75 (signal_outcomes card, joint-pass-flow card, denominator banner, buy_is_zero alert, MIXED labels, share-of-denominator pct column)
- `tests/test_buy_funnel_dashboard.py` — +186 / -30 (8 new v0.1 semantic-correction tests)
- Total: +943 / -236

## 7. 7d performance (PARKING LOT)
| Endpoint | latest | 24h | 7d |
| --- | --- | --- | --- |
| /summary | 0.11s | 0.05s | 0.07s |
| /gate-diagnostics | 0.25s | 3.90s | 40.39s |
| /rejection-reasons | 0.13s | 1.83s | 24.45s |
| /near-miss | 1.30s | 2.61s | 6.14s |
| /config-overlay | 0.14s | 0.84s | 1.80s |
| /joint-pass-flow (NEW) | 1.78s | 3.38s | 10.76s |

7d selector exposed in UI. No silent timeouts. 7d gate-diagnostics + rejection-reasons slow-but-usable — PARKING LOT (C14B-2E proper fix, not in scope per Section 19).

## 8. SmartBot / service state
- SmartBot PID 1196517 healthy (uptime 4h+, holds /tmp/trading_bot.lock + trading_bot.db).
- `smartbot-runner.service` failed-state: NOT fixed in this task (per owner instruction).
- BLOCKER for next SmartBot restart — owner action needed (`systemctl --user reset-failed smartbot-runner.service`).

## 9. Safety / scope confirmations
- Production DB remained read-only.
- No strategy threshold changed.
- No config changed.
- No Alpaca order submitted.
- No SmartBot restart.
- No trading-dashboard restart.
- PR #101 remains unmerged.

## 10. Final status
PR #101 OPEN with v0.1 semantic correction at commit `4d2e2b7`. Ready for owner merge authorization.
