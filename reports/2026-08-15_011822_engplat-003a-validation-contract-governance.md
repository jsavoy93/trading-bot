# ENGPLAT-003A Validation Contract Governance Archive

## Summary
Performed the smallest governance amendment to resolve the ENGPLAT-003A validation-contract contradiction.

## Exact old wording
```markdown
The generated `ProjectConfig` must pass:

- `parse_project_config()`
- `validate_project_config()`
```

## Exact new wording excerpt
```markdown
The generated `ProjectConfig` must pass structural parsing via
`parse_project_config()`. Before any write, ENGPLAT-003A preflight must validate
all intrinsic `ProjectConfig` semantics that do not depend on runtime filesystem
existence.

Runtime filesystem-readiness validation is deferred until the configured runtime
directories/files are legitimately created by later runtime components.
```

Full new governance also lists intrinsic preflight validation requirements and deferred runtime-readiness checks.

## Files changed
- `AGENT_BACKLOG.md`
- `ITERATION_PROGRESS_LOG.md`
- `REPORT.md`
- `reports/2026-08-15_011822_engplat-003a-validation-contract-governance.md`

## Validation split clarified

### Intrinsic / required during 003A preflight
- `project_id` validity
- `display_name` validity
- `repository_root` validity
- absolute and contained configured governance, workflow, event, and report paths
- governance path configuration for the five generated governance files
- workflow/event/report path containment under the repository root
- QA command validity and safety
- positive QA timeout
- owner presence and uniqueness
- agent-owner presence and uniqueness
- `agents_may_merge = False`
- any other pure `ProjectConfig` semantic rule that does not depend on runtime filesystem existence

### Runtime readiness / deferred
- workflow-store parent existence
- event-store parent existence
- report directory existence/readiness
- any other check whose only failure is that lazily-created runtime paths do not yet exist

## Confirmations
- Runtime-readiness validation is deferred, not bypassed.
- `validate_project_config()` itself is unchanged.
- No `engineering/models.py` changes.
- No runtime code changes.
- No tests changed.
- No PR #38 implementation changes.
- ENGPLAT-003A scope was not broadened.
- ENGPLAT-003B was not started.
- Fantasy work was not touched.

## Governance consistency checks
- ENGPLAT-003A section no longer requires immediate full runtime-readiness `validate_project_config()` success.
- Preserved: exactly five managed files.
- Preserved: no `engineering/`, `reports/`, `.agent-state`, registry persistence, CLI, overwrite/force.
- Preserved: preflight + fail-fast with partial state possible.
- Preserved: zero writes on known validation/conflict failure.
- Preserved: generic templates only.
- Runtime/test implementation file audit: PASS — no `engineering/`, `tests/`, `dashboard_api/`, `src/`, or fantasy paths changed.
- `git diff --check`: PASS.

## Decision
Ready for Josh read-only review. Do not merge automatically.
