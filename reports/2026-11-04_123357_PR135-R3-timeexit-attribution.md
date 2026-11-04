# PR #135 round 3 (follow-up) — the 15-day time-exit sell must be attributable

- **Task**: follow-up to PR #135 round 3 — fix the P&L attribution of the
  15-day time-exit market sell (found while reviewing the round-3 work).
- **Branch**: `agent/swing-paper-order-tagging-20261103` (PR #135) — pushed, NOT merged
- **Commit**: `e21d35d` — "STRAT-SWING-PAPER: make the 15-day time-exit sell label attributable"
- **Model that completed the work**: minimax-portal/MiniMax-M3 (this session; no fallback used, no sub-agent spawned)
- **Started / ended**: 2026-11-04 12:35–2026-11-04_123357 UTC
- **Status**: DONE (implementation) — Josh review/merge approval required.

## 1. Executive summary

The time-exit market sell used the client_order_id
`rsi2-<SYM>-<YYYYMMDD>-<n>-timeexit`. `parse_label()` splits on `-` and
rejects it ("malformed label"), so `report_pnl.attribute()` returned
`None` and **every time-exit fill was dropped from the per-strategy P&L
report**. The runner was correct; the report silently under-counted
realized P&L.

## 2. Acceptance criteria and evidence

| # | Criterion | Proof method | Exact result | Status |
|---|---|---|---|---|
| F.1 | The time-exit label is parseable and attributed to rsi2 | unit test | `test_time_exit_label` PASS (`make_time_exit_client_order_id("rsi2","AMD","20261103",2) == "rsi2-AMD-20261103-2-timeexit"`, length ≤ 48); `test_parse_roundtrip_entry_stop_partial` PASS (`parse_label("rsi2-NVDA-20261103-7-timeexit") == {..., "kind": "timeexit"}`) | PASS |
| F.2 | The runner emits exactly that label | fake-broker test | `test_time_exit_sell_uses_a_parseable_label` PASS (the submitted sell carries `rsi2-AMD-20261103-1-timeexit`; `parse_label(coid)["strategy"] == "rsi2"`) | PASS |
| F.3 | report_pnl attributes the time-exit fill | report unit test | `test_time_exit_sell_is_attributed_to_the_strategy` PASS (buy 100 @50, time-exit sell 100 @60 → `realized["rsi2"] == 1000.0`, one trade, `sell_id` ends with `-timeexit`) | PASS |
| F.4 | No regression | pytest | Swing suites **102 passed, 0 failed**; full suite **1 failed, 2239 passed, 2 skipped, 13 deselected** — the single failure is the pre-existing env-dependent `test_strat026_shadow.py::TestSystemdUnits::test_units_not_installed` (verified identical on `origin/main`). Zero new failures. | PASS |

## 3. Files changed

- `experiments/swing_paper/rules.py`: new `TIMEEXIT_SUFFIX` +
  `make_time_exit_client_order_id()`; `parse_label()` strips the suffix
  and reports kind `timeexit`.
- `experiments/swing_paper/runner.py`: the time exit uses the helper.
- `tests/test_swing_paper_rules.py`, `tests/test_swing_paper_report.py`,
  `tests/test_swing_paper_runner.py`: the three tests above.

## 4. Known risks / uncertainties

- **Legacy labels are unaffected.** The `-stop-p` / `-stop-oco` legacy
  kinds still parse exactly as before; `-timeexit` is a new kind and
  cannot collide with an existing one (no other label ends in `timeexit`).
- `next_sequence()` still ignores the `-timeexit` suffix, so the
  per-symbol-per-day sequence is not disturbed by a time exit.
- The fix touches attribution only; no order is sent differently
  (same label string as before).

## 5. Manager decision

Ready for the independent reviewer (ENG-07) and Josh's merge decision on
PR #135. Nothing merged, nothing deployed, no timer enabled, no order placed.
