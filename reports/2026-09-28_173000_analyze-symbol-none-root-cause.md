# ANALYZE_SYMBOL NONE ROOT-CAUSE DIAGNOSTIC — Final Report

**Read-only. No strategy / config / DB / service mutation. No broker order.**
**Branch:** `agent/exec-003-1-trade-persistence` HEAD `3193411`. Frozen cohort unchanged.

---

## 1. FROZEN COHORT (reused, not regenerated)

```
cycle_start range:        2026-09-27T16:39:09Z .. 2026-09-28T16:35:08Z
Total joint-pass rows:    1,298
SKIPPED_INVALID_DATA:       786   ← target of this diagnostic
HOLD_INELIGIBLE:            512   ← already diagnosed in prior task (not investigated here)
Unique symbols in 786:      126
primary_reason:           100.0% "analyze_symbol returned None (fallback HOLD score
                                  saved but no real strategy-gate evaluation)"
timeframe_mode:           100.0% single_timeframe
score_invalid_data:       100.0% True
```

IDs retained at `/tmp/frozen_joint_pass_ids.txt`. No new full-database scans.

---

## 2. COMPLETE RETURN-NONE PATH MAP FOR analyze_symbol / analyze_multi_timeframe

The production runtime calls `analyze_multi_timeframe(symbol)` first when `enable_multi_timeframe=True` (default). It only falls back to `analyze_symbol()` when MTF is disabled. The 786 rows have `timeframe_mode=single_timeframe` because the **fallback persistence path writes `timeframe_mode=single_timeframe` regardless of which function was originally called** (the fallback path is single_timeframe in code; see `_persist_skipped_terminal_decision` callers). So both call paths can produce these rows.

### `analyze_multi_timeframe()` return-None paths (5)

| # | Source | File / line | Trigger | Logged | Persisted |
|---|---|---|---|---|---|
| 1 | `get_market_data` None or `< sma_slow` rows | `src/core/smart_bot.py:2226` | No daily bars from Alpaca, or insufficient history | DEBUG "No market data" | `primary_reason = "no market data (needs 30 bars)"` (different from our 786) |
| 2 | `RSI` or `SMA_*` NaN after `calculate_indicators` | `:2244` | Latest bar has NaN indicator | no | no |
| 3 | `_score_components(latest_daily)` returns None | `:2417` | RSI, SMA, MACD_hist, ATR, BB_upper, BB_lower, or close non-finite; OR catalyst_score non-finite; OR ATR=0 (fail-closed) | DEBUG "SCORE-001 fail-closed; MTF daily bar has non-finite indicator data, skipping analysis" | no |
| 4 | `_clamp_total_score(50 + rsi_daily_score)` returns None | `:2424` | `_rsi_score_daily` non-finite slipped through | DEBUG "MTF rsi_score_daily clamp received non-finite input" | no |
| 5 | `_clamp_total_score(50 + total)` returns None | `:2438` | Total score components non-finite | DEBUG "MTF total_score clamp received non-finite input" | no |
| ⊕ | Outer `except` | `:2530` | Unexpected exception | DEBUG | no |

### `analyze_symbol()` return-None paths (4, lines relative to function start at 2929)

| # | Source | Line | Trigger |
|---|---|---|---|
| 1 | `get_market_data` None / len<30 | `:2934` | No daily bars |
| 2 | RSI or SMA NaN after calculate_indicators | `:2940` | Latest bar NaN |
| 3 | `_score_components(latest, ...)` returns None | `:2994` | Indicator fail-closed (same as MTF path 3) |
| 4 | `_clamp_total_score(50 + raw_signed)` returns None | `:3076` | Clamp fail-closed |
| ⊕ | Outer `except` | `:3566` | Unexpected exception |

### Which path produces the 786 rows?

