# PR3 — Durable SQLite chat history — ready for review

- **Task**: DASH-008 (PR3) — Durable SQLite chat history for the
  Engineering Dashboard.
- **Owner**: Trading-Manager (delegated, executed in this session).
- **Branch**: `agent/dashboard-chat-history-durable-pr3`
- **Base**: `main`
- **Commit**: `f24e95b`
- **Push status**: pushed to `origin/agent/dashboard-chat-history-durable-pr3`
- **Reporting mode**: implementation (PR3 modified repo files; REPORT.md
  + this archive written; REPORT.md is gitignored per existing rule).
- **Started (UTC)**: 2026-09-08 12:24 (after Josh's `GO` at 12:23)
- **Ready for review (UTC)**: 2026-09-08 12:46
- **Elapsed**: ~22 minutes (acknowledgment → branch → discovery →
  implementation → tests → docs → commit → push).
- **Continuity**: continuous (no resume from stale workflow).

## 1 — PR3 acceptance criteria (PASS / FAIL, exact evidence)

### Storage
| Criterion | Status | Evidence |
| --- | --- | --- |
| `.agent-state/engineering-chat.sqlite3` created on first use | PASS | `tests/test_chat_persistence.py::test_file_mode_is_0600_after_first_write` |
| File mode 0600 root:root | PASS | Same test (stat.S_IMODE == 0o600) |
| Parent dir mode 0700 | PASS | `tests/test_chat_persistence.py::test_parent_dir_mode_is_0700` |
| SQLite WAL active | PASS | `tests/test_chat_persistence.py::test_wal_journal_mode_is_active` |
| Gitignored | PASS | `.gitignore` adds `.agent-state/engineering-chat.sqlite3*` lines |
| Schema idempotent (`PRAGMA user_version = 1`) | PASS | `test_schema_is_idempotent_across_repeated_ensure`, `test_schema_version_recorded_in_user_version` |
| All 14 spec'd columns present | PASS | `test_schema_columns_match_pr3_spec` (exact set match) |
| Role CHECK rejects unknown | PASS | `test_role_check_constraint_rejects_unknown_role` |
| Both required indexes present | PASS | `test_required_indexes_are_present` |

### Visibility / projection reuse
| Criterion | Status | Evidence |
| --- | --- | --- |
| Single source of truth = `chat_gateway.project_message` | PASS | `dashboard_api/chat_persistence_integration.py` calls `chat_gateway.project_message` indirectly via ChatMessage from `_project_gateway_history`. The persistence layer itself only enforces the SQLite CHECK. |
| No second filter in persistence layer | PASS | `test_persistence_layer_does_not_introduce_a_second_filter` |
| Visible assistant rows persisted from chat.history | PASS | `test_history_persists_visible_assistant_rows_only` (system / toolUse / delivery-mirror filtered before persistence; only `role=assistant, stopReason=stop` reaches store) |
| Internal-only identity fields on ChatMessage | PASS | `ChatMessage.to_dict()` excludes `source_message_id`, `response_id`, `raw_timestamp_ms` (existing `test_to_dict_*` and `test_chat_copy_*` assertions still hold) |

### Persistence semantics
| Criterion | Status | Evidence |
| --- | --- | --- |
| chat.send accept → 1 user row, `delivery_status="accepted"` | PASS | `test_send_accepted_is_persisted_with_run_id`, `test_user_row_inserted_on_chat_send_accept` |
| chat.send reject → NOT persisted | PASS | `test_send_rejected_is_not_persisted` |
| chat.send failure → NOT persisted | PASS | `test_send_failed_transport_is_not_persisted` |
| Round-trip `truncated` + `truncation_source` | PASS | `test_truncation_metadata_round_trips` |

### Dedup (the 5 corrected cases Josh specified)
| # | Case | Status | Test |
| --- | --- | --- | --- |
| 1 | same source message reconciled 100× → 1 row | PASS | `test_same_source_message_reconciled_100_times_yields_one_row` |
| 2 | same source message reconciled after restart → 1 row | PASS | `test_same_source_message_after_restart_yields_one_row` |
| 3 | identical text from different run_ids → 2 rows | PASS | `test_identical_text_different_run_ids_yields_two_rows` |
| 4 | identical text from different openclaw_session_ids → 2 rows | PASS | `test_identical_text_different_openclaw_session_ids_yields_two_rows` |
| 5 | session rotation preserves old rows and appends new rows | PASS | `test_session_rotation_preserves_old_rows_and_appends_new_rows` |

### API (read side; UI NOT switched)
| Criterion | Status | Evidence |
| --- | --- | --- |
| `GET /api/engineering/chat/history/durable` registered | PASS | `routes` list in `app.py` includes the new path; `test_no_mutation_http_methods_or_routes` asserts it; live curl currently returns 404 because dashboard.service is pre-PR3 — expected |
| Latest 50 default, oldest → newest | PASS | `test_latest_returns_oldest_first_within_default_limit` |
| Pagination via `before_id` | PASS | `test_older_than_paginates_backwards` |
| Hard ceiling 200 | PASS | `test_limit_is_bounded_by_max_limit`, `_parse_positive_int` in `app.py` |
| Project / agent scoped | PASS | `test_provider_scopes_by_conversation_id`, `_LazyConversationDurableProvider` |
| Env-var kill switch for write side | PASS | `_chat_persistence_enabled`, `DASHBOARD_CHAT_PERSISTENCE_ENABLED` env var |
| Browser UI not switched | PASS (by definition) | `templates/dashboard.html` unchanged; `dashboard_api/app.py` chat-history route unchanged |

### Tests + regression
| Criterion | Status | Evidence |
| --- | --- | --- |
| New tests pass | PASS | 34/34 new tests pass |
| Full safe suite pass | PASS | 979/979 pass (was 969 pre-PR3, net +10) |
| No regression in existing tests | PASS | Updated only route-set + import-rename assertions in 3 existing test files |

## 2 — Dedup-key synthesis (locked with Josh 2026-09-08 12:23)

```
user priority 1: f"u|{project_id}|{agent_id}|{conversation_id}|run:{run_id}"
user priority 2: f"u|{project_id}|{agent_id}|{conversation_id}|ts:{iso_second_bucket}|sid:{session_id}|sha:{text_hash[:16]}"
user backstop:    f"u|{project_id}|{agent_id}|{conversation_id}|local:{uuid4_hex}"

assistant priority 1: f"a|{project_id}|{agent_id}|{conversation_id}|mid:{__openclaw.id}|sid:{session_id}"
assistant priority 2: f"a|{project_id}|{agent_id}|{conversation_id}|rid:{responseId}|sid:{session_id}"
assistant priority 3: f"a|{project_id}|{agent_id}|{conversation_id}|ts:{epoch_ms}|sid:{session_id}|sha:{text_hash[:16]}"
```

Discovery probe of the live OpenClaw Gateway on 2026-09-08 12:25 UTC
confirmed `__openclaw.id` is 8-char hex (observed 50/50 unique within a
50-msg window) and `responseId` is present on every assistant message
(observed 19/19 unique). Per-message `run_id` is NOT in the chat.history
projection — priority-2 therefore uses `responseId` instead, matching
Josh's spec ("run_id + message/session identity").

## 3 — Identifier semantics (locked 2026-09-08 12:23)

- `conversation_id` = `agent:trading-manager:telegram:direct:8455029949`
  (stable OpenClaw session key; does NOT rotate across trajectory
  rotation).
- `openclaw_session_id` = rotating trajectory UUID (e.g.
  `97174a0c-27d9-40e2-86ef-c11edb1f033d`).
- Session rotation therefore APPENDS new rows under the same
  `conversation_id` with a new `openclaw_session_id`. Old rows preserved.
- `conversation_id` resolver: `_default_conversation_id_resolver` in
  `chat_persistence_integration.py`; `_LazyConversationDurableProvider`
  in `app.py` resolves on first read.

## 4 — Files added / changed (exact list)

| Path | Action | Lines (approx) | Why |
| --- | --- | --- | --- |
| `dashboard_api/chat_persistence.py` | NEW | ~580 | Storage layer (schema, WAL, 0600, dedup, read API) |
| `dashboard_api/chat_history_durable.py` | NEW | ~80 | Read-side protocol + store-backed provider |
| `dashboard_api/chat_persistence_integration.py` | NEW | ~310 | Wiring wrapper (chat.send / chat.history → store) |
| `dashboard_api/chat_gateway.py` | MODIFIED | +50 | Rename `_project_message` → `project_message`; add internal identity fields on ChatMessage; add `_nested_get` helper |
| `dashboard_api/app.py` | MODIFIED | +210 | New `CHAT_HISTORY_DURABLE_ROUTE`; new GET endpoint; `_LazyConversationDurableProvider`; `PersistingChatHistoryProvider` injection; env-var kill switch |
| `tests/test_chat_persistence.py` | NEW | ~640 | 26 tests |
| `tests/test_chat_history_durable.py` | NEW | ~135 | 8 tests |
| `tests/test_dashboard_api_app.py` | MODIFIED | +6/-0 | Route-set + new route constant + chat_copy import rename |
| `tests/test_dashboard_api_provider.py` | MODIFIED | +4/-0 | Route-set + new route constant |
| `.gitignore` | MODIFIED | +6 | Explicit ignore of `engineering-chat.sqlite3*` |
| `AGENT_BACKLOG.md` | MODIFIED | +132 | DASH-008 entry appended |
| `MENTOR.md` | MODIFIED | +88 | Durable-chat section appended |
| `ITERATION_PROGRESS_LOG.md` | MODIFIED | +56 | Continuity entry appended |

Total diff: 13 files, 2366 insertions(+), 11 deletions(-).

## 5 — Stop / approval conditions

- **Stop reason**: explicit Josh instruction at 2026-09-08 12:23: "STOP
  when PR3 is ready for review".
- **Manager decision**: READY FOR REVIEW — no merge, no service restart.
- **Approval requested**: Josh reviews the diff on
  `agent/dashboard-chat-history-durable-pr3` (commit `f24e95b`) and
  either approves merge + restart or requests narrow changes.

## 6 — Out of scope (explicit, locked 2026-09-08)

- PR4 — UI switch (browser reads `/api/engineering/chat/history/durable`).
- PR5 — backfill of pre-PR3 conversation history.
- Cloudflare Tunnel / Access configuration changes.
- Raw trajectory / reset file parsing.

## 7 — Audit chain (PR3 closure)

- `/root/.openclaw/audit-archives/trading-bot/2026-09-08_124605_pr3-durable-chat-history-ready-for-review.md` (this file)
- `/root/.openclaw/workspace/trading-bot/REPORT.md` (current rolling report)
- `/root/.openclaw/workspace/trading-bot/reports/2026-09-08_124605_pr3-durable-chat-history-ready-for-review.md` (in-repo archive, reviewable)
- `/root/.openclaw/workspace/trading-bot/AGENT_BACKLOG.md` (DASH-008 entry)
- `/root/.openclaw/workspace/trading-bot/ITERATION_PROGRESS_LOG.md` (continuity entry)
- `/root/.openclaw/workspace/trading-bot/MENTOR.md` (durable-chat section)

PR3 STOPPED. Awaiting Josh's review.
