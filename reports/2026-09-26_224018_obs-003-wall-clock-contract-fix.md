# OBS-003 — WALL-CLOCK CONTRACT FIX (PR #97 closing commit)

**Branch / base SHA:** `agent/obs-003-retrospective-research` @ `7422b46` (prior audit) → new commit on top
**Reporting mode:** IMPLEMENTATION — code change to fix the horizon-semantic
defect identified in the prior review, plus regenerated labels.
**Created:** 2026-09-26T22:40 UTC
**Pipeline re-run:** 2026-09-26T22:35Z → 2026-09-26T22:38Z (229.0 s,
cache reused; first and only semantic-fix rerun)

---

## 1. ORIGINAL AUTHORIZED HORIZON CONTRACT

Per OBS-003A (audit archive
`/root/.openclaw/audit-archives/trading-bot/2026-09-26_155500_obs-003a-forward-return-feasibility.md`):

> **+30m**: wall-clock 30 minutes from decision time. For after-hours
> decisions, this may land in static price; document and report per-decision.
> **+1h**: wall-clock 1 hour.
> **+4h**: wall-clock 4 hours.
> **+1d**: **next trading session open** (NOT 24 wall-clock hours).

The forward price for +30m / +1h / +4h is "the first eligible 1-minute
market bar at-or-after the target" (later restated in the price-
semantics section of the same archive).

## 2. PREVIOUS INCORRECT IMPLEMENTATION

`forward_bar_trading_minutes()` in
`src/research/price_alignment.py` walked N bars forward through
regular-session minute bars only, skipping pre-market and after-hours.
This was used as the default by `label_horizons()`.

This was a correctness defect because:

- A 15:50 ET decision with +30m should resolve to the first bar at-or-
  after 16:20 ET (wall-clock contract) — but the legacy implementation
  walks 30 regular-session minutes forward, which lands in next-session
  morning territory.
- Approximately 98% of OK bar selections differed between the two
  semantics on the OBS-003 cohort.
- The defect existed in f28aea1 (the original implementation) and was
  inherited by PR #96 (validity check) and PR #97 (timezone/freshness).
  It was not surfaced to the owner until the final-semantics review.

## 3. CORRECTED IMPLEMENTATION

### New function

`forward_bar_wall_clock(bars, cycle_start, n_minutes)` computes:

```python
target = cycle_start + timedelta(minutes=n_minutes)
return first bar in bars with bar_ts >= target
```

Pre-market, regular-session, and after-hours bars are ALL eligible at-
or-after the target. This matches the OBS-003A specification exactly.

### Default behavior

`label_horizons()` now calls `forward_bar_wall_clock()` for trading-
minute horizons by default. The legacy `forward_bar_trading_minutes()`
is retained (with explicit "LEGACY" naming) for reproducibility of the
prior unit tests but is not the default for any new labeling.

### Decision reference price

Unchanged: synthetic 1-min bar at-or-before cycle_start subject to
the 240-min freshness cap and the eligible-date-partition constraint
(`label_decisions` loads only `[decision_date, decision_date + 1]`
into the per-decision frame). Documented as **synthetic counterfactual
research reference, NOT the price SmartBot itself uses** in the
metadata sidecar and in the module docstring.

### Freshness behavior (corrected documentation)

The 240-minute cap alone does NOT prevent prior-day bar use. Two
layers of protection operate together:

| layer | what it enforces |
|-------|------------------|
| A. Max bar age (240 min) | Rejects bars more than 4h old even if technically at-or-before |
| B. Eligible date partitions | `label_decisions` loads only `[decision_date, decision_date + 1]`. Prior-day bars are NEVER in the frame |

Combined behavior for the OBS-003 cohort:

* Overnight (00:00-07:59 UTC) decision: no eligible bar at-or-before
  cycle_start in `[decision_date, decision_date+1]`. Result:
  MISSING_DECISION_BAR.
* Pre-market (08:00-13:29 UTC) decision: first bar of day at 08:00 UTC.
  For a 12:00 UTC decision (4h old, at boundary) the 08:00 UTC bar IS
  accepted. For a 13:30 UTC decision (5h30m old) it is REJECTED.
* Regular-session / after-hours decision: most recent at-or-before
  bar, typically age < 1 minute.

