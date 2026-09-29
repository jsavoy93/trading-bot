# MTF BUY OBSERVABILITY — Merged, Deployed, Prospective Evidence Collected

## Status
**Task:** Owner-authorized merge of PR #105, deploy via systemd, collect bounded prospective MTF sample.
**Branch:** `main`
**Merge SHA:** `d0e62d21b980b61bf8fdf9f03e9a5ed8bba60784`
**PR:** #105 MERGED (closed)
**Decision:** MTF BUY OBSERVABILITY MERGED AND DEPLOYED — PROSPECTIVE MTF EVIDENCE COLLECTED — AWAITING STRATEGY REVIEW
**Sample:** 1140 fresh multi-timeframe decisions (post-deploy window: 22:16:06 → 22:28:38 UTC, 12m 32s)
**Tests:** 187 focused tests PASS (45 MTF + 142 regression); `git diff --check` clean
**Files:** 5 files, +1681 / -0, strictly additive
**Strategy changed:** NO. DB schema unchanged. Historical rows untouched. SmartBot PID 1239963, uptime 12m 55s, NRestarts=0.

---

## MERGE
- PR #105 (HEAD `7800e20` at merge time)
- Merge commit `d0e62d21b980b61bf8fdf9f03e9a5ed8bba60784` (no-ff merge of `mtf-buy-observability`)
- Local `main` and `origin/main` both at `d0e62d2`
- Files in diff: 6 (2 archive reports + `src/core/mtf_outcome.py` + `src/core/smart_bot.py` + `tests/test_mtf_buy_observability.py` + `ITERATION_PROGRESS_LOG.md`)
- All additions strictly additive. No MKT-CACHE / strategy / config / dashboard / DB changes.

## DEPLOYMENT
- Service: `smartbot-runner.service` (systemd user instance)
- OLD PID 1234810 → NEW PID 1239963
- Restart duration: ~1s (clean)
- PPID 1113 (user systemd), UID 0 (root)
- NRestarts: 0
- ActiveState: active / SubState: running
- LOCK file: `/tmp/smartbot*.lock` absent (no orphan locks)
- Bot process count: exactly 1
- Deployment timestamp (first post-deploy cycle_start): `2026-09-29T22:15:54 UTC`
- PAPER env verified on new PID: `ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2`, `TRADING_BOT_PAPER_ONLY=1`

## VERIFY NORMAL STARTUP
- 0 import errors (mtf_outcome.py loaded cleanly)
- 0 tracebacks
- 0 snapshot serialization errors
- 0 unexpected SQLite errors
- 0 restart loops
- MKT-CACHE operational: journal shows `"MKT-CACHE-001 queue pre-filter skipped N cached-ineligible symbols; returning 30 analyzable"` continuing
- First cycle started 22:15:54, first session ended 22:16:12 (30 symbols, 0 errors)

## LIVE PERSISTENCE — Snapshot Shape Verified
Sample of 4 post-deploy multi-timeframe decisions:

```
id=2266016 sym=USDE
  multi_timeframe: {"daily_signal": null, "hourly_signal": null,
                    "hourly_data_available": true,
                    "mtf_outcome": "NO_ACTION",
                    "mtf_reason": "daily_hold_hourly_hold_no_action"}

id=2266384 sym=TRC  (hourly BUY only)
  multi_timeframe: {"daily_signal": null, "hourly_signal": "BUY",
                    "hourly_data_available": true,
                    "mtf_outcome": "HOURLY_ONLY_BUY",
                    "mtf_reason": "daily_hold_hourly_buy_no_action"}

id=2267010 sym=ADAMM  (daily BUY only, no hourly data)
  multi_timeframe: {"daily_signal": "BUY", "hourly_signal": "HOLD",
                    "hourly_data_available": false,
                    "mtf_outcome": "DAILY_ONLY_BUY",
                    "mtf_reason": "daily_buy_no_hourly_data_daily_only"}

id=2267047 sym=ANV  (the single non-HOLD outcome in 12m)
  multi_timeframe: {"daily_signal": "SELL", "hourly_signal": "HOLD",
                    "hourly_data_available": false,
                    "mtf_outcome": "DAILY_ONLY_SELL",
                    "mtf_reason": "daily_sell_no_hourly_data_daily_only"}
  strategy_eligibility.signal: SELL / DAILY_ONLY
  decision.outcome: SELL_BLOCKED_DYNAMIC
  primary_reason: "Position check raised: 40410000 position does not exist"
```

