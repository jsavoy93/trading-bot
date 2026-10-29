# EXEC — Two-sleeve portfolio design document (PAPER ONLY, docs only)

## Executive summary
Design document only (no production code) for the two-sleeve portfolio:
Sleeve L (frozen STRAT-017 gross profitability, annual June formation,
equal-weight Q5) and Sleeve S (redesigned SmartBot, strategy unchanged).
Adds capital split, sleeve tagging schema, shared portfolio risk governor,
per-sleeve reporting, the Priority 4 historical evaluation plan, and 9 open
decisions for Josh. Committed to a docs branch; PR opened; NOT merged.

## Task or purpose
Owner directive 2026-10-29 00:23 UTC — Priorities 1 and 3: PR #111 status
(already MERGED 2026-10-22; nothing to merge) + two-sleeve design doc.

## Branch
docs/two-sleeve-architecture (off origin/main f8e2144, fresh worktree)

## Commit
f8e2144

## Files changed
- docs/TWO_SLEEVE_PORTFOLIO_DESIGN.md (NEW)
- reports/2026-10-29_002701_two-sleeve-design-doc.md (NEW, this archive)

## Tests run
None applicable — documentation-only change; no code paths touched.

## Exact test summary
N/A (no runtime change; full suite unaffected by a new docs file).

## Overall acceptance result
PASS (design doc covers all 8 requested areas + evaluation plan + open
decisions; search-first confirmed no prior two-sleeve design exists).

## Known risks
- STRAT-017 survivorship caveat (owner 2026-10-12: selection bias NOT ruled
  out) carried into the design as a standing qualification.
- Sleeve S historical evaluation is limited by the hourly-data gap (D8).
- All defaults in the doc are proposals awaiting Josh's decisions (§12).

## Manager decision
Awaiting Josh: PR review + the 9 open decisions (D1–D9).

## Next recommended action
Josh answers D1–D9; then phase P1 (sleeve schema migration) can be scoped
as a normal backlog item with tests — only after explicit approval.