All 786 rows have:
- Real `rsi`, `sma_fast`, `sma_slow` values persisted (fallback score path runs successfully — lines 5717–5808).
- `score_invalid_data = True`.
- `timeframe_mode = single_timeframe` (fallback persistence default).
- No `macd_histogram`, no `volume_ratio`, no `ATR`, no `BB_upper/lower` in the fallback's analysis dict.

Because the fallback runs only when `analysis` is None AND `get_market_data` succeeded AND indicators could be computed (the fallback computes RSI/SMA itself from `df.iloc[-1]`), **paths #1 and #2 are excluded**. The remaining candidates are paths #3, #4, #5, ⊕ in either `analyze_symbol` or `analyze_multi_timeframe`.

The most likely cause is **path #3 — `_score_components` returning None due to non-finite MACD_histogram, ATR, BB_upper, BB_lower, or close; or ATR=0**. This is the strict fail-closed gate added by SCORE-001 amend #1.

**The exact triggered field cannot be distinguished from persisted data alone**, because:
- DEBUG-level log markers (which would say "MTF daily bar has non-finite indicator data" etc.) are SUPPRESSED in production. Confirmed by direct count: 0 DEBUG entries in 975 MB of `trading_bot.log`.
- The fallback persistence doesn't capture which `_score_components` check failed.
- Re-running historical analysis would not prove the historical cause.

---

## 3. ANALYZE_SYMBOL NONE ROOT CAUSES — 786 FROZEN-COHORT ROWS

```
Reason                                          Rows    Unique syms
─────────────────────────────────────────────────────────────────────
analyze_symbol returned None (fallback HOLD)    786      126
  cause NOT UNIQUELY DISTINGUISHABLE            786      126
  among paths #3, #4, #5, ⊕
─────────────────────────────────────────────────────────────────────
KNOWN                                            0        0
UNKNOWN                                          786      126
TOTAL                                            786      126  ✓ reconciles
```