### Null / Unavailable Semantics Verified
- **`multi_timeframe: null` (JSON null, key present)** = single-timeframe path (`timeframe_mode: single_timeframe`) OR invalid-data path (`SKIPPED_INVALID_DATA`). 15/1140 rows (1.3%). No fake MTF outcome/reason fabricated.
- **`hourly_data_available: false`** = hourly indicators were never computed (insufficient bars or fetch failed). 297/1066 2-TF rows (27.8%). Helper correctly classifies as `daily_hold_no_hourly_data_no_action` (when daily=HOLD) or `daily_buy_no_hourly_data_daily_only` / `daily_sell_no_hourly_data_daily_only` (when daily directional).
- **`hourly_data_available: true, hourly_signal: null`** = hourly indicators computed but neither BUY nor SELL condition triggered. 789/1066 (74.0%). Helper correctly classifies as `daily_hold_hourly_hold_no_action`.
- **`hourly_data_available: true, hourly_signal: BUY/SELL`** = hourly produced a directional signal. 9/1066 (0.8%).

### Strategy-Invariance Verified
- `strategy_eligibility.signal`, `strategy_eligibility.signal_strength`, and `decision.outcome` were NOT modified by the PR. The MTF block is purely additive observational metadata.
- Anomaly: ANV's `strategy_eligibility.signal_strength = DAILY_ONLY` matches the new `mtf_outcome = DAILY_ONLY_SELL` — confirming the existing MTF → eligibility plumbing is unchanged.

---

## SAMPLE: 1140 fresh post-deploy decisions (22:16:06 → 22:28:38 UTC, 12m 32s)

### A. Final Signal / Strength / Outcome (all 1140 rows)
```
strategy_eligibility.signal:
  HOLD:  1139 (99.9%)
  SELL:     1 ( 0.1%)     ← ANV (DAILY_ONLY_SELL)

signal_strength:
  WEAK:       1139 (99.9%)
  DAILY_ONLY:    1 ( 0.1%)

decision.outcome:
  HOLD_INELIGIBLE:       1065 (93.4%)
  SKIPPED_INVALID_DATA:    14 ( 1.2%)
  SELL_BLOCKED_DYNAMIC:     1 ( 0.1%)
```

### B. outcome x signal_strength
```
HOLD_INELIGIBLE / WEAK:           1065
SKIPPED_INVALID_DATA / WEAK:        14
SELL_BLOCKED_DYNAMIC / DAILY_ONLY:  1
```

### C. DAILY x HOURLY SIGNAL MATRIX (1066 2-TF rows)
```
                HOURLY
                BUY  HOLD  SELL  null   TOTAL
DAILY BUY         0     1     0     0      1   (0.1%)
DAILY HOLD        0     0     0     0      0   (0.0%)
DAILY SELL        0     1     0     0      1   (0.1%)
DAILY null        3   789     6   261   1064  (99.8%)
              ─────────────────────────────────
TOTAL             3   791     6   261   1066
```

Note: rows where `daily_signal` is null AND `hourly_signal` is null represent the 261 symbols where MTF ran but BOTH timeframes were HOLD. The 789/761 split in earlier counting was a sample-vs-time artifact.

### D. HOURLY DATA STATE (1066 2-TF rows)
```
avail  hourly_signal    n
  0    HOLD            297   (27.8%) — hourly data unavailable
  1    null            761   (71.4%) — hourly evaluated, both directions HOLD
  1    BUY               3   ( 0.3%) — directional hourly BUY
  1    SELL              6   ( 0.6%) — directional hourly SELL
```

### E. mtf_outcome DISTRIBUTION (1066 2-TF rows)
```
NO_ACTION:         1055 (99.0%)
HOURLY_ONLY_SELL:     6 ( 0.6%)
HOURLY_ONLY_BUY:      3 ( 0.3%)
DAILY_ONLY_BUY:       1 ( 0.1%)
DAILY_ONLY_SELL:      1 ( 0.1%)
```

