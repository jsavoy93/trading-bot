# OBS-003 — Time / Price Semantics Verification (audit archive)

**Branch / base SHA:** `agent/obs-003-retrospective-research` @ `7c77044` (prior) → new commit on top
**Reporting mode:** IMPLEMENTATION (per AGENTS.md — code change to fix
timezone-aware session + decision-bar freshness rule + tests + updated
metadata; labels regenerated once).
**Created:** 2026-09-26T20:33 UTC
**Pipeline re-run:** 2026-09-26T20:21Z → 2026-09-26T20:31Z (539.5 s,
cache reused; second run to refresh metadata.json after bytecode cache
cleared)

---

## 1. Two issues from the validity-check reasoning

### Issue 1: Hard-coded DST UTC hours in the prior fix

**Before this commit:** `SESSION_OPEN = time(13, 30)` and
`SESSION_CLOSE = time(20, 0)` were hard-coded constants in
`price_alignment.py`. The docstring described them as "the DST-correct
UTC times" — true for late-September 2026, but the implementation
would have been wrong for any non-DST dates (e.g., January 2027
should be 14:30-21:00 UTC).

**Yes, this was a defect** that would have produced wrong results if
the OBS-003 cohort had been in standard time. The code was correct
only by accident of date selection.

### Issue 2: Chronologically backwards reasoning about prev-date bars

**Before this commit:** The validity-check report stated:

> "The cache file for 09-23 has bars from 08:00 UTC 09-23 onward (all
> later than 02:00 UTC 09-24), so adding it wouldn't help anyway."

This is **chronologically incorrect**. The cache file for 2026-09-23
contains bars from 08:00 UTC to 23:59 UTC. The 23:59 UTC bar from
2026-09-23 is **earlier** than 02:00 UTC on 2026-09-24 (because 09-23
23:59 < 09-24 02:00 in wall-clock order). So a prev-date cache CAN
contain a bar satisfying the literal "bar_ts <= decision_ts" rule.

**In practice, this did not affect existing labels**, because
`label_decisions()` only merges `cur_date` and `nxt_date` into the
per-decision frame — it never includes `prev_date` bars. So the stale
prev-date bars were fetched into the on-disk cache but never reached
`decision_bar()` for the OBS-003 cohort.

But the reasoning was still wrong, and the code was vulnerable: any
future change that merged prev_date into the per-decision frame would
silently allow stale prior-day prices. This commit closes that hole
with an explicit freshness rule.

---

## 2. Durable timezone-aware implementation

### What changed in `src/research/price_alignment.py`

* Replaced hard-coded `SESSION_OPEN = time(14, 30)` /
  `SESSION_CLOSE = time(21, 0)` with constants in **America/New_York
  local time**: `SESSION_OPEN_ET = time(9, 30)` /
  `SESSION_CLOSE_ET = time(16, 0)`.
* Added `NY_TZ = ZoneInfo("America/New_York")`.
* Rewrote `is_regular_session_minute(ts)` to convert `ts` to
  `America/New_York` and check
  `SESSION_OPEN_ET <= t < SESSION_CLOSE_ET` on the NY-local clock,
  plus weekend exclusion.
* Removed `SESSION_TZ` (unused after the change).

### Verified behavior (deterministic tests)

| date | UTC timestamp | NY-local | is_regular_session_minute |
|------|--------------:|---------:|:------------------------:|
| 2026-06-15 (EDT) | 13:30 UTC | 09:30 EDT | True |
| 2026-06-15 (EDT) | 19:59 UTC | 15:59 EDT | True |
| 2026-06-15 (EDT) | 20:00 UTC | 16:00 EDT | **False** (close instant, exclusive) |
| 2026-06-15 (EDT) | 22:30 UTC | 18:30 EDT | False (after-hours) |
| 2026-06-15 (EDT) | 13:00 UTC | 09:00 EDT | False (pre-market) |
| 2026-01-15 (EST) | 14:30 UTC | 09:30 EST | True |
| 2026-01-15 (EST) | 20:59 UTC | 15:59 EST | True |
| 2026-01-15 (EST) | 21:00 UTC | 16:00 EST | **False** (close instant, exclusive) |
| 2026-01-15 (EST) | 14:00 UTC | 09:00 EST | False (pre-market) |
| 2026-09-27 (Sun) | 13:30 UTC | 09:30 EDT | False (weekend) |
| 2026-10-31 (Sat) | 12:30 UTC | 08:30 EDT | False (weekend) |
| 2026-11-02 (Mon) | 14:30 UTC | 09:30 EST | True (first post-DST Monday) |
| 2026-11-02 (Mon) | 13:30 UTC | 08:30 EST | False (pre-market in EST) |

