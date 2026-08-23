# Dashboard health severity classification — detailed report

## Objective
Ensure INFO-level optional data-source warnings do not downgrade Engineering Dashboard overall health.

## Read-only trace findings
- `EngineeringHealthSummary.overall_status` is derived in `dashboard_api/engineering_read_model.py::_engineering_health()`.
- Before this change, aggregation special-cased `ERROR`, then treated any non-empty `warnings` list as `DEGRADED`.
- `HealthWarning.severity` already distinguishes `INFO`, `WARNING`, and `ERROR`, but aggregation did not distinguish INFO from severity levels that should degrade health.
- Existing tests verified missing GitHub metadata remained visible, but did not assert that INFO-only warnings keep overall health healthy.

## Root cause
The health aggregation used `warnings` as a blanket downgrade signal. The GitHub PR metadata warning is intentionally `INFO` because it is optional/provider-wiring metadata, but the blanket check still caused `overall_status=DEGRADED`.

## Exact health-severity mapping after fix
- No warnings/blockers/test failures/repository issues: `HEALTHY`.
- INFO-only warnings: `HEALTHY`; INFO warning remains visible in `health_warnings` and UI.
- WARNING present: `DEGRADED`.
- ERROR present: `ERROR`.
- Repository unsafe/dirty: `DEGRADED` via existing repository warning and explicit repository safety check.
- Query-service failure: `DEGRADED` via existing WARNING.
- Failed latest test or blockers: unchanged, still `DEGRADED`.

## Files changed
- `dashboard_api/engineering_read_model.py`
- `tests/test_dashboard_engineering_read_model.py`
- `tests/test_dashboard_api_app.py`
- `ITERATION_PROGRESS_LOG.md`
- `reports/2026-08-23_155859_dashboard-health-severity-classification.md`

## Validation
- Focused dashboard health/read-model/provider tests: `48 passed, 2 warnings`.
- Full safe suite: `779 passed, 80 warnings`.
- `git diff --check`: PASS.

## Manual checks before commit
- Snapshot remained `DEGRADED` before commit because the repository was intentionally dirty from this implementation branch.
- GitHub INFO remained visible.
- Query-service warning absent and backlog counts populated.

## Additional trace during manual verification
The first clean-branch snapshot still showed `DEGRADED` because idle source data (`No active workflow is recorded.`) was treated as a workflow blocker. ENGDASH-006 requires safe empty live activity, and idle/no-active-workflow is informational state rather than a blocker. The fix was extended narrowly so `_workflow_summary()` only turns no-active-workflow gaps into blockers when the gap text contains `blocked`, `gap`, or `missing`.

## Pending after report write
- Commit/amend.
- Manual clean-branch snapshot showing `HEALTHY` with GitHub INFO remaining.
- Push and PR.
