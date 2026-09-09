# PR4 — Durable + Live Chat UI for the Engineering Dashboard
# In-repo reviewable archive

**Task:** DASH-009
**Branch:** `agent/dashboard-chat-durable-ui-pr4`
**Commit:** `c698cd2e7d813ae45ff01a58a3194c8a52260c41`
**PR:** #74 — https://github.com/jsavoy93/trading-bot/pull/74
**Timestamp:** 2026-09-09T02:45:10Z
**mergeStateStatus:** CLEAN
**mergeable:** MERGEABLE

## Executive summary

PR4 is ready for Josh's review. The Chat tab now loads durable SQLite
history (latest 50, oldest → newest) first, then reconciles with live
Gateway polling. The merge is deterministic: durable rows are
authoritative for historical ordering; live rows may appear immediately
with `data-source="live-only"` and collapse into durable rows on
catch-up. Optimistic send UX is preserved. "Load older" paginates with
viewport preservation. Auto-scroll is iPhone-Safari safe (stick only
when user was near bottom BEFORE the render). Per-message Copy and
"Copy since my last message" still work on the merged view.

A PR4 correction (Josh 2026-09-09 02:25 UTC) added stale
terminal-state recovery: `projectAgentStatus()` now transitions the
pill from Failed back to Idle on ANY healthy non-terminal poll
(session available, no active run, run_status not in
{failed, killed, timeout}). The pre-PR4 rule required a future
has_active_run=true event, which left backgrounded/throttled tabs
stuck on `Trading manager · Failed` indefinitely. Genuine terminal
failures still surface as Failed. The pill recovery is pill-only:
visible durable chat history rows are never cleared or reordered by
the status transition.

## Task

Update the Chat tab so durable SQLite history is the primary visible
source while live Gateway polling continues to provide current run
state and new message reconciliation.

## Branch

`agent/dashboard-chat-durable-ui-pr4`

## Commit

`c698cd2e7d813ae45ff01a58a3194c8a52260c41`

## Files changed

- `dashboard_api/app.py` (chat JS embedded template rewritten; ~250 lines added)
- `dashboard_api/chat_gateway.py` (ChatMessage.to_dict identity fields)
- `dashboard_api/chat_history_durable.py` (_to_chat_message populates identity)
- `tests/test_dashboard_api_app.py` (1 pre-PR4 test mock updated)
- `tests/test_pr4_durable_chat_ui.py` (NEW, 22 tests, ~1284 lines)
- `AGENT_BACKLOG.md` (DASH-009 status + correction appended)
- `MENTOR.md` (PR4 section appended)
- `ITERATION_PROGRESS_LOG.md` (continuity entry appended)

## Tests run

`TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest`

## Test summary

- **1001/1001 PASSED** (979 pre-PR4 + 22 PR4).
- Wall time: 95.96s.
- 85 warnings (all pre-existing; no new warnings introduced by PR4).
- 0 live brokerage calls (TESTING=1 enforced).
- `git diff --check` exit=0.

## Overall acceptance result

PASS for all 18 acceptance criteria in the original PR4 spec, plus
the 4 PR4 correction criteria (status recovery):

| Criterion | Result |
|-----------|--------|
| Durable-first latest 50 on Chat tab open | PASS |
| Live polling continues on existing cadence | PASS |
| Session rotation preserves durable rows | PASS |
| Live + durable deterministic merge (no text-only dedup) | PASS |
| Live-only → durable collapse on catch-up | PASS |
| Optimistic send + collapse on durable catch-up | PASS |
| 4000-char bound + non-text rejection preserved | PASS |
| Send failure preserves draft + history + banner | PASS |
| Load older prepends with viewport preservation | PASS |
| Load older hides when no older rows remain | PASS |
| Auto-scroll: near bottom → follow; scrolled up → stay put | PASS |
| Per-message Copy on durable rows | PASS |
| Copy since my last message on merged view | PASS |
| Hidden/tool/system rows never exposed | PASS |
| Active run → Failed terminal state surfaces as Failed | PASS |
| Later healthy available poll → Idle (clears stale Failed) | PASS |
| Background/throttled polling recovery | PASS |
| Durable history remains visible throughout status recovery | PASS |

## Known risks

1. **Cloudflare Access 2s polling throttle**: backgrounded tabs may
   not receive polls for >2 minutes. The new status recovery logic
   handles this — any healthy poll clears stale Failed. The 2026-09-08
   17:59 UTC issue was specifically caused by the pre-PR4 rule that
   required a future has_active_run=true event to clear stale Failed.
2. **Manager tool-loop fix is out of scope**. PR4 is UI-side only.
   The 2026-09-08 13:04 UTC manager introspection loop was not
   addressed.
3. **PR5 backfill is out of scope**. Historic pre-PR3 chat history
   is not retroactively persisted.
4. **Mergeable but no CI checks reported**. PR #74 reports
   mergeable=true, mergeStateStatus=CLEAN, but no GitHub Actions
   checks. Full safe suite passed locally.

## Manager decision

STOP. Awaiting Josh's explicit merge approval. Do not auto-merge.
Do not begin PR5.

## Next recommended action

Josh reviews PR #74 and either:
- Approves merge (then deploy via `sudo systemctl --user restart
  dashboard.service` after merge), OR
- Requests changes (then agent iterates).

## Audit archive

`/root/.openclaw/audit-archives/trading-bot/2026-09-09_024355_pr4-ready-for-review.md`
(16320 bytes, authoritative audit record).

## PR body excerpt

> Updates the Chat tab so durable SQLite history is the primary visible
> source while live Gateway polling continues to provide current run
> state (Working / Idle / Failed, has_active_run, run_status) and new
> message reconciliation.
> ...
> Tests: tests/test_pr4_durable_chat_ui.py adds 22 tests. Full safe
> suite 1001/1001 (979 pre-PR4 + 22 PR4). git diff --check clean.
> ...
> Awaiting Josh's explicit merge approval.