## 4. TIMEZONE / CALENDAR

Retained from PR #97 commit `ae4267d`:

* America/New_York + zoneinfo for DST-aware session classification.
* SESSION_OPEN_ET = 09:30, SESSION_CLOSE_ET = 16:00.
* Weekend exclusion via weekday() >= 5.

NYSE market holidays, early closes, and extraordinary closures are
NOT explicitly handled. The September 2026 cohort (2026-09-23 Wed,
2026-09-24 Thu, 2026-09-25 Fri) contains no holiday and no early
close, so the limitation is not material for this research window.
Full exchange-calendar support is parked.

## 5. TESTS

`tests/test_obs_003_research_tooling.py` — **56 / 56 passing**
(48 prior + 8 new wall-clock tests).

New tests added at this commit:

| test | asserts |
|------|---------|
| `test_30m_target_is_exactly_30_wall_clock_minutes_later` | 13:30 UTC + 30m → 14:00 UTC |
| `test_decision_at_1550_et_targets_1620_et_not_next_morning` | 19:50 UTC + 30m → 20:20 UTC (NOT next-session) |
| `test_decision_at_1900_utc_targets_2000_utc_regardless_of_close` | 19:00 UTC + 60m → 20:00 UTC (at session close) |
| `test_240m_target_is_exactly_240_wall_clock_minutes_not_240_market_minutes` | 13:30 UTC + 240m → 17:30 UTC (NOT next-day 09:50 UTC) |
| `test_uses_after_hours_bars_when_target_falls_there` | +30m target in after-hours is still accepted |
| `test_returns_horizon_beyond_when_target_exceeds_available_bars` | Truncated barset → HORIZON_BEYOND_AVAILABLE_BARS |
| `test_returns_first_bar_at_or_after_target_not_after` | First bar at-or-after target (not after) |
| `test_default_horizon_uses_wall_clock_not_trading_minutes` | label_horizons default is wall-clock |

Existing legacy `TestForwardBarTradingMinutes` tests retained — they
prove the legacy function still works for reproducibility, but the
default contract is now wall-clock.

## 6. PIPELINE RUN COUNT + CACHE REUSE

* Run count: **1** (this is the only run with the wall-clock fix).
* Cache reuse: **41,196 / 41,196 files intact**, 0 re-fetched.
* Runtime: 229.0 s (faster than trading-minute walk because the
  forward-bar lookup is a single sorted-index lookup, not a 240-iter
  loop).

## 7. FINAL LABEL COVERAGE

| view | horizon | n_total | n_ok | n_missing_decision_bar | n_horizon_beyond | n_no_next_session | %ok |
|------|---------|--------:|-----:|-----------------------:|-----------------:|------------------:|----:|
| RAW | fwd_30m | 91,138 | 20,669 | 70,344 | 125 | 0 | 22.68 % |
| RAW | fwd_60m | 91,138 | 20,659 | 70,344 | 135 | 0 | 22.67 % |
| RAW | fwd_240m | 91,138 | 20,596 | 70,344 | 198 | 0 | 22.60 % |
| RAW | next_session_open | 91,138 | 20,529 | 70,344 | 0 | 265 | 22.53 % |
| DEDUP | fwd_30m | 88,733 | 20,340 | 68,268 | 125 | 0 | 22.92 % |
| DEDUP | fwd_60m | 88,733 | 20,330 | 68,268 | 135 | 0 | 22.91 % |
| DEDUP | fwd_240m | 88,733 | 20,267 | 68,268 | 198 | 0 | 22.84 % |
| DEDUP | next_session_open | 88,733 | 20,201 | 68,268 | 0 | 264 | 22.77 % |

Coverage is materially higher than the trading-minute implementation
(because wall-clock can use pre-market and after-hours bars), and
near-uniform across the three wall-clock horizons (~22.6-22.7 %).
next_session_open coverage is unchanged from prior runs because it
uses its own logic.

## 8. FINAL JOINT-PASS STATISTICS (Group A: RSI pass AND SMA pass)