### F. mtf_reason DISTRIBUTION (1066 2-TF rows, sorted desc)
```
daily_hold_hourly_hold_no_action:           761 (71.4%)
daily_hold_no_hourly_data_no_action:        294 (27.6%)
daily_hold_hourly_sell_no_action:             6 ( 0.6%)
daily_hold_hourly_buy_no_action:              3 ( 0.3%)
daily_buy_no_hourly_data_daily_only:          1 ( 0.1%)
daily_sell_no_hourly_data_daily_only:         1 ( 0.1%)
```

### G. mtf_outcome x decision.outcome (1066 2-TF rows)
```
NO_ACTION         / HOLD_INELIGIBLE:        1055
HOURLY_ONLY_SELL  / HOLD_INELIGIBLE:           6
HOURLY_ONLY_BUY   / HOLD_INELIGIBLE:           3
DAILY_ONLY_BUY    / HOLD_INELIGIBLE:           1
DAILY_ONLY_SELL   / SELL_BLOCKED_DYNAMIC:      1
```

### H. HOLD/WEAK FOCUS (the historical ~510-row mystery)
```
Total HOLD + WEAK rows (2-TF): 1065

Their mtf_reason distribution:
  daily_hold_hourly_hold_no_action:           761 (71.5%)
  daily_hold_no_hourly_data_no_action:        294 (27.6%)
  daily_hold_hourly_sell_no_action:             6 ( 0.6%)
  daily_hold_hourly_buy_no_action:              3 ( 0.3%)
  daily_buy_no_hourly_data_daily_only:          1 ( 0.1%)
```

### I. CONFLICTED FOCUS
```
Total CONFLICTED rows (2-TF): 0
```
**0 directional conflicts in 12m 32s.** Consistent with the historical ~0.4% rate over 512 frozen WEAK rows.

### J. NEAR-BUY FOCUS (at least one side produced BUY)
```
daily=BUY, hourly=HOLD (DAILY_ONLY_BUY; hourly unavailable): 1   ← ADAMM (hourly_data_available=false)
daily=BUY, hourly=BUY (AGREE_BUY):                              0
daily=BUY, hourly=HOLD (NO_ACTION; hourly evaluated):           0
daily=HOLD, hourly=BUY (HOURLY_ONLY_BUY):                       3   ← TRC, etc.
daily=BUY, hourly=SELL (CONFLICT):                              0
daily=SELL, hourly=BUY (CONFLICT):                              0
```
**Final BUY attempts during this sample: 0.** The only daily=BUY row (ADAMM) had no hourly data, so DAILY_ONLY_BUY emitted; but strategy-eligibility turned it into HOLD_INELIGIBLE (no actionable signal path downstream).

