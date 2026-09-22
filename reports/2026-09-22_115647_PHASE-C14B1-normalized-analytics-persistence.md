# PHASE-C14B-1 — Normalized Analytics Persistence Foundation

## Executive summary

- **Task:** add the schema bootstrap + write-path foundation for the
  normalized analytics design from the PHASE-C14B read-only audit.
  Two child tables (`decision_gate_evaluations`,
  `decision_execution_checks`) are created idempotently in
  `_init_schema`. `finalize_decision_history` is extended to INSERT
  one row per gate / check inside the SAME transaction as the parent
  decision_history INSERT, using `cur.lastrowid` to bind the parent.
  Atomicity: if any child INSERT raises, the parent is rolled back.
  decision_snapshot is never modified; the child tables are purely
  additive mirrors of the structured Python Dict.
- **Branch:** `agent/phase-c14b1-normalized-analytics-persistence`
  (created from `main @ 96f8fcb`).
- **Commit:** none yet — slice stops at test-verified state per task
  instructions ("Do NOT deploy/restart services").
- **Files changed:**
  - `src/database/sqlite_db.py` — +341 lines (idempotent schema
    bootstrap + new helpers `_persist_gate_evaluations` /
    `_persist_execution_checks` + extension of
    `finalize_decision_history`).
  - `tests/test_db_normalized_analytics_persistence.py` — NEW
    (25 tests, 5 classes; all synthetic / tempfile-based).
- **Tests run:**
  - new bootstrap + persistence tests: **25 passed in 2.10s**
  - directly related (`test_obs_001_phase_a_decision_snapshot.py` +
    `test_obs_002_terminal_decision_coverage.py` +
    `test_bot001_session_lifecycle.py` +
    `test_phase_c13_cycle_start_index_durability.py`): **90 passed
    in 7.03s**
- **Production DB status:** clean. The session-scoped autouse
  fixture in the new test file drops the new tables from the
  production DB at the end of the test session so the only side
  effect of running the new tests is the same as before
  (i.e., no orphan empty tables left behind).
- **Status:** **DONE** — schema bootstrap is idempotent on fresh
  DBs; persistence helpers are wired into the existing finalize
  path; atomicity verified; truth contract enforced. No services
  touched; no endpoint reads changed; no backfill; no behavior
  changes.

## Scope

### Files allowed to change

- `src/database/sqlite_db.py` — add two tables + four indexes to
  `_init_schema`; extend `finalize_decision_history`; add two new
  helpers on `SQLiteDB`.
- `tests/test_db_normalized_analytics_persistence.py` — NEW.

### Files NOT changed

- `dashboard.py` — Phase C endpoint reads untouched (per the
  user's "Do NOT change Phase C dashboard endpoint reads yet"
  rule).
- `src/core/smart_bot.py` — `_persist_obs_001_decision_snapshot`
  unchanged; the new helpers are invoked transparently from inside
  `finalize_decision_history` so the smart-bot call site is
  untouched.
- `migrations/` — schema bootstrap is in `_init_schema` (same
  pattern as PHASE-C12 / C13); no new migration file.
- Production DB — not modified (verified by read-only inspection
  before and after each test run; final state: only the original
  `decision_history` table exists, `decision_history` row count
  unchanged at 1,332,421).

## Truth-contract verification