| view | horizon | n_total | n_ok | median | mean | p_positive |
|------|---------|--------:|-----:|-------:|-----:|----------:|
| RAW | fwd_30m | 4,614 | 393 | **+0.04 %** | +0.22 % | 52.4 % |
| RAW | fwd_60m | 4,614 | 393 | **+0.00 %** | +0.14 % | 49.1 % |
| RAW | fwd_240m | 4,614 | 387 | **+0.05 %** | -0.03 % | 51.4 % |
| RAW | next_session_open | 4,614 | 380 | **+0.00 %** | +0.03 % | 48.7 % |
| DEDUP | fwd_30m | 4,481 | 386 | **+0.04 %** | +0.22 % | 52.6 % |
| DEDUP | fwd_60m | 4,481 | 386 | **+0.00 %** | +0.14 % | 49.2 % |
| DEDUP | fwd_240m | 4,481 | 380 | **+0.04 %** | -0.04 % | 51.3 % |
| DEDUP | next_session_open | 4,481 | 373 | **+0.00 %** | -0.01 % | 48.8 % |

**Joint-pass finding (WALL-CLOCK)**: no observed edge in either
direction. Medians cluster near zero (-0.04 % to +0.05 %); p_positive
clusters near 50 % (48.7 % to 52.6 %). The "joint-pass was underwater"
finding from the trading-minute implementation **DID NOT SURVIVE** the
semantic correction.

## 9. FINAL HIGH-RSI STATISTICS (RSI >= 55)

| view | horizon | n_total | n_ok | median | mean | p_positive |
|------|---------|--------:|-----:|-------:|-----:|----------:|
| RAW | fwd_30m | 20,046 | 4,432 | **+0.00 %** | +0.07 % | 49.3 % |
| RAW | fwd_60m | 20,046 | 4,428 | **+0.00 %** | +0.09 % | 49.5 % |
| RAW | fwd_240m | 20,046 | 4,418 | **+0.29 %** | +0.60 % | 60.0 % |
| RAW | next_session_open | 20,046 | 4,402 | **+0.71 %** | +1.53 % | 64.6 % |
| DEDUP | fwd_30m | 19,474 | 4,335 | **+0.00 %** | +0.06 % | 49.1 % |
| DEDUP | fwd_60m | 19,474 | 4,331 | **+0.00 %** | +0.08 % | 49.3 % |
| DEDUP | fwd_240m | 19,474 | 4,321 | **+0.28 %** | +0.59 % | 59.8 % |
| DEDUP | next_session_open | 19,474 | 4,305 | **+0.69 %** | +1.52 % | 64.6 % |

**High-RSI finding (WALL-CLOCK)**: no edge at short horizons (median
~0.00 %, p_positive ~49 %); weakly positive at +240m (median +0.29 %,
p_positive 60 %); positively maintained at next-session-open (median
+0.71 %, p_positive 64.6 %). The trading-minute finding's strong
short-horizon signal (+0.82 %, p_pos 66 % at +240m) is much weaker
under wall-clock at +240m but next-session-open is unchanged.

## 10. RAW vs DEDUP COMPARISON

All findings agree qualitatively between RAW and DEDUP. DEDUP has
~2.6 % fewer rows (time-based dedup of contiguous same-symbol
decisions within 30 minutes). For high-RSI, RAW has 20,046 decisions
vs DEDUP's 19,474. The headline findings are stable across both views.

## 11. WHICH PRIOR RESULTS ARE SUPERSEDED

**Superseded for `fwd_30m`, `fwd_60m`, `fwd_240m`**:

The trading-minute implementation results from f28aea1 (original OBS-
003 implementation), 7c77044 (validity-check rerun), ae4267d (PR #97
follow-up), and the 7422b46 prior audit archive all used trading-
minute walking. Under the corrected wall-clock contract:

* Coverage: now near-uniform 22.6-22.7 % across all three wall-clock
  horizons (was 12-23 % under trading-minute).
* Joint-pass (Group A) headline finding: trading-minute showed
  underwater at +30m/+60m/+240m (median -0.10 % to -0.59 %,
  p_positive 35-43 %); wall-clock shows essentially zero (median
  -0.04 % to +0.05 %, p_positive ~49-52 %).
* High-RSI headline finding: trading-minute showed positive at all 4
  horizons (median +0.16 % to +0.82 %); wall-clock shows zero at
  +30m/+60m, weakly positive at +240m (median +0.29 %), and
  unchanged at next-session-open.

