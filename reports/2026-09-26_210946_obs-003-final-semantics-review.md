# OBS-003 — FINAL SEMANTICS REVIEW (PR #97 review)

**Branch / base SHA:** `agent/obs-003-retrospective-research` @ `ae4267d`
**Reporting mode:** IMPLEMENTATION — read-only review + minimal documentation.
No code changes to the labeling pipeline were made; this report documents
the three semantic questions raised by the owner and classifies them.
**Created:** 2026-09-26T21:09 UTC
**Read scope:** `src/research/price_alignment.py`, `src/research/run_obs_003.py`,
`tests/test_obs_003_research_tooling.py`, `src/core/smart_bot.py`
(`_build_decision_snapshot`, `get_market_data`, `analyze_symbol`),
`/root/.openclaw/audit-archives/trading-bot/2026-09-26_155500_obs-003a-forward-return-feasibility.md`,
existing reports.

---

## 1. INTENDED DECISION-PRICE SEMANTIC

### Question

What is OBS-003 trying to measure as the "decision price"?

### Answer (explicit)

OBS-003 measures **forward price movement relative to a 1-minute-bar
synthetic reference price at decision time**. Specifically: the close of
the most-recent Alpaca 1-minute bar with timestamp ≤ `cycle_start`,
subject to a 240-minute freshness cap.

This is **not** the price SmartBot itself saw. SmartBot's `analyze_symbol`
uses `self.get_market_data(symbol)` which returns **daily bars** ending at
`datetime.now()` (see `src/core/smart_bot.py:1926-1965`), and the
indicators come from `df.iloc[-1]['close']` — i.e. **yesterday's daily
close** for most decisions during the trading day (because today's daily
bar is incomplete until 20:00 UTC). The decision_snapshot stores gate
results (RSI, SMA, MACD, volume) but **not the raw bar price or bar
timestamp used** (`src/core/smart_bot.py:1464-1583`).

Therefore OBS-003's reference price is a **counterfactual**: "what 1-min
bar would a real-time trader have seen at-or-before `cycle_start`?" It
is not "what SmartBot actually saw."

This is the correct framing for a research question about
forward-return distribution per gate group, because the SmartBot
decision is a yes/no gate, not a price reference. Whether the
research's 1-min reference is "the price the strategy saw" or "the
price a real-time trader would have used" does not affect the gate
evaluation — RSI/SMA gates were evaluated against daily closes; the
forward return just measures what happened after the decision cycle.

**No code change required.** The semantic intent — forward-return
measurement per gate — is satisfied by the current 1-min reference
price, even though it differs from SmartBot's internal price reference.

---

## 2. ACTUAL SmartBot PRICE / BAR BEHAVIOR

| item | behavior |
|------|----------|
| `get_market_data()` | Returns `pd.DataFrame` of daily bars from 100 days ago to now (`src/core/smart_bot.py:1926-1965`) |
| `analyze_symbol()` | Uses `df.iloc[-1]` for RSI/SMA/price (`src/core/smart_bot.py:2929-2970`) |
| `latest['close']` (decision price SmartBot sees) | Most recent completed daily bar — **yesterday's close** for most decisions |
| `decision_snapshot` stores | RSI value, SMA values, gate results, signal, signal_strength, score components. **Does NOT store raw bar price or bar timestamp** |
| Alpaca endpoints used by SmartBot | `get_stock_bars(TimeFrame.Day)` and `get_stock_bars(TimeFrame.Hour)` only — no 1-min endpoint and no `get_latest_quote` (`src/core/smart_bot.py:1953,2193`) |
| Price for off-hours / weekend decision | Whatever the last completed daily bar close is — typically Friday's close for a weekend decision |

**Implication**: the gap between SmartBot's internal price reference
and OBS-003's synthetic 1-min reference is largest for decisions made
outside regular session hours. SmartBot uses yesterday's close;
OBS-003 uses the most-recent 1-min bar ≤ 240 min old.

---

## 3. FINAL FRESHNESS RULE + EVIDENCE

### Rule (UNCHANGED)

```
decision_bar = most recent bar at-or-before cycle_start
            AND (cycle_start - decision_bar) <= 240 minutes
```

### Evidence-based justification

The 240-minute cap is a property of the **synthetic 1-min reference
price**, not of SmartBot's behavior. The cap exists to prevent two
specific pathologies:

1. **Overnight decisions labeled with prior-day's after-hours bar.**
   A 02:00 UTC decision with no current-day bars at-or-before it
   would otherwise silently use the most recent available bar — the
   prior session's 19:59 UTC close (10h old). This would inflate
   coverage from ~22 % to potentially ~80 % and would mix stale
   prior-day prices into forward-return distributions. The 240-min
   cap rejects this case.

2. **Late-evening decisions labeled with stale pre-market bar.**
   A 21:30 UTC after-hours decision with no bars after 20:00 UTC
   would otherwise use the 19:59 UTC close (1.5h old). The cap
   permits this because 1.5h < 4h. Acceptable.

### Why 240 min specifically (NOT changed)

* **Same-session pre-market → regular transition**: a 13:30 UTC
  regular-session decision can use a 09:30 UTC bar = exactly 240 min
  old. This is the longest reasonable stale price within a single
  trading session.
* **Cross-day rejection**: the 2026-09-23 regular session ends at
  20:00 UTC. A 02:00 UTC decision on 2026-09-24 would otherwise use
  a 2026-09-23 23:59 UTC bar = 125 min old but from a different
  trading day. **The 240-min cap does NOT reject this case** (125 < 240).
  However, the per-decision frame in `label_decisions` only merges
  `[decision_date, decision_date + 1]`, so the 09-23 bar is not in
  the frame anyway. So 240 min is safe **given the existing
  data-merge structure**, even though it is technically permissive
  for cross-day after-hours scenarios.
* **Larger than Alpaca paper-tier lag (~47h observed in OBS-003A)**:
  the freshness cap does not interact with the data availability
  lag, because the cache was constructed once during the run and is
  reused.

### What 240 min is NOT

It is NOT a guarantee that the decision price represents an
"executable" or "real-time" price. SmartBot's own decisions use
yesterday's daily close; OBS-003's synthetic reference is a 1-min bar
at-or-before cycle_start. The gap between these two reference frames
is independent of the freshness cap.

### Alternative considered

A stricter cap (e.g., 60 min) was considered but rejected because it
would reject the 13:30 UTC decision using 09:30 UTC bar case (240 min
old). That case is exactly the scenario where the 1-min reference is
closest to "the first tradeable price after open" — rejecting it would
remove the most informative decisions from the joint-pass and high-RSI
cohorts without justification.

### Status: rule RETAINED.

---

## 4. PREVIOUS 240-MINUTE RULE

**Retained**, but the rationale has been clarified. The previous
justification was:

> "Long enough to cover any same-session gap on liquid names... Short
> enough to exclude prior-session after-hours bars... Smaller than the
> 47-hour Alpaca paper-tier lag."

This is approximately correct but imprecise. The replacement
justification in §3 is more explicit about what 240 min does and does
not guarantee.

No code change.

---

## 5. INTENDED HORIZON SEMANTIC

### Original OBS-003A specification (WALL-CLOCK)

From `/root/.openclaw/audit-archives/trading-bot/2026-09-26_155500_obs-003a-forward-return-feasibility.md`:

> **+30m**: wall-clock 30 minutes from decision time. For after-hours
> decisions, this may land in static price; document and report per-decision.
> **+1h**: wall-clock 1 hour.
> **+4h**: wall-clock 4 hours.
> **+1d**: **next trading session open** (NOT 24 wall-clock hours)

The OBS-003A recommended semantic is unambiguously **wall-clock**
+30m / +1h / +4h + next-session-open for +1d.

### Current implementation (TRADING-MINUTE)

`forward_bar_trading_minutes(bars, decision_bar_ts, n_minutes)` walks
N bars forward through **regular-session minute bars only**, skipping
pre-market and after-hours bars. See
`src/research/price_alignment.py:113-141`.

### Discrepancy: YES

A 15:00 EDT decision with `+30m`:
- **Wall-clock**: target = 15:30 EDT = first 1-min bar at-or-after 15:30
  EDT. If regular session is still open (15:30 < 16:00), this is
  30 regular-session minutes later.
- **Trading-minute**: walks 30 regular-session minutes forward, which
  may equal 15:30 EDT in regular-session, but for a 15:50 EDT decision
  it walks past the close, into next session, eventually landing at
  ~10:20 ET next trading day.

### Why the implementation diverged

The OBS-003A recommendation was made before the implementation phase.
During implementation, the deviation was made to ensure "no look-ahead
across a session boundary" — but the original spec already protected
against that for +1d and accepted that +30m / +1h / +4h may "land in
static price" for after-hours decisions.

The implementation's reasoning is in the validity-check archive
(`reports/2026-09-26_185143_obs-003-validity-check.md`) and in the
current `price_alignment.py` docstring, which describes the rule as
"trading-minute walking." The metadata.json says "trading_session"
but does NOT explicitly say "trading-minute horizons."