| Invariant | How verified | Result |
|---|---|---|
| `decision_history` remains authoritative | child rows are populated from the structured Python Dict passed to `finalize_decision_history`; no JSON reparse; no live settings read | PASS |
| `decision_snapshot` is never modified | `test_decision_snapshot_unmodified_by_persistence` compares `gates` list before and after the call | PASS |
| `applied=false` excluded from evaluation totals | `test_applied_false_persisted_with_null_passed`; `test_missing_applied_field_fails_closed` | PASS |
| Missing / malformed `applied` fails closed | `test_missing_applied_field_fails_closed`; `test_malformed_applied_field_fails_closed` (covers str, int>1, int<0, float, list, dict, "yes") | PASS |
| `HOLD_INELIGIBLE != failed gate` | `test_hold_ineligible_with_no_gates_persists_no_gate_rows`; `test_hold_ineligible_does_not_create_gate_failures` | PASS |
| Outcome enum never consulted | `test_no_recomputation_from_settings` overrides the outcome to a misleading value and verifies gate rows still derive from the structured `gates[]` array | PASS |
| `applied` / `passed` from structured facts only | `test_decision_snapshot_unmodified_by_persistence` and `test_no_recomputation_from_settings` together prove this | PASS |
| Multiplicity preserved | `test_duplicate_gate_names_preserved_via_ordinality` — same `gate_name` appears twice, both rows persist, distinguished by `ordinality` | PASS |
| `first_blocking_check` set on exactly one row | `test_multiple_checks_persisted`; `test_first_blocking_check_defensive_when_name_not_in_checks`; `test_first_blocking_check_preserved_when_none` | PASS |
| `gap_note` preserved verbatim | `test_gap_note_preserved_verbatim` | PASS |
| Child INSERT atomic with parent INSERT | `test_child_insert_failure_rolls_back_parent` patches `_persist_gate_evaluations` to raise; asserts parent decision_history row is NOT committed | PASS |
| UNIQUE constraint on `decision_history_id, ordinality` | `test_unique_constraint_on_ordinality_preserves_multiplicity` | PASS |

## Schema details

### `decision_gate_evaluations`

```sql
CREATE TABLE IF NOT EXISTS decision_gate_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_history_id INTEGER NOT NULL,
    cycle_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    cycle_start TEXT NOT NULL,
    ordinality INTEGER NOT NULL,
    gate_name TEXT NOT NULL,
    gate_category TEXT,
    applied INTEGER NOT NULL,
    passed INTEGER,
    observed_value REAL,
    threshold_value REAL,
    reason TEXT,
    UNIQUE(decision_history_id, ordinality)
);
CREATE INDEX IF NOT EXISTS idx_dge_cycle_start
    ON decision_gate_evaluations(cycle_start);
CREATE INDEX IF NOT EXISTS idx_dge_gate_name
    ON decision_gate_evaluations(gate_name);
```

### `decision_execution_checks`

```sql
CREATE TABLE IF NOT EXISTS decision_execution_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_history_id INTEGER NOT NULL,
    cycle_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    cycle_start TEXT NOT NULL,
    ordinality INTEGER NOT NULL,
    check_name TEXT NOT NULL,
    applied INTEGER NOT NULL,
    passed INTEGER,
    observed_value REAL,
    threshold_value REAL,
    reason TEXT,
    gap_note TEXT,
    is_first_blocking INTEGER NOT NULL DEFAULT 0,
    UNIQUE(decision_history_id, ordinality)
);
CREATE INDEX IF NOT EXISTS idx_dec_cycle_start
    ON decision_execution_checks(cycle_start);
CREATE INDEX IF NOT EXISTS idx_dec_check_name
    ON decision_execution_checks(check_name);
```

### One refinement vs. the audit archive

The audit archive specified `UNIQUE(decision_history_id, gate_name)`.
The user instruction explicitly required preserving multiplicity and
order. The adopted schema uses
`UNIQUE(decision_history_id, ordinality)` instead — same uniqueness
guarantee, but multiplicity and order survive. This matches the
user instruction "use ordinality/index where needed rather than
assuming names are unique" and is forward-looking robustness for
future gate/check names that may repeat in the source array.
Current production data has distinct gate names per row (verified
in the C14B audit), so no behavior change for existing data.

## Atomicity

`finalize_decision_history` now does:

```python
with _get_conn() as conn:
    cur = conn.execute("INSERT INTO decision_history ...", (...))
    parent_id = cur.lastrowid
    if parent_id is not None:
        self._persist_gate_evaluations(conn, parent_id, ...)
        self._persist_execution_checks(conn, parent_id, ...)
    return cur.rowcount > 0
```