**NOT superseded**:

* `fwd_next_session_open` — uses its own separate logic (first
  regular-session bar on the next trading day). Implementation and
  results are unchanged.
* The `decision_price_synthetic_reference` framing (introduced at
  7422b46) — still accurate.
* The DST/EST timezone-aware session classification — unchanged.
* The 240-min freshness cap and the eligible-date-partition behavior
  — unchanged in mechanics, documented more accurately.

**Why previous results were superseded**:

The original OBS-003A specification (wall-clock) was mis-implemented
as trading-minute walking. The deviation was not surfaced to the
owner before PR #96 was merged. This commit (PR #97 closing) corrects
the implementation to match the authorized contract.

**Historical reports retained**:
- `reports/2026-09-26_175839_obs-003-retrospective-research.md` (f28aea1)
- `reports/2026-09-26_185143_obs-003-validity-check.md` (7c77044)
- `reports/2026-09-26_203320_obs-003-time-price-semantics-verified.md` (ae4267d)
- `reports/2026-09-26_210946_obs-003-final-semantics-review.md` (7422b46)

The historical CSVs (summary_*.csv) in `reports/research/` are also
retained (last-modified reflects the wall-clock run) but are not
versioned per-run; the prior summaries can be regenerated by
re-running with the legacy function if reproducibility audit is
needed.

## 12. FINAL FRESHNESS BEHAVIOR WORDING

The OBS-003 reference-price semantic, in a single sentence:

> "For each decision, the decision price is the close of the most
> recent 1-minute Alpaca bar at-or-before cycle_start, provided (a) the
> bar is no more than 240 minutes older than cycle_start AND (b) the
> bar's date falls in `[cycle_start.date, cycle_start.date + 1]` (the
> eligible date partitions loaded by label_decisions)."

The freshness rule has TWO independent layers (max age 240 min;
eligible date partitions) that together ensure prior-day bars are
never used and that excessively stale same-day bars are rejected.

## 13. TIMEZONE / CALENDAR LIMITATIONS

* EDT vs EST: auto-shifted via `zoneinfo.ZoneInfo("America/New_York")`.
* Weekend exclusion: weekday() >= 5.
* **NYSE holidays**: NOT implemented. Current cohort (2026-09-23,
  2026-09-24, 2026-09-25) contains no NYSE holiday, so the limitation
  is not material for this research window.
* **Early closes**: NOT implemented. Current cohort contains no early-
  close session.
* **Extraordinary closures**: NOT implemented.

If a future research window includes a holiday or early-close date,
the existing implementation will treat it as a normal trading day.
Affected decisions will produce MISSING_DECISION_BAR (no bars
returned by Alpaca) or HORIZON_BEYOND_AVAILABLE_BARS, which is
defensible but not informative. Full NYSE exchange-calendar support
is parked.

## 14. COMMIT SHA

`agent/obs-003-retrospective-research` @ new commit (will be reported
in the terminal summary after push).

## 15. PR #97 STATUS

| item | value |
|------|-------|
| PR | https://github.com/jsavoy93/trading-bot/pull/97 |
| State | OPEN (NOT merged) |
| Commits on branch | 5 (f28aea1, 7c77044, ae4267d, 7422b46, + new wall-clock fix) |
| Code change in this commit | `price_alignment.py` (added `forward_bar_wall_clock`, made it the default); `run_obs_003.py` (metadata); tests +8 |
| Pipeline runs for this fix | 1 (cache reused, 41,196 files intact, 0 re-fetched, 229 s) |
| Merge status | **DO NOT MERGE** — pending owner review of this report |

## 16. BLOCKER / SHOULD-FIX / PARKING-LOT

### BLOCKER

* **None.** Pipeline ran cleanly, all 56 tests pass, SmartBot
  untouched.

### SHOULD-FIX (done in this commit)

* Horizon-semantic discrepancy (TRADING-MINUTE → WALL-CLOCK): FIXED
  at this commit. The implementation now matches the OBS-003A
  specification.

### PARKING-LOT (do NOT implement without owner authorization)