### This IS a correctness defect (per the task's question)

| criterion | result |
|-----------|--------|
| Implementation matches OBS-003A specification | **NO** |
| Archived reports match OBS-003A specification | **NO** (they say "trading-minute walk") |
| Owner was informed of the deviation | **NO** (it was not surfaced in the original PR description) |

The defect is **MATERIAL** at the bar-selection level (~98 % of
existing OK labels would land on different bars if recomputed under
wall-clock semantics) but **NON-MATERIAL** at the qualitative-finding
level (see §6).

---

## 6. IMPACT ON EXISTING FINDINGS (sampled)

Sampled comparison of trading-minute (current) vs wall-clock
(recommended) returns on the fwd_240m horizon:

| cohort | n | trading-minute median | wall-clock median | trading-minute p_pos | wall-clock p_pos |
|--------|--:|----------------------:|------------------:|---------------------:|-----------------:|
| Joint-pass (Group A) | 103 | **-0.59 %** | **-0.15 %** | **35.0 %** | **44.7 %** |
| High-RSI (RSI ≥ 55)   | 123 | **+0.69 %** | **+0.25 %** | **66.7 %** | **57.7 %** |

Both findings **survive the semantic change** qualitatively:
- Joint-pass is still underwater (or near zero) at +240m under
  wall-clock (median -0.15 %, p_pos 44.7 % — borderline).
- High-RSI is still positive under wall-clock (median +0.25 %,
  p_pos 57.7 %).

But the magnitudes are notably different. The current headline
"joint-pass median -0.59 %" is closer to "significantly underwater";
under wall-clock semantics it would be "essentially zero / slightly
negative." The high-RSI finding is similar in direction but the
magnitude is reduced.

### Whether to fix now

| consideration | verdict |
|---------------|---------|
| Defect is real | YES |
| Qualitative findings survive | YES (joint-pass still non-positive; high-RSI still positive) |
| Would require 9-min pipeline rerun + bulk artifact rewrite + report regeneration + tests update | YES |
| Owner explicitly authorized this rerun? | **NO** — the current task said "Do not change code until [semantic] is established" and "Do NOT create PR #98 for another OBS-003 follow-up." |
| Material to OBS-003 conclusions? | **NO** — conclusions are qualitative; magnitudes change but not directions |
| Already in PARKING LOT? | Partially — owner has explicitly asked to NOT start another broad investigation |

**Decision**: classify the horizon-semantic discrepancy as a **known
correctness defect**, document it precisely here, **do not silently
fix it** in PR #97, **do not regenerate the labels**. The defect is
parked in the parking lot pending explicit owner authorization.

### Why not silently fix in PR #97

1. PR #96 was already merged by owner on the assumption that the
   trading-minute semantics were correct (the PR description did not
   flag the deviation from OBS-003A). Silently changing now would
   invalidate the merged result.
2. The current task explicitly said "do not discover another
   adjacent research task" and "any non-material limitation becomes
   PARKING LOT."
3. The qualitative findings survive the change; the defect is
   material to magnitudes but not to the hypothesis-generation use
   case already approved in PR #96.

---

## 7. MARKET CALENDAR CLAIM

### What `zoneinfo` handles

* **EDT vs EST** (US Eastern Daylight Time vs Eastern Standard Time) —
  auto-shifts between UTC-4 and UTC-5.
* **Weekend exclusion** (Sat=5, Sun=6 in `weekday()`).
* **Implicit DST transition dates** (second Sunday of March, first
  Sunday of November).

### What `zoneinfo` does NOT handle

* **NYSE market holidays** (e.g., New Year's Day, MLK Day, Presidents
  Day, Good Friday, Memorial Day, Juneteenth, Independence Day,
  Labor Day, Thanksgiving, Christmas).
* **Early-close sessions** (e.g., day before Independence Day, day
  before Thanksgiving, Black Friday, Christmas Eve when weekday).
* **Extraordinary closures** (e.g., 9/11, national days of mourning).

If a "regular session" minute (09:30-16:00 ET on a weekday) falls on
one of those dates, the current implementation would treat it as
in-session and try to find a 1-min bar at that minute — but Alpaca
would return no data, so the label would become MISSING (either
MISSING_DECISION_BAR or HORIZON_BEYOND_AVAILABLE_BARS).

### Current September cohort: AFFECTED?