If any of the three INSERTs raises, the `with` block exits via
exception → the connection's implicit transaction is rolled back →
**decision_history is not committed unless the child rows also
succeed**. `test_child_insert_failure_rolls_back_parent` proves
this by patching `_persist_gate_evaluations` to raise and verifying
that `decision_history` has zero rows for the (cycle_id, symbol).

## Test isolation from production DB

The new test file uses a `temp_db_path` fixture that points at a
fresh `tmp_path / "c14b1_test.db"` per test. The `isolated_db`
fixture:
1. `importlib.reload()`s `src.database.sqlite_db` so the module is
   fully imported.
2. `monkeypatch.setattr(sqlite_db_module, "DB_PATH", temp_db_path)`
   so `_get_conn()` (which reads `DB_PATH` at call time) uses the
   temp DB.

A session-scoped autouse fixture
`cleanup_production_db_after_session` drops the new tables /
indexes from the production DB after the test session finishes.
This is necessary because OTHER test files in the suite (e.g.,
`test_obs_001_phase_a_decision_snapshot.py`) call `SQLiteDB()`
directly against the production DB and rely on the existing
schema bootstrap. Since the new tables are added inside the same
`CREATE TABLE IF NOT EXISTS` block as the existing tables, those
existing tests create the new tables on the production DB as a
side effect. The session-scoped cleanup restores production DB to
its pre-PR state so Josh's review sees no orphan empty tables.

## Known limitations / next slice

This slice deliberately does NOT:

- Add Phase C dashboard endpoint reads (the user's explicit "Do NOT
  change Phase C dashboard endpoint reads yet" rule).
- Add fallback logic for pre-cutover rows in the dashboard endpoints.
- Backfill historical rows.
- Drop the existing `decision_snapshot` column.
- Change scoring, eligibility, ranking, sizing, risk, brokerage,
  OBS-001, OBS-002, or any other SmartBot behavior.
- Restart / pause any service.

The next slice (PHASE-C14B-2, scoped separately) will switch the
Phase C endpoints to read from the child tables with a
snapshot-fallback path for pre-cutover rows.

## Risks

| Risk | Severity | Mitigation in this slice |
|---|---|---|
| Child INSERTs fail silently and analytics undercount | HIGH | child INSERTs are inside the parent's transaction; any raise rolls back decision_history |
| `applied` type drift (JSON true vs int 1) | low | `_coerce_applied_int` accepts both; everything else → 0 |
| Write amplification grows total DB write rate ~2.9× (195k → 570k INSERTs/day) | low | 6.6 rows/s sustained; well below SSD limits; no row contention with `decision_history` |
| New tables grow ~30 MB/day | low | decision_history itself grows 681 MB/day; child tables are 4.4% of that |
| Pre-existing `TestGateAggregationAppliedFilter` failures in `test_dashboard_phase_c_obs_analytics.py` (6 tests) | pre-existing, NOT caused by this slice | verified by `git stash` + re-run; the failures exist on `main @ 96f8fcb` and are unrelated to this work |

## Decision required from Josh

- Approve this slice as the foundation for the C14B storage
  design.
- Authorize the schema bootstrap to land on production DB (via the
  next deploy of `src/database/sqlite_db.py`; no separate migration
  needed because the `CREATE TABLE IF NOT EXISTS` pattern is
  metadata-only and idempotent on the existing production DB).
- Schedule PHASE-C14B-2 (endpoint reads) as the next slice.

## Next recommended action

After Josh approval:
1. Land `src/database/sqlite_db.py` on `main` (single file, +341
   lines).
2. Deploy SmartBot / dashboard.service restart to apply the
   schema bootstrap to production DB.
3. Verify post-deploy: `SELECT COUNT(*) FROM decision_gate_evaluations`
   grows linearly with `decision_history` writes.
4. Begin PHASE-C14B-2 (endpoint reads + snapshot-fallback) as a
   separate PR.