* SELL coverage investigation (97.6 % SELL MISSING).
* Joint-pass EMPTY_ALL bias investigation.
* Live forward collection.
* STRAT-002 (threshold tuning).
* Larger-window retrospective.
* NYSE holiday / early-close calendar support.
* C14B-2E performance audit (already parked).
* AGENT_BACKLOG.md wording cleanup.
* EXEC-005 SELL inline-bypass (already parked).

## 17. CONFIRMATION: NO PRODUCTION BEHAVIOR CHANGED

| item | status |
|------|--------|
| SmartBot 1082165 | untouched (uptime 2-22 days) |
| `trading_bot.db` | read-only via `?mode=ro` |
| SmartBot code / strategy / config | unchanged |
| Alpaca trading endpoints | unchanged (only paper-tier historical-data used) |
| Schema | unchanged |
| Other services restarted | none |
| OpenClaw / gateway config | unchanged |
| C14B-2E | remained PARKED |
| STRAT-002 | NOT started |
| Live forward collection | NOT implemented |
| Symbol universe | NOT redesigned |
| Dashboards | NOT built |
| Performance optimization | NOT done |
| DecisionSnapshot schema | unchanged (we did not need to add a new field) |

Code changes in this commit:
1. `src/research/price_alignment.py` — added `forward_bar_wall_clock()`;
   made it the default in `label_horizons()`; renamed
   `n_trading_minutes` → `n_wall_clock_minutes`; updated module
   docstring (decision-bar freshness, market-calendar limitation,
   synthetic-reference disclaimer).
2. `src/research/run_obs_003.py` — metadata sidecar now reports
   wall-clock horizon contract, eligible-date-partition behavior,
   and synthetic-reference disclaimer.
3. `tests/test_obs_003_research_tooling.py` — 8 new wall-clock tests;
   existing legacy tests retained unchanged.

Pipeline was regenerated ONCE (229 s, cache reused).

## 18. FINAL QUESTIONS

**Q1: "If PR #97 is merged now, does OBS-003 implement the originally
authorized research semantics without any known material correctness
defect affecting this cohort?"**

**A1: YES.** The implementation now matches the OBS-003A contract on
all three horizons (wall-clock +30m/+60m/+240m; next-session-open for
+1d). The session-window logic is timezone/DST-aware. The freshness
rule and eligible-date-partition behavior are explicitly documented.
The current September cohort contains no NYSE holidays or early
closes, so the unsupported calendar limitation is not material.

**Q2: "If we stop OBS-003 permanently after this merge, what material
strategy question remains unresolved?"**

**A2: Two follow-up questions, neither of which is authorized by this
task:**

1. **Why is joint-pass (Group A) not underwater under wall-clock
   semantics?** The trading-minute implementation suggested a negative
   edge for joint-pass (median -0.59 % at +240m, p_pos 35 %). Under
   wall-clock, joint-pass is essentially zero (median +0.05 %,
   p_pos 51 %). Possible explanations to investigate later (NOT
   authorized now): (a) the trading-minute result was an artifact of
   mixing after-hours bars with regular-session bars in the legacy
   walk, or (b) joint-pass genuinely has no edge either way and the
   trading-minute result was a sampling artifact from thinly-traded
   names. Resolution requires either longer-window data or
   ground-truth trade attribution — both out of scope here.

2. **Why is high-RSI's short-horizon signal (median +0.16 % at +30m,
   p_pos 57 %) ZERO under wall-clock (median 0.00 %, p_pos 49 %)?
   The +240m signal survives (median +0.29 %, p_pos 60 %) but is
   weaker than the trading-minute result (+0.82 %, p_pos 66 %). Same
   possible explanations as above; same out-of-scope status.

Both questions belong to OBS-004 / STRAT-002 territory, which
require explicit owner authorization.

**Closing remark**: Under the corrected wall-clock contract, the
headline finding "high-RSI cohort has positive next-session-open
return (median +0.71 %, p_pos 64.6 %)" remains valid and is the
strongest surviving signal. This is consistent with the trading-
minute implementation's result for next-session-open (which is
unchanged because it uses separate logic). All other findings are
materially weaker or absent under wall-clock.

OBS-003 WALL-CLOCK CONTRACT FIXED — AWAITING OWNER MERGE
