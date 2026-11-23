# BLEND-001 — Preregistration erratum (written 2026-11-23, BEFORE any DEV result)

State at the time this erratum was written: **NO DEV blend result has been
computed or looked at.** No C4 monthly series, no blend monthly series, no bar
values, no random-draw percentiles exist. The only run performed so far is the
script's self-test/smoke path (and a 5-month smoke of the aggregation), none of
which was examined for performance. Everything below is an implementation
decision taken in ignorance of DEV outcomes.

The frozen file `PREREGISTRATION.md` is at its original freeze commit
(`a104853`) content and is NOT edited by this erratum. This erratum records the
one deliberate deviation.

## E1. C4 monthly aggregation method (replaces §5's "Non-overlapping implementation" formula)

§5 froze: `s_t = sum_i n_i * r_i,net / mean_over_month_days(max(deployed_capital, 1 unit))`
— i.e. a per-month rescaling by that month's mean daily deployed notional.

The implementation uses instead a **single fixed capital denominator**:

- The C4 sleeve is modelled as deploying a fixed amount of capital, **one unit
  per open position**, and holding cash (0% return) otherwise.
- `D` = the **95th percentile of the sleeve's daily open-position count**, used as
  ONE fixed number for the whole DEV window: `D = max(p95(open count on days with
  at least one open position), 1)`.
- Each session's sleeve return = `(sum over open trades of that session's
  close-to-close return) / D`. Entry fees are charged on the entry session and
  exit fees on the exit session, each divided by `D`. A session with nothing open
  earns 0 (cash), never renormalised.
- Trades are assigned per session by the session's month; a trade whose exit date
  is not a MOM-grid session is booked on the last grid session on or before its
  exit date.

### No look-ahead (why the denominator is trailing, not whole-window)

A p95 computed over the whole DEV window uses future sessions to scale past
months — that is look-ahead, and is rejected. The denominator is therefore
**trailing only**: on session `i` the value in force is
`D_i = max(p95 of the open-position count over sessions [start, i], 1)`, a
cumulative-expanding window. `cap_divisor()` (whole-window p95) remains in the
code purely as a reference/monitor number, reported but never used to scale the
series. `test_cap_divisor_is_trailing_only` fails if the whole-window p95 ever
leaks into `s_t`.

**Decision (recorded here):** the documented, code-checked method is the
**fixed-denominator, 1-unit-per-position, cash-elsewhere** method with a
**trailing expanding-window p95 denominator `D_i`** (not a whole-window p95, and
not the §5 per-month mean-deployed-notional rescaling). The prereg §5 formula is
superseded for `s_t`; the weights (§5 B1/B2), month grid (§4), bars (§6) and
random benchmark (§7) are unchanged.

`test_blend_units.py::test_monthly_series_matches_documented_aggregation` pins
the code to this exact definition (fixed amount per open position, trailing
denominator, cash elsewhere). If code and this erratum diverge, that test fails.

## E2. Pass bar (e) percentile constant

No change to the constant: (e) stays `percentile >= 98.75`, exactly as frozen in
`PREREGISTRATION.md` §6(e) at commit `a104853`, and the code evaluates 98.75.
An explanatory parenthetical about where 98.75 comes from was inserted after the
freeze in the working copy; it is removed (the frozen file is restored verbatim).
This erratum is the sole record.

## E3. Scope

Only `research/` paths are touched by this PR. The `research/blend-001/results.*`
local outputs are already covered by an ignore rule if one is needed; the root
`.gitignore` change is reverted so the PR stays inside `research/`.