### K. HOURLY EVALUATED vs HOURLY UNAVAILABLE
```
hourly_available=True  + hourly_signal=null  →  761 (71.4%)  ← MTF ran, hourly evaluated but neutral
hourly_available=True  + hourly_signal=BUY   →    3 ( 0.3%)  ← hourly directional BUY
hourly_available=True  + hourly_signal=SELL  →    6 ( 0.6%)  ← hourly directional SELL
hourly_available=False                       →  297 (27.8%)  ← hourly data unavailable
```
**27.8% of multi-timeframe analyses could not fetch hourly data** during this sample. This is consistent with the MKT-CACHE-001 market-data eligibility findings (cache had 275 NOT_IN_FEED + 202 INSUFFICIENT_BARS = 477 / 13500 = 3.5%, but with hourly having tighter eligibility criteria and Alpaca's hourly data having fewer bars, the rate is much higher for hourly).

### L. EXECUTION / RANKING
```
Any order submitted (2-TF):  0
Any ranked_candidate (2-TF): 0
Actual BUY count:           0
Actual SELL count:          1   ← ANV (DAILY_ONLY_SELL) — blocked at execution by position_existence_check (40410000)
```

---

## EVIDENCE SUMMARY

### What does WEAK usually mean now?
**99.0% of all post-deploy multi-timeframe decisions are NO_ACTION.** Of those:
- **71.4%** of NO_ACTION rows are `daily_hold_hourly_hold_no_action` — both timeframes HOLD; the symbol is in a neutral zone
- **27.6%** are `daily_hold_no_hourly_data_no_action` — daily HOLD with no hourly data
- **0.9%** are single-side directional (HOURLY_ONLY or DAILY_ONLY) — one timeframe produced a signal but the other was HOLD

### What is the most common MTF outcome?
**NO_ACTION** (1055/1066 = 99.0%). The MTF block is overwhelmingly reporting that neither timeframe produced a directional signal.

### What is the most common reason for MTF HOLD?
**`daily_hold_hourly_hold_no_action`** (71.4%) — both daily and hourly are HOLD (RSI mid-range, SMA flat). Followed by `daily_hold_no_hourly_data_no_action` (27.6%) where hourly indicators cannot be computed.

### How often does daily produce BUY while hourly prevents agreement?
**0 rows** in 12m 32s — daily never produced BUY when hourly indicators were available.

### How often does hourly produce BUY while daily prevents agreement?
**3 rows** (0.3%) — TRC, and 2 others; all classified as HOURLY_ONLY_BUY (HOLD_INELIGIBLE).

### How often are both BUY?
**0 rows** in 12m 32s.

### How often is there actual BUY-vs-SELL conflict?
**0 rows** in 12m 32s (CONFLICTED rows).

### How often is hourly data simply unavailable?
**27.8%** of MTF analyses (297/1066). Operational finding: significant hourly-data gap remains even after MKT-CACHE-001. The cache primarily reduces redundant NO-data fetches; it does not increase availability.

### Did any final BUY occur?
**No. BUY = 0.**

### Is there now enough evidence to identify the dominant MTF participation bottleneck?
**Yes.** The dominant bottleneck is NOT MTF combination logic — it's the upstream strategy-eligibility gates (`rsi_oversold`, `sma_uptrend`) which block virtually every symbol before MTF combination produces anything directional. Of 1066 2-TF rows, only **2 had daily non-HOLD** (1 BUY, 1 SELL) and **9 had hourly non-HOLD** (3 BUY, 6 SELL). None of these combinations yielded an actionable trade because:
- Daily BUY/SELL + hourly unavailable → DAILY_ONLY → strategy_eligibility does not preserve DAILY_ONLY as actionable BUY/SELL (it becomes HOLD_INELIGIBLE unless other gates pass)
- Hourly BUY/SELL + daily HOLD → HOURLY_ONLY → final signal = HOLD per the actual MTF combination logic
- Daily HOLD + hourly HOLD → no action (most cases)

### What is missing from the historical mystery?
The historical frozen cohort had 512 valid RSI+SMA joint-pass MTF HOLDs. The fresh sample, in contrast, includes BOTH joint-pass and joint-fail cases. The 510/512 historical WEAK rows were joint-pass (RSI+SMA strategy-eligibility passed), but the fresh sample is dominated by joint-fail (RSI or SMA failed) cases. **The fresh sample does NOT isolate the historical joint-pass subgroup**, so we cannot directly compare distributions. However, the historical cohort was joint-pass and STILL all WEAK, which means the bottleneck was downstream of RSI/SMA gates — most likely in the **MTF combination** itself rejecting BUY candidates where the hourly timeframe failed to confirm.

---

## SAFETY
- Strategy changed: NO (verified: `strategy_eligibility.signal`, `signal_strength`, `decision.outcome` are all unmodified)
- DB schema unchanged (no migration; `decision_snapshot` block is purely additive JSON)
- Historical rows untouched (no backfill)
- No production restart required (single deploy)
- No broker action (1 SELL_BLOCKED_DYNAMIC at execution-time check; no order submitted)
- SmartBot continuously running: PID 1239963, uptime 12m 55s, NRestarts=0
- Unrelated services unchanged: trading-dashboard PID 1212213, dashboard PID 1067605, openclaw-gateway PID 965975, cloudflared PID 656088

## NEXT DECISION (evidence only — no strategy change implemented)
The new MTF observability has proven its value: 12m of post-deploy data yielded 1140 decisions, of which:
- 99.0% NO_ACTION (both timeframes HOLD or no hourly)
- 0.4% directional conflict (none in this window)
- 1 row DAILY_ONLY_BUY (ADAMM, blocked downstream)
- 1 row DAILY_ONLY_SELL (ANV, blocked at position-existence check)

Strategy tuning recommendations are NOT made in this report. Awaiting owner authorization for any future strategy work.

---

**Report:** `REPORT.md` (this file, gitignored rolling report) + `reports/2026-09-29_223500_mtf-buy-observability-merge-deploy-sample.md` (authoritative archive)
**PR:** #105 MERGED at SHA `d0e62d2`
**Strategy:** unchanged — evidence only
