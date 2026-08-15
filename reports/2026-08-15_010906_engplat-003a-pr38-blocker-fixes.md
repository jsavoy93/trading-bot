# ENGPLAT-003A PR #38 Blocker Fix Archive

## Summary
Fixed only the three blocking findings from the read-only PR #38 review.

## Scope
Allowed implementation files changed:
- `engineering/bootstrap.py`
- `tests/test_engineering_bootstrap.py`

Authorized reporting/progress files changed:
- `MENTOR.md`
- `ITERATION_PROGRESS_LOG.md`
- `reports/2026-08-15_010906_engplat-003a-pr38-blocker-fixes.md`

No changes were made to `engineering/models.py`, `engineering/context.py`, `engineering/adapters.py`, `engineering/manager.py`, workflow code, dashboard code, `src/`, or fantasy files.

## Blocker fixes

### 1. Predictable ProjectConfig semantic validation after writes
Fixed in `engineering/bootstrap.py` by adding bootstrap-local intrinsic ProjectConfig validation during `_preflight(...)` before managed artifact writes. The helper validates structural parse, intrinsic field semantics, absolute/contained governance/workflow/report paths, QA command safety, positive timeout, owner/agent owner presence and uniqueness, and approval-gated merge policy. It does not create runtime directories to satisfy readiness checks.

### 2. Unauthorized `engineering/` directory creation
Removed the post-write `engineering/` parent directory creation from `apply_bootstrap(...)`. Successful apply now creates exactly the five authorized managed files and no runtime structure.

### 3. Post-write `report_dir` validation filtering
Removed the post-write semantic validation/filtering block from `apply_bootstrap(...)`. There is no `report_dir` suppression or selected-error filtering in apply success handling. Runtime filesystem readiness remains outside ENGPLAT-003A.

## Behavioral verification
A direct bootstrap apply audit produced:
- success: `True`
- destination entries: `['AGENTS.md', 'AGENT_BACKLOG.md', 'AGENT_OPERATING_PLAN.md', 'AUTONOMOUS_ENGINEERING_HANDOFF.md', 'OWNERS.md']`
- `engineering_exists`: `False`
- `reports_exists`: `False`
- `agent_state_exists`: `False`
- `registry_exists`: `False`
- `workflow_parent_exists`: `False`

## Test Evidence

### Focused bootstrap tests
Command:
`PYTHONDONTWRITEBYTECODE=1 TESTING=1 UNIT_TESTING=1 /root/.openclaw/workspace/trading-bot/.venv/bin/python -m pytest tests/test_engineering_bootstrap.py -q`

Result: `51 passed, 1 warning in 0.57s`.

### ProjectConfig/Context regressions
Command:
`PYTHONDONTWRITEBYTECODE=1 TESTING=1 UNIT_TESTING=1 /root/.openclaw/workspace/trading-bot/.venv/bin/python -m pytest tests/test_engineering_project_config.py tests/test_engineering_project_context.py -q`

Result: `93 passed, 1 warning in 0.40s`.

### Full safe suite
Command:
`PYTHONDONTWRITEBYTECODE=1 TESTING=1 UNIT_TESTING=1 PAPER_MODE=true ALPACA_API_KEY=PKTEST000000000000000 ALPACA_API_SECRET=*** ALPACA_BASE_URL=https://paper-api.alpaca.markets /root/.openclaw/workspace/trading-bot/.venv/bin/python -m pytest -q`

Result: `604 passed, 2 warnings in 20.42s`.

Warnings were pre-existing/non-blocking environment/dependency warnings:
- `PytestConfigWarning: Unknown config option: timeout`
- `DeprecationWarning: websockets.legacy is deprecated`

### Diff check
Command: `git diff --check`

Result: PASS.

## Scope audit
- Exactly five bootstrap destination files confirmed.
- No `engineering/` creation confirmed.
- No `reports/` creation confirmed.
- No `.agent-state` creation confirmed.
- No validation filtering confirmed.
- No registry behavior confirmed.
- No CLI/overwrite behavior confirmed.
- No prohibited files changed.

## Decision
Ready for Josh re-review of PR #38. Do not merge automatically.
