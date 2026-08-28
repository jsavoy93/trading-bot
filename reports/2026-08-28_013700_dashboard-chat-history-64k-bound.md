# Dashboard chat.history 64K Bound + Upstream Truncation Marker Detection
**Date**: 2026-08-28 01:37 UTC
**Branch**: `feat/dashboard-chat-history-64k-bound`
**Base commit**: `335ee41` (PR #67 merge)
**Author**: trading-manager agent (implementation)
**Status**: DONE — pending Josh PR review

## Objective

Implement Phase 1 of the read-only root-cause trace from
`/root/.openclaw/audit-archives/trading-bot/2026-08-27_124200_read-only-chat-history-truncation-trace.md`.

Josh's correction: do NOT use the previously proposed 500K + 16K combination.
Instead, raise both bounds to **64,000 chars** and detect the canonical
OpenClaw Gateway chat.history truncation marker
(`\n...(truncated)...`) so the projection never silently truncates.

## Files changed

- `dashboard_api/chat_gateway.py` — added `CHAT_HISTORY_MAX_CHARS`,
  `OPENCLAW_GATEWAY_TRUNCATION_MARKER`, `_split_off_openclaw_gateway_marker()`;
  raised `CHAT_MESSAGE_MAX_CHARS` from 16_000 to 64_000; added
  `ChatMessage.truncation_source`; updated `_project_message()` to detect
  upstream Gateway truncation FIRST; passed `maxChars: CHAT_HISTORY_MAX_CHARS`
  in the chat.history RPC payload.
- `tests/test_dashboard_chat_gateway.py` — added 8 new regression tests,
  renamed 3 `_16k_` boundary tests to `_64k_`, updated 3 existing tests to
  assert the new `truncation_source` field and the new bound.
- `MENTOR.md` — updated chat-section inbound-bound paragraph; documented
  the `truncation_source` contract; documented the `chat.message.get`
  fallback for >64K responses.
- `ITERATION_PROGRESS_LOG.md` — appended implementation entry.
- `REPORT.md` — current rolling executive summary.
- `reports/2026-08-28_013700_dashboard-chat-history-64k-bound.md` (this file)
  — authoritative audit record.

## Acceptance Criteria + Evidence

### AC1: chat.history RPC passes `maxChars=64_000` to the Gateway

**Proof**: `test_chat_history_rpc_passes_max_chars_to_gateway`
**Result**: PASS — `f"const MAX_CHARS = {CHAT_HISTORY_MAX_CHARS}"` present in
the Node script; `maxChars: MAX_CHARS` present in `client.loadHistory()`
call.

### AC2: `CHAT_MESSAGE_MAX_CHARS = 64_000`

**Proof**: `test_chat_bounds_constants_remain_unchanged` (updated to pin 64K).
**Result**: PASS — assertion `CHAT_MESSAGE_MAX_CHARS == 64_000` passes.

### AC3: `CHAT_HISTORY_MAX_CHARS == CHAT_MESSAGE_MAX_CHARS`

**Proof**: `test_chat_bounds_constants_remain_unchanged` (asserts equality).
**Result**: PASS — neither layer cuts earlier than the other.

### AC4: 18,354-char known audit response passes through verbatim

**Proof**: `test_known_18354_char_audit_response_passes_through_unchanged`.
**Result**: PASS — `msgs[0]["truncated"] is False`,
`msgs[0]["truncation_source"] is None`, `len(msgs[0]["text"]) == 18354`,
`msgs[0]["text"].endswith(natural_ending)` where `natural_ending` is the
exact final sentence from the 2026-08-27 audit.

### AC5: 63,999 chars passes through complete

**Proof**: `test_64k_hard_bound_63999_chars_complete_with_no_truncation`.
**Result**: PASS — `truncated=False, truncation_source=None,
len(text)==63999`.

### AC6: 64,000 chars (exactly at the bound) passes through complete

**Proof**: `test_64k_hard_bound_exactly_64000_chars_complete_with_no_truncation`.
**Result**: PASS — `truncated=False, truncation_source=None,
len(text)==64000, "[Response truncated]" not in text`.

### AC7: 64,001 chars (just over the bound) explicitly truncated with marker

**Proof**: `test_64k_hard_bound_64001_chars_truncated_with_explicit_marker`.
**Result**: PASS — `truncated=True, truncation_source="dashboard",
text ends with " [Response truncated]"`.

### AC8: Upstream Gateway truncation marker detected by suffix

**Proof**: `test_gateway_truncation_marker_surfaces_as_truncation_source_gateway`.
**Result**: PASS — text ending with `\n...(truncated)...` produces
`truncated=True, truncation_source="gateway"`, marker is stripped from
projected text, dashboard's own `[Response truncated]` is NOT appended
(no double-marker).

### AC9: Marker substring NOT at suffix is NOT misclassified

**Proof**: `test_gateway_marker_substring_not_at_suffix_is_not_misclassified`,
`test_gateway_marker_with_trailing_content_is_not_misclassified`.
**Result**: PASS — quoting a previous truncated message (marker mid-body)
is preserved verbatim with `truncated=False, truncation_source=None`.

### AC10: PRs #63–#67 contracts all preserved

**Proof**: existing tests in `test_dashboard_chat_gateway.py`.
**Result**: PASS — all 32 tests from PRs #63–#67 pass unchanged. Specifically:
  - `test_history_projection_keeps_only_assistant_messages_with_stop_reason_stop`
  - `test_history_projection_drops_openclaw_delivery_mirror_assistant_messages`
  - `test_history_projection_drops_delivery_mirror_even_when_not_adjacent_to_real_reply`
  - `test_chat_copy_*` (PRs #64/#65 copy controls)
  - `test_send_adapter_payload_omits_timeoutms_field_entirely` (PR #67 chat.send fix)
  - `test_send_compose_returns_promptly_after_acceptance_regardless_of_run_length`
  - `test_legacy_180s_send_alias_constant_is_removed_from_module`

### AC11: Full safe test suite passes with no regression

**Proof**: `.venv/bin/python -m pytest -q` (whole repo, no `live` markers).
**Result**: `870 passed, 84 warnings in 65.98s`. Previous baseline: 862.
Net: +8 new tests, 0 regressions.

### AC12: `git diff --check` clean

**Proof**: `git diff --check`.
**Result**: PASS.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| 64K × 50 messages = ~3.2M chars worst-case history payload | Low | 25 MiB WebSocket cap is the ceiling; 3.2M chars (~3.2 MiB UTF-8) is well under it. |
| Gateway marker string changes in future OpenClaw release | Low | The 64K dashboard bound is independently applied; even if the marker is removed entirely, the dashboard still truncates at 64K. |
| User content containing `...(truncated)...` substring elsewhere | Low | Detection is by **suffix position only**; covered by `test_gateway_marker_substring_not_at_suffix_is_not_misclassified` and `test_gateway_marker_with_trailing_content_is_not_misclassified`. |
| Future response genuinely > 64K | Low | Documented `chat.message.get` fallback in MENTOR.md. Not implemented in this PR. |

## Behavior changes (visible to Josh)

| Aspect | Before | After |
|---|---|---|
| Inbound bound (dashboard projection) | 16,000 chars | **64,000 chars** |
| chat.history RPC per-msg cap (Gateway) | 8,000 (Gateway default) | **64,000 (explicit)** |
| 18,354-char audit response | Silently cut at 8,018 chars with `truncated=False` | **Complete, `truncated=False, truncation_source=null`** |
| `truncated` field on JSON | bool (sometimes misleading) | bool + **`truncation_source: null|gateway|dashboard`** |
| Pathological >64K response | Bound at 16K silently | **Bound at 64K with explicit dashboard marker** |
| Cut at Gateway layer (rare but possible) | Reported as `truncated=False` (silent) | **Surfaced as `truncated=True, truncation_source="gateway"`** |

## Next action

1. Commit (Phase 1 only, no other changes).
2. Push branch `feat/dashboard-chat-history-64k-bound`.
3. Open PR.
4. STOP for Josh PR review.

## Out of scope (deferred to follow-up PRs)

- Per-message full-text retrieval via OpenClaw Gateway `chat.message.get`
  for responses that genuinely exceed 64K. RPC is supported and
  live-verified; documented in MENTOR.md.
- Browser-side UI rendering of the `truncation_source` field (showing
  "OpenClaw cut this upstream" vs "Dashboard cut this" indicators).
  Current PR ships the field; UI follow-up PR can consume it.