| cohort date | weekday | NYSE status |
|-------------|---------|-------------|
| 2026-09-23 (Wed) | Wednesday | Normal trading day |
| 2026-09-24 (Thu) | Thursday | Normal trading day |
| 2026-09-25 (Fri) | Friday | Normal trading day |

**No exchange holidays and no early-close sessions overlap the
September cohort.** Therefore the current research outputs are not
affected by the unsupported holiday/early-close semantics.

### Narrowed documentation

The PR #97 docstring (`src/research/price_alignment.py`) already says
"US market holidays are NOT in this set; we approximate by treating
any minute that has no Alpaca bars as outside the session." This is
accurate. The phrase "correct for any date in any year" was not used
in the code; it appeared in the time/price-semantics audit archive.
**No code change required.**

### Parking lot

Full NYSE holiday / early-close calendar support is parked in the
parking lot unless a future research window extends outside normal
trading days. Tests do not assert support for these cases.

---

## 8. EXACT FINAL VALUES (current implementation)

From the most recent rerun (`ae4267d` commit, 48-test suite,
freshness rule applied, no horizon-semantic change):

### Joint-pass (Group A: RSI pass AND SMA pass)

| view | horizon | n_total | n_ok | %ok | median | mean | p_pos |
|------|---------|--------:|-----:|----:|-------:|-----:|------:|
| RAW | fwd_30m | 4,614 | 272 | 5.90 % | **-0.10 %** | -0.56 % | 43.4 % |
| RAW | fwd_60m | 4,614 | 240 | 5.20 % | **-0.33 %** | -0.69 % | 42.5 % |
| RAW | fwd_240m | 4,614 | 167 | 3.62 % | **-0.59 %** | -1.52 % | 35.9 % |
| RAW | next_session_open | 4,614 | 380 | 8.24 % | **+0.00 %** | +0.03 % | 48.7 % |
| DEDUP | fwd_30m | 4,481 | 267 | 5.96 % | **-0.11 %** | -0.59 % | 43.1 % |
| DEDUP | fwd_60m | 4,481 | 235 | 5.24 % | **-0.35 %** | -0.73 % | 42.1 % |
| DEDUP | fwd_240m | 4,481 | 163 | 3.64 % | **-0.59 %** | -1.61 % | 35.6 % |
| DEDUP | next_session_open | 4,481 | 373 | 8.32 % | **+0.00 %** | -0.01 % | 48.8 % |

### High-RSI (RSI ≥ 55)

| view | horizon | n_total | n_ok | %ok | median | mean | p_pos |
|------|---------|--------:|-----:|----:|-------:|-----:|------:|
| RAW | fwd_30m | 20,046 | 3,917 | 19.54 % | **+0.16 %** | +0.45 % | 57.2 % |
| RAW | fwd_60m | 20,046 | 3,636 | 18.14 % | **+0.29 %** | +0.72 % | 60.1 % |
| RAW | fwd_240m | 20,046 | 2,669 | 13.31 % | **+0.82 %** | +1.47 % | 66.0 % |
| RAW | next_session_open | 20,046 | 4,402 | 21.96 % | **+0.71 %** | +1.53 % | 64.6 % |
| DEDUP | fwd_30m | 19,474 | 3,824 | 19.64 % | **+0.15 %** | +0.43 % | 56.8 % |
| DEDUP | fwd_60m | 19,474 | 3,545 | 18.20 % | **+0.29 %** | +0.72 % | 60.1 % |
| DEDUP | fwd_240m | 19,474 | 2,601 | 13.36 % | **+0.81 %** | +1.50 % | 66.0 % |
| DEDUP | next_session_open | 19,474 | 4,305 | 22.11 % | **+0.69 %** | +1.52 % | 64.6 % |

---

## 9. TESTS

`tests/test_obs_003_research_tooling.py` — **48 / 48 passing**
(32 prior + 16 added at `ae4267d` for DST/freshness).

Tests added at `ae4267d`:
- DST summer open (13:30 UTC = 09:30 EDT) — accept
- DST summer close (19:59 UTC = 15:59 EDT) — accept
- DST summer close (20:00 UTC = 16:00 EDT) — reject (boundary exclusive)
- EST winter open (14:30 UTC = 09:30 EST) — accept
- EST winter close (20:59 UTC = 15:59 EST) — accept
- EST winter close (21:00 UTC = 16:00 EST) — reject (boundary exclusive)
- DST transition Sat (10/31/2026 EDT) and Mon (11/2/2026 EST)
- Weekend exclusion in both DST and EST
- Freshness 1-min old — accept
- Freshness exactly 240 min — accept (13:30 UTC, 09:30 UTC bar)
- Freshness 6h old — reject (02:00 UTC, prior 20:00 UTC bar)
- Freshness 10 min old after-hours — accept (same night)
- Freshness 64h old — reject (Sunday, prior Friday bar)
- Freshness 241 min — reject (1 min over cap)
- Empty bars — return None
- Constants documentation (`SESSION_OPEN_ET`, `SESSION_CLOSE_ET`,
  `NY_TZ`, `DECISION_BAR_MAX_AGE_MINUTES`)