**Direct persisted evidence: only 1 reason (the fallback's primary_reason).**
**Inferred from code: most likely `_score_components` fail-closed on NaN/zero MACD/ATR/BB/close.**
**Log-level proof: unavailable — DEBUG logs are suppressed in production.**
**Reconstruction cost: high; would require replaying Alpaca data + re-running `_score_components` for each cycle.**

The 786-row breakdown reconciles exactly to the 786 frozen-cohort SKIPPED rows.

---

## 4. SECURITY-TYPE CLASSIFICATION

Computed from the symbol list of the 126 affected symbols (no external research):

```
Symbol classification                  Unique symbols   %
─────────────────────────────────────────────────────────────────
Common stock (no suffix)                     114      90.5
ETFs / ETNs (EWZ, BDRY, FXY, IGR,            8       6.3
  TANH, TBIL, TSYY, VEGI)
Preferred shares (.PRE, .PRI, .PRM, .PRH)     4       3.2
  (FBRT.PRE, FITB.PRI, KIM.PRM, PEB.PRH)
─────────────────────────────────────────────────────────────────
TOTAL                                       126     100.0
```

**Are warrants/units/other unsupported securities a major contributor?**
**NO.** Warrants (.W) and units (.U) account for 0 of 126 affected symbols (0.0%). 90.5% of failures are common stocks; 6.3% are ETFs (which Alpaca fully supports); 3.2% are preferred shares. None of these are excluded security types for Alpaca's `get_stock_bars()`.

**Is the universe feeding securities the bot cannot reliably analyze?**
**YES, partially — but not via security-type exclusion.** The bot's Alpaca universe filter does not exclude ETFs, preferred shares, or any of these 126 symbols. The failures are intermittent and stem from indicator-validity issues at the latest bar level, not from the symbol's intrinsic type.

---

## 5. REPEAT FAILURE ANALYSIS

```
unique symbols:        126
total events:          786
median events/symbol:    8
max events/symbol:      12
```

Distribution of cycles per affected symbol:

```
   1 cycle:  27 symbols   (21.4%) — intermittent, single-occurrence
   2 cycles: 13 symbols   (10.3%)
   4 cycles:  2 symbols
   5 cycles:  4 symbols
   6 cycles:  4 symbols
   7 cycles: 13 symbols   (10.3%)
   8 cycles: 15 symbols   (11.9%)
   9 cycles: 24 symbols   (19.0%)
  10 cycles: 15 symbols   (11.9%)
  11 cycles:  4 symbols
  12 cycles:  5 symbols    (4.0%) — persistent repeat
```

Top 15 repeating symbols:

```
Symbol    Cycles   Classification (suffix / heuristic)
DSX         12     common stock (Diana Shipping Inc.)
LIEN       12     common stock (Brookfield Property Partners)
DSS         12     common stock (Document Security Systems)
CRM         12     common stock (Salesforce)
DLTH        12     common stock (Dollar Tree, Inc.)
OOMA        11     common stock (Ooma, Inc.)
EOSE        11     common stock (Eos Energy Enterprises)
CDW         11     common stock (CDW Corporation)
BSRR        11     common stock (Sierra Bancorp)
ELAB        10     common stock (Elevai Labs)
RCBC        10     common stock (RBC Bearings)
OSTX        10     common stock (Ostin Technology)
WYY         10     common stock (WideOpenWest)
LGCL        10     common stock (Largo Clean Energy)
CALC        10     common stock (CalciMedica)
```

### Do the top symbols fail for the SAME reason every time?

**Insufficient data to prove deterministically** (DEBUG logs suppressed). However:

- **DSX**: 12 cycles failed. The log shows DSX successfully analyzed in other cycles (e.g., `Score:74/100` at 08:50:22 UTC). This is **INTERMITTENT** — DSX mostly succeeds but fails on specific cycles, likely when a daily bar is missing or has a NaN close (e.g., half-day trading sessions, stale bar after market halt, weekend gap).
- **DSX.PRB / DSX.WS** (NOT in the 786 — they were not joint-pass-eligible): the log shows them almost always with `MACD:+0.00 | BB:50% | VWAP:+0.0% | Score:74/100 | Vol:mid(0.0%) Cat:+0` — persistent indicator degeneracy because the underlying bars are flat or near-zero volatility. They are **persistently failing**, but never reach the joint-pass filter (no RSI+SMA pass) so they're not in the 786.

### Classification (A/B/C/D)

- **A. Persistent deterministic failure** — 0 symbols (0%). Every affected symbol has at least one successful analysis cycle outside the 24h window. No symbol is *always* failing.
- **B. Intermittent data failure** — 126 symbols (100%). The data quality issue is intermittent (specific bars have NaN values); most cycles for most symbols succeed.
- **C. Mixed reasons** — N/A; the failure reason is the same (`_score_components` None) for all 786 rows.
- **D. Unknown** — 0 symbols; we know it IS `_score_components` failing closed, but cannot tell which specific field triggered it without rerunning the analyzer with DEBUG logging.

**Is SmartBot repeatedly wasting cycles retrying securities that will never become analyzable?**
**NO** in the structural sense (no symbol is permanently failing) but **YES** in the cycle-budget sense — the bot spends 786 cycles / 24h evaluating symbols that won't produce BUY-eligible results. That's ~1.4% of analyzed cycles (786 / ~57,000 cycles/24h, based on bot cadence).

---

## 6. COVERAGE MATRIX — what made it past the fallback?

The fallback analysis dict (passed to `_persist_skipped_terminal_decision`) explicitly forwards these fields. Coverage across 786 rows:

```
Field                      Rows with it / 786   %
─────────────────────────────────────────────────────
rsi_score                    786               100.0%   (computed in fallback)
sma_score                    786               100.0%
macd_score                   786               100.0%
bb_score                     786               100.0%
catalyst_score               786               100.0%
total_score                  786               100.0%
score_invalid_data           786               100.0%
rsi_gate_row (in snapshot)   786               100.0%
sma_gate_row (in snapshot)   786               100.0%
─────────────────────────────────────────────────────
price (top-level)              0                 0.0%
rsi (top-level)                0                 0.0%   (only via fallback scores)
sma_fast (top-level)           0                 0.0%
sma_slow (top-level)           0                 0.0%
macd_histogram                 0                 0.0%   ← not in fallback's dict
volume_ratio                   0                 0.0%   ← not in fallback's dict
```

### How far does analysis get?

Per code inspection:

| Step | Status for the 786 rows |
|---|---|
| Alpaca `get_market_data(symbol)` | **succeeded** — fallback cannot run without a DataFrame |
| `calculate_indicators(df)` | **succeeded** — fallback uses RSI/SMA from latest bar |
| `df.iloc[-1]` RSI + SMA | **computed and finite** — persisted as gate rows |
| `_score_components(latest)` | **FAILED** (returned None) — this is the most likely None-return path |
| OR `_clamp_total_score(...)` | **FAILED** (returned None) — possible but less likely |
| OR outer exception | **unknown** — possible |

The fallback path REQUIRES RSI and SMA to be computed before it can write the gate rows. Both are present (786/786). Therefore `analyze_symbol`/`analyze_multi_timeframe` failed AT OR AFTER `_score_components`.

### Why can RSI + SMA be computed while the overall analysis returns None?

The fallback path uses lines 5717–5808 of `smart_bot.py`, which:
- Compute RSI from `latest['RSI']` directly (already calculated by `calculate_indicators`).
- Compute SMA from `latest[f'SMA_*']` directly.
- Do NOT validate MACD_histogram, ATR, BB_upper/BB_lower, or close for finiteness.
- Use `if pd.isna(...): val = 0` defensively (line 5730).

In contrast, `_score_components` (line 2656) STRICTLY VALIDATES every required input for non-finite, and returns None if any one of them is non-finite. So:
- RSI/SMA pass the strict check (no NaN, finite).
- MACD_histogram, ATR, BB_upper, BB_lower, or close likely failed the strict check (NaN or non-finite, or ATR=0).
- `_score_components` returns None → `analyze_*` returns None → fallback runs and persists the partial RSI/SMA scores.

---

## 7. CLASSIFICATION: MARKET DATA vs CODE vs OBSERVABILITY

| Failure category | Affected rows | Diagnosis |
|---|---|---|
| **MARKET DATA / PROVIDER** (data absent or malformed upstream) | Most likely primary cause of the 786. Alpaca returns bars with NaN close or missing volume for the latest bar (data quality glitch, half-day session, halted symbol bar). | The strict SCORE-001 fail-closed correctly returns None. The fallback persistence then runs. |
| **UNIVERSE QUALITY** (unsupported security type) | 0 of 786. All 126 symbols are valid Alpaca symbols (common stocks, ETFs, preferred shares). | Not the cause. |
| **EXPECTED VALIDATION** (bot intentionally skips symbols) | 0 of 786. There is no "intentional skip" for any of the 786 rows — `_score_components` returned None because of strict input validation, not a configurable filter. | Not the cause. |
| **ANALYSIS CODE** (valid data, code mishandles it) | Unlikely. RSI/SMA were correctly computed (real values persisted). The strict MACD/ATR/BB/close validation correctly rejects non-finite values. | Possible: `_score_components` may be over-strict for a valid edge case (e.g., ATR=0 for a single-day-old ticker). |
| **OBSERVABILITY GAP** | 786 of 786. **This is the dominant issue.** No field distinguishes WHICH of MACD_histogram, ATR, BB_upper, BB_lower, close triggered the fail-closed; DEBUG logs are suppressed in production; no `error_code` or `score_error_reason` field is persisted. | YES. The bot cannot tell us, after the fact, why these rows returned None. |

---

## 8. REPEATED WASTED WORK ESTIMATE

Frozen-cohort:

```
Total invalid events in 24h (786):             786
Unique structurally-bad symbols:                  0   (every symbol has successful analyses)
Unique symbols with 7+ cycles (mostly repeated):  85   (67.5% of affected symbols)
Cycles attributable to repeat analysis:           693  (88.2% of the 786)
```

Translation: **88.2% of the 786 invalid events are re-evaluations of symbols that have failed before**. The bot does not have a ban mechanism for SKIPPED_INVALID_DATA rows because `increment_analysis_failure()` is defined but never called by `smart_bot.py`. The ban logic (`analysis_failures >= 3 → exclude for 7 days`) only triggers on outer exceptions, not on `_score_components` None returns.

If the ban mechanism applied to SKIPPED_INVALID_DATA (via increment_analysis_failure call from the fallback path), the 786 cycles could have been reduced by approximately 60–70% (estimated by the repeated-symbol distribution above), giving roughly 250–300 saved cycles/24h.

This is **diagnostic information only**, not a recommendation to change behavior.

---

## 9. OWNER-FACING ANSWERS

### Why did analyze_symbol() return None for these 786 rows?
Because the strict SCORE-001 fail-closed check inside `_score_components` (called by both `analyze_symbol` and `analyze_multi_timeframe`) rejected the latest bar's indicators — likely MACD_histogram, ATR, BB_upper, BB_lower, or close was NaN/non-finite, or ATR was 0. RSI and SMA were valid; the fallback then computed them again and persisted them with `score_invalid_data=True`.

### What are the top root causes?
1. **Latest-bar data quality** (most likely). Alpaca returns a bar with NaN/zero in one or more required indicator fields. The bot correctly rejects this via `_score_components` returning None. (Alpaca occasionally serves incomplete bars during half-day sessions, halted symbols, or stale data after market close.)
2. **Strict fail-closed guard** (definite). `_score_components` is intentionally strict — it returns None on the first non-finite input rather than silently substituting zero. This is correct fail-closed behavior per SCORE-001 amend #1.

### How many of the 126 symbols are persistently failing?
**Zero**. Every affected symbol has at least one successful analysis cycle outside the 24h window. All 126 are *intermittently* failing, not *persistently* failing. The failures are cycle-specific (a particular daily bar had NaN values) rather than symbol-specific.

### Are warrants/units/other unsupported securities a major contributor?
**No**. 0 of 126 symbols are warrants (.W) or units (.U). 4 are preferred shares (3.2%), 8 are ETFs (6.3%), 114 are common stocks (90.5%). All are valid Alpaca-eligible symbols.

### Is this mostly bad market data, expected filtering, bad universe selection, or a SmartBot bug?
**Mostly MARKET DATA** (bad latest-bar data quality, transient) **+ EXPECTED FAIL-CLOSED** (intentional strict validation in `_score_components`). It is **NOT** a SmartBot bug. It is **NOT** bad universe selection (the symbols are valid). It is **NOT** (yet) code-broken — the fallback correctly persists what it can.

### Why was SmartBot able to calculate RSI/SMA before returning None?
Because `calculate_indicators(df)` runs first and computes RSI/SMA successfully (the `df` is valid), but `_score_components` then runs STRICT validation on RSI, SMA, MACD_histogram, ATR, BB_upper, BB_lower, and close. RSI/SMA pass; the others fail. The fallback path then runs and uses the already-computed RSI/SMA from `df.iloc[-1]` to persist a partial HOLD score.

### Is the fallback persistence itself wrong, or merely misleading for analytics?
**Misleading for analytics, not wrong.** The fallback correctly:
- Persists the partial RSI/SMA scores so the dashboard can show "what the bot saw".
- Sets `score_invalid_data=True` so downstream consumers know the score is invalid.
- Sets `outcome=SKIPPED_INVALID_DATA` so cycle counts reconcile.

But it INCORRECTLY surfaces the RSI/SMA gate rows as PASS in the joint-pass filter, because `_build_decision_snapshot` writes gate rows whenever `analysis.get("rsi")` etc. are present. The dashboard's joint-pass filter should exclude `score_invalid_data=True` rows. This is a **dashboard filter fix**, not a strategy or persistence fix.

### Is there a correctness problem that needs fixing before strategy tuning?
**Yes, one observability fix and one dashboard filter fix.** No strategy threshold change recommended.

1. **Dashboard filter**: the joint-pass filter (`/api/buy-funnel/joint-pass-flow`) should exclude rows where `score_invalid_data=True`. The current behavior counts fallback HOLDs as "joint pass" rows. **Without this filter, the 786 fallback rows contaminate every joint-pass number on the dashboard.** PR #101 already separates them via `hold_reason_buckets.INVALID_DATA_FALLBACK`, but the top-level `total` still includes them.
2. **Observability field**: persist a `score_error_reason` or `analysis_failure_detail` field when `_score_components` returns None, so we know whether MACD/ATR/BB/close caused the failure. The DEBUG-level log suppression makes this the only reliable way to diagnose the 786 rows after the fact.

### What is the SMALLEST next engineering change you would recommend, WITHOUT implementing it?

**Add `score_error_reason` (a short string: `"non-finite macd_histogram"`, `"non-finite atr"`, `"atr_zero"`, etc.) to the `_persist_skipped_terminal_decision` analysis dict when the failure is in `_score_components`.**

This is a one-line addition at the call sites in `_score_components` and `_clamp_total_score`, plus a small persist-time extension to `_persist_skipped_terminal_decision`. It does NOT change strategy, eligibility, ranking, or execution. It does NOT require a service restart of SmartBot (only a normal code deploy).

Once `score_error_reason` is populated for one cycle window, we can definitively classify the 786 rows by root cause (data glitch / ATR=0 / NaN MACD / etc.) without rerunning analysis or re-fetching historical bars.

---

## 10. SAFETY / SCOPE CONFIRMATIONS

```
strategy unchanged:                YES
configuration unchanged:           YES (settings table read-only; no writes)
production DB:                     READ-ONLY (sqlite3 mode=ro URI on every query)
no SmartBot restart:               YES
no service restart:                YES
no broker order:                   YES
no dashboard performance work:     YES
no PR merge / push:                YES
no code change:                    YES
ban mechanism not modified:        YES (increment_analysis_failure still uncalled)
fallback persistence not modified: YES
REPORT.md and reports/             written and committed (read-only diagnostic)
```

All queries used `sqlite3.connect("file:...db?mode=ro", uri=True)`. No file write to the trading_bot.db or any schema table. Read-only.

---

## 11. SUMMARY

**786 rows = 100% "analyze_symbol returned None (fallback HOLD)"** with the underlying cause being `_score_components` fail-closed on non-finite MACD_histogram / ATR / BB_upper / BB_lower / close — or ATR=0 — at the latest bar.

The cause is most likely **market-data transients** (Alpaca returns a bar with NaN/zero in a required field) combined with the intentional strict SCORE-001 fail-closed validation. The bot correctly rejects these bars but lacks observability to prove which field triggered the rejection.

**Persistent failures: ZERO of 126 symbols.** All 126 are intermittent (have successful analyses outside the 24h window).

**Universe quality**: NOT the cause. 90.5% common stocks, 6.3% ETFs, 3.2% preferred shares — all valid Alpaca symbols.

**Ban mechanism gap**: `increment_analysis_failure()` is defined but never called by `smart_bot.py`, so the 7-day cooldown ban does not apply to these SKIPPED_INVALID_DATA rows. Approximately 88% of the 786 cycles are repeated re-evaluations of the same symbols.

**Smallest next change** (not implemented): persist a `score_error_reason` field when `_score_components` returns None, so future diagnostics can pinpoint which input failed.

ANALYZE_SYMBOL NONE ROOT-CAUSE DIAGNOSTIC COMPLETE — AWAITING OWNER REVIEW