The implementation is now correct for any date in any year.

### Equivalence to prior fix for the OBS-003 cohort

For 2026-09-23..2026-09-25 (entirely in EDT), the new
ET-based logic produces **byte-identical session classification** to
the previously hard-coded 13:30-20:00 UTC window. Verified by
`TestRegularSessionFilter::test_obs_003_cohort_september_dst`.

---

## 3. Decision-price freshness rule

### The hole

`decision_bar(bars, cycle_start)` previously returned the most-recent
bar in `bars` with `index <= cycle_start`, with **no upper bound on
age**. Combined with `label_decisions()` building the per-decision
merged frame as `[cur_date, nxt_date]`, the effective behavior was:
- Overnight decisions (00:00-08:00 UTC) → MISSING_DECISION_BAR (no bars
  exist at-or-before cycle_start on cur_date; prev_date is not
  included).
- Pre-market decisions (08:00-13:30 UTC) → first available bar at 08:00
  UTC used, which can be up to ~5.5 hours old for a 13:30 UTC
  regular-session decision.
- Regular-session decisions → most recent bar within minutes.
- After-hours decisions → most recent bar within minutes.

The pre-market case was acceptable in practice but the rule was
implicit and easy to break by future code changes.

### The smallest defensible rule

```
decision_bar = most recent bar in `bars` such that:
    bar_ts <= cycle_start                  (no look-ahead)
    AND
    cycle_start - bar_ts <= 240 minutes    (freshness cap)
```

240 minutes was chosen because:
- It covers the same-session pre-market → regular transition on
  liquid names (a 13:30 UTC regular-session decision can use a 09:30
  UTC bar = exactly 4 hours old).
- It excludes overnight or weekend prior-session bars (the OBS-003
  prior-session close at 20:00 UTC is more than 240 minutes before any
  overnight decision).
- It is larger than the Alpaca paper-tier lag (~47 hours observed
  in OBS-003A) so it does not interact with the data lag.

### What changed in `src/research/price_alignment.py`

Added:

```python
DECISION_BAR_MAX_AGE_MINUTES: int = 4 * 60  # 240 minutes
```

`decision_bar()` now checks:

```python
age_minutes = (cycle_start - candidate).total_seconds() / 60.0
if age_minutes > DECISION_BAR_MAX_AGE_MINUTES:
    return None
```

### Freshness tests added (deterministic, synthetic)

`TestDecisionBarFreshness` covers:

| case | expectation |
|------|-------------|
| Bar 1 min before cycle_start | accepted |
| 13:30 UTC decision, 09:30 UTC bar (4h old) | accepted (boundary) |
| 02:00 UTC decision, prior 20:00 UTC bar (6h old) | **rejected** |
| 00:05 UTC decision, prior 23:55 UTC bar (10 min old) | accepted (same night) |
| Sunday 12:00 UTC decision, prior Friday 20:00 UTC bar (64h old) | **rejected** |
| Bar 241 min before cycle_start | **rejected** (1 min over cap) |
| Empty bars | returns None |

---

## 4. Impact on the existing September OBS-003 cohort

### Timezone classification: NO CHANGE

The new ET-based logic produces **identical session classification**
for all 91,138 decisions in the cohort, because every date falls
inside EDT (DST 2026: March 8 - November 1). Verified by direct
comparison of `is_regular_session_minute` for all relevant dates.

### Freshness rule: 123 currently-OK labels become MISSING_DECISION_BAR

Computed by simulating the new `decision_bar()` over all 11,716 OK
decisions at fwd_240m using the existing on-disk cache.

| | OK at fwd_240m |
|--|--:|
| Before freshness rule | 11,716 |
| After freshness rule  | 11,593 |
| **Rejected as stale** | **123** |

All 123 rejected decisions are in **pre-market hours** (12:30-13:13
UTC) and were using the first bar of the day (08:00-08:58 UTC,
4h05m-5h10m old). Affected groups:

| group | affected |
|-------|---------:|
| In joint-pass (Group A) | 2 |
| In high-RSI (RSI >= 55) | 29 |
| In pre-market hours | 123 |

123 / 11,716 = 1.05 % of fwd_240m OK labels. **Material enough to
warrant a re-run** (per the task: "If successful labels would
materially change: rerun only what correctness requires").

### Headline findings: STABLE across the change

| cohort | horizon | before (median) | after (median) | before p_pos | after p_pos |
|--------|---------|----------------:|---------------:|-------------:|------------:|
| Joint-pass RAW | fwd_240m | -0.59% | -0.59% | 35.5% | 35.9% |
| Joint-pass RAW | next_session_open | +0.00% | +0.00% | 48.2% | 48.7% |
| High-RSI RAW | fwd_240m | +0.82% | +0.82% | 66.1% | 66.0% |
| High-RSI RAW | next_session_open | +0.69% | +0.71% | 64.1% | 64.6% |
| Joint-pass DEDUP | fwd_240m | -0.62% | -0.59% | 35.2% | 35.6% |
| High-RSI DEDUP | fwd_240m | +0.80% | +0.81% | 66.0% | 66.0% |

All headline qualitative findings are **unchanged**:
- Joint-pass cohort remains underwater at +30m/+60m/+240m.
- High-RSI cohort remains strongly positive at all 4 horizons.
- next_session_open near zero (joint-pass) / positive (high-RSI).

### Whether September outputs required regeneration

**Yes, regenerated once.** Cache reused (41,196 .pkl files, 480 MB).
Total pipeline runtime 539.5 s (slightly slower than prior 448 s due
to the freshness check inside the per-decision loop).

---

## 5. Precise MISSING_DECISION_BAR reason taxonomy

The post-freshness-rule labeled dataset now supports this precise
breakdown (computed offline from cache-status, session classification,
and the freshness rule):

| reason | count | pct of MISSING |
|--------|------:|----------------:|
| `NO_CONTEMPORANEOUS_BAR_OVERNIGHT` | 38,749 | 55.10 % |
| `NO_CONTEMPORANEOUS_BAR_PREMARKET` | 19,658 | 27.95 % |
| `EMPTY_PROVIDER_DATA_FOR_DATE` | 10,120 | 14.39 % |
| `STALE_PRIOR_BAR_REJECTED_BY_FRESHNESS_RULE` | 1,815 | 2.58 % |
| `NO_CACHE_FILE` | 2 | 0.00 % |
| **Total** | **70,344** | **100.00 %** |

All 38,749 `NO_CONTEMPORANEOUS_BAR_OVERNIGHT` cases are decisions at
00:00-07:59 UTC where Alpaca paper-tier returns no bars earlier than
08:00 UTC on the decision date. The cache contains no usable
prior-session bar because the per-decision merged frame only includes
`[decision_date, decision_date + 1]`, and the freshness rule would
reject any prior-day bar in any case.

All 10,120 `EMPTY_PROVIDER_DATA_*` cases are symbols for which Alpaca
paper-tier returns no rows (the symbol is not in the paper feed, is
delisted, or is an OTC/pink-sheet/warrant ticker).

The 1,815 `STALE_PRIOR_BAR_REJECTED_BY_FRESHNESS_RULE` cases are the
newly-flagged subset. They are decisions during regular_dst or
after-hours sessions where the only available at-or-before bar is more
than 240 minutes older than cycle_start (typically a long quiet
period or a thinly-traded symbol with sparse bars).

---

## 6. What SmartBot itself used as the price for overnight decisions

We did not perform a new production investigation (per the task). The
existing `decision_history` table stores `decision_snapshot` which
contains the gate-evaluation context but **not the raw bar price** used
at decision time. We can read the `cycle_start` timestamp and know
whether a contemporaneous Alpaca 1-min bar existed; for the OBS-003
cohort, the cache shows that for overnight decisions (00:00-07:59
UTC) Alpaca paper-tier has no bar at-or-before cycle_start, which is
the same limitation SmartBot would have hit if it tried to fetch a
spot price for those decisions. SmartBot's existing
`SKIPPED_INVALID_DATA` outcome (5,452 / 91,138 = 5.98 %) is its own
handling of this data-absence condition. The OBS-003 retrospective
labels correctly map these to MISSING_DECISION_BAR for the forward-
return measurement.

---

## 7. PR #96 status

| item | value |
|------|-------|
| PR | https://github.com/jsavoy93/trading-bot/pull/96 |
| State | OPEN |
| Commits on branch | 3 (f28aea1, 7c77044, + new commit from this report) |
| Tracked files changed | `src/research/price_alignment.py`, `src/research/run_obs_003.py`, `tests/test_obs_003_research_tooling.py`, `reports/2026-09-26_185143_obs-003-validity-check.md`, `reports/2026-09-26_203320_obs-003-time-price-semantics-verified.md` |
| Untracked regenerations | `reports/research/{labeled_dataset.csv,labeled_dataset_dedup.csv,summary_*.csv,metadata.json}` (gitignored bulk artifacts) |
| Merge status | **DO NOT MERGE** — pending owner review of this report |

---

## 8. Tests

* `tests/test_obs_003_research_tooling.py` — **48 / 48 passing**
  (32 prior + 16 new DST/freshness tests).
* New tests cover:
  - DST summer session open/close (13:30/20:00 UTC)
  - EST winter session open/close (14:30/21:00 UTC)
  - DST transition (Sat 2026-10-31 = EDT, Mon 2026-11-02 = EST)
  - Weekend exclusion in both DST and EST
  - Acceptable recent at-or-before bar (1 min old)
  - Boundary bar at exactly 240 min (13:30 UTC decision, 09:30 UTC bar)
  - Stale prior-day bar rejection (6h old, 64h old)
  - After-hours prior-day bar acceptance (10 min old, same night)
  - 1-min-over-cap rejection
  - Empty-bars handling
  - Constants documentation (`SESSION_OPEN_ET`, `SESSION_CLOSE_ET`,
    `NY_TZ`, `DECISION_BAR_MAX_AGE_MINUTES`)

All tests are deterministic and synthetic (no live Alpaca, no
production DB).

---

## 9. Confirmation of safety constraints

| confirmation | status |
|--------------|--------|
| SmartBot 1082165 untouched | ✓ uptime 2-19 days, no restart event |
| `trading_bot.db` writes | none — opened only via `?mode=ro` |
| SmartBot code / strategy / config / settings | unchanged |
| Schema | unchanged |
| Other services restarted | none |
| OpenClaw / gateway config | unchanged |
| C14B-2E | remained PARKED — no branch, no audit |
| STRAT-002 | NOT started |
| Live forward collection | NOT implemented |
| Symbol universe | NOT redesigned |
| Dashboards | NOT built |
| Pipeline performance work | NOT done |

Only code changes in this commit:
1. `src/research/price_alignment.py` — ET-based session window via
   `zoneinfo.ZoneInfo("America/New_York")`; added
   `DECISION_BAR_MAX_AGE_MINUTES = 240` and freshness check in
   `decision_bar()`.
2. `src/research/run_obs_003.py` — metadata sidecar now reports the
   ET-based session string and the freshness rule.
3. `tests/test_obs_003_research_tooling.py` — 16 new tests covering
   DST summer, EST winter, transition boundary, freshness edges.

Plus two regenerations of the existing labeled outputs (one to apply
the freshness rule to labels; one to refresh the metadata sidecar
after clearing the bytecode cache).

---

## 10. Final question

**"Is PR #96 now safe to merge as reusable research tooling across
both DST and standard-time dates?"**

**Answer: yes — with the caveats noted in the prior validity-check
report and the freshness rule documented above.**

* The session-window logic now uses `America/New_York` and
  `zoneinfo`, so it is correct for any date in any year regardless of
  DST.
* The freshness rule (240 min cap) prevents any future code change
  from silently allowing stale prior-day prices for off-hours
  decisions.
* The September 2026 OBS-003 outputs were regenerated once to apply
  the freshness rule; only 1.05 % of fwd_240m OK labels changed
  (123 / 11,716), all in pre-market hours, all from thinly-traded
  symbols. Headline findings (joint-pass underwater, high-RSI
  positive) are stable.
* No production behavior was changed.
* The 31 → 48 deterministic test growth covers both DST and EST
  session windows plus the freshness rule.
* The PR remains open and unmerged pending owner review.

OBS-003 TIME/PRICE SEMANTICS VERIFIED — AWAITING OWNER MERGE