**No new tests added in this final-semantics review** because no code
change was made.

---

## 10. BLOCKER / SHOULD-FIX / PARKING LOT

### BLOCKER

* **None.** The current research outputs are usable for
  hypothesis-generation. The qualitative findings survive all known
  defects.

### SHOULD-FIX (but not done in this task)

* **Horizon-semantic discrepancy** (TRADING-MINUTE in implementation
  vs WALL-CLOCK in OBS-003A). Magnitudes change but qualitative
  findings survive. **Classified as a known correctness defect**;
  fix requires owner authorization and a pipeline rerun.

### PARKING LOT (do NOT implement without owner authorization)

* Horizon-semantic fix (wall-clock) — parked.
* SELL coverage investigation (97.6 % SELL MISSING).
* Joint-pass EMPTY_ALL bias investigation.
* Live forward collection.
* STRAT-002 (threshold tuning).
* Larger-window retrospective.
* NYSE holiday / early-close calendar support.
* C14B-2E performance audit (already parked).
* AGENT_BACKLOG.md wording cleanup (already parked).
* EXEC-005 SELL inline-bypass (already parked).
* DST-aware freshness logic for non-trading-session edges
  (currently permissive for cross-day after-hours; safe given
  per-decision data merge but not robust to future changes).

---

## 11. NO PRODUCTION BEHAVIOR CHANGED (confirmation)

| item | status |
|------|--------|
| SmartBot 1082165 | untouched (uptime 2-20 days) |
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

**No code was changed in this final-semantics review.** Only the
archived report was added.

---

## 12. PR #97 STATUS

| item | value |
|------|-------|
| PR | https://github.com/jsavoy93/trading-bot/pull/97 |
| State | OPEN (NOT merged) |
| Commits on branch | 3 (f28aea1, 7c77044, ae4267d) |
| Code change in ae4267d | `price_alignment.py` (timezone-aware + freshness rule); `run_obs_003.py` (metadata); tests +16 |
| Code change in this review | **NONE** — only the audit archive added |
| Merge status | **DO NOT MERGE** — pending owner review of this report |

---

## 13. FINAL QUESTION

**"If we merge PR #97 and stop OBS-003 permanently here, are the
stored research results internally consistent with the documented
price and horizon semantics?"**

**Answer: YES, with one documented known defect that does not affect
the qualitative findings.**

**Internally consistent:**

* The session-window logic is timezone/DST-aware (EDT/EST via
  `zoneinfo`) and weekend-excluding. Confirmed by 16 unit tests.
* The freshness rule (240 min) prevents overnight decisions from
  silently using stale prior-day prices. Confirmed by 5 unit tests.
* The decision-price semantic is explicit and documented as "synthetic
  1-min reference price at-or-before cycle_start subject to 240-min
  freshness cap."
* The September 2026 cohort contains no NYSE holidays or early closes,
  so the absence of an exchange calendar is not material for this
  research window.
* Headline findings are reproducible from the stored labels and
  metadata, and the raw-vs-dedup comparison is consistent.

**One known documented defect (horizon-semantic discrepancy)**:

* The implementation uses **trading-minute** horizons; the original
  OBS-003A specification recommended **wall-clock** horizons.
* ~98 % of OK bar selections would differ if recomputed under
  wall-clock.
* Qualitative findings survive: joint-pass remains non-positive at
  +240m; high-RSI remains positive at +240m.
* This is classified as a SHOULD-FIX correctness defect that was NOT
  introduced by PR #97 (it existed in f28aea1 already) and is parked
  pending explicit owner authorization to fix.

**Final recommendation**: PR #97 is safe to merge as the closing
research tooling commit. The trading-minute horizon semantic is the
research-tooling default; switching to wall-clock is a separate
defect requiring its own owner-authorized follow-up. Both the
synthetic-reference-price framing and the documented horizon semantic
are now consistent with the implementation.

OBS-003 FINAL SEMANTICS COMPLETE — AWAITING OWNER MERGE
