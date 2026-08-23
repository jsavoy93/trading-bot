# Dashboard health governance cleanup — detailed report

## Objective
Return the Engineering Dashboard data source health to clean operation with the smallest safe remediation.

## Scope constraints observed
- No trading logic changes.
- No trading strategy changes.
- No workflow semantic changes.
- No new dashboard features.
- No ENGCTRL work.
- No broad refactor.
- No deletion of historical evidence.

## Root cause
`AGENT_BACKLOG.md` contained parser-controlled `Status:` fields with inline historical merge comments. The backlog parser treats the entire right-hand side of `Status:` as an enum value, so values such as `DONE  # merged PR #36 (8d8d0c0)` are invalid.

## Parser-sensitive fields audited
Audited every task heading matched by `engineering.backlog.TASK_HEADING_PATTERN` and every `Status:`, `Owner:`, and `Priority:` line inside task sections.

Malformed fields corrected:
- ENGPLAT-002A: `Status: DONE  # merged PR #25 (068a102)` → `Status: DONE` plus `Merge note: merged PR #25 (068a102)`
- ENGDASH-005 historical section: `Status: DONE  # merged PR #30 (d8f2d58)` → `Status: DONE` plus `Merge note: merged PR #30 (d8f2d58)`
- ENGPLAT-002C1: `Status: DONE  # merged PR #32 (c866a8c)` → `Status: DONE` plus `Merge note: merged PR #32 (c866a8c)`
- ENGPLAT-002C2: `Status: DONE  # merged PR #34 (c4fd1b8)` → `Status: DONE` plus `Merge note: merged PR #34 (c4fd1b8)`
- ENGPLAT-002C3: `Status: DONE  # merged PR #36 (8d8d0c0)` → `Status: DONE` plus `Merge note: merged PR #36 (8d8d0c0)`
- ENGPLAT-003A: `Status: DONE  # merged PRs #37/#38/#39 (b91b6e2)` → `Status: DONE` plus `Merge note: merged PRs #37/#38/#39 (b91b6e2)`
- ENGDASH-005 stale-status remediation entry: `Status: DONE  # merged PR #30 (d8f2d58); duplicate entry resolved` → `Status: DONE` plus `Merge note: merged PR #30 (d8f2d58); duplicate entry resolved`

## Duplicate/stale entries
Detected duplicate task IDs:
- `GOV-001`
- `ENGDASH-005`

These are preserved as historical/stale governance sections because deleting or rewriting historical evidence is higher risk and they do not currently break parser execution. The remediation only normalizes parser-controlled values.

## Validation so far
Focused dashboard/query validation:
`46 passed, 2 warnings`

## Remaining validation pending at initial report write
- Full safe suite.
- `git diff --check`.
- Manual snapshot call on a clean committed branch.
- Manual dashboard health check on a clean committed branch.
- Push and PR.


## Final validation before commit
- Focused dashboard/query tests: `46 passed, 2 warnings`.
- Full safe suite: `774 passed, 81 warnings`.
- `git diff --check`: PASS.
- Backlog parser: PASS, `50` parsed tasks; status counts `REVIEW=4`, `DONE=29`, `TODO=16`, `BLOCKED=1`.
- Manual snapshot before commit: status 200; query-service warning gone; backlog counts populated; `live_activity` and `recent_executions` present; remaining warnings were repository dirty state and GitHub PR metadata INFO.
- Manual dashboard before commit: status 200; Live agent activity and Recent executions sections render; engineering routes are GET-only.

## GitHub metadata diagnosis
`gh auth status` is authenticated for `github.com`, and repository remote is `git@github.com:jsavoy93/trading-bot.git`. The dashboard read model defaults to `EmptyPullRequestMetadataReader`, so PR metadata unavailability is expected provider wiring / not-yet-implemented integration when no explicit PR reader is injected. This cleanup does not expand scope into GitHub integration work.

## Expected post-commit check
After commit, repository dirty-state warning should clear. The GitHub metadata INFO warning may remain unless an explicit PR reader is injected; that is legitimate non-blocking external/provider-wiring availability, not a backlog parser issue.


## Post-commit clean-branch validation
- Commit: `3d6609b66692c6e286a5b9e877d8512cf58b168d`
- `git status --short`: clean.
- `git diff --check`: PASS.
- Manual snapshot after commit: status 200; `repository_safe=True`; query-service warning gone; backlog counts populated; `live_activity` and `recent_executions` present.
- Final snapshot health: `DEGRADED` only because GitHub PR metadata is unavailable as INFO through the default `EmptyPullRequestMetadataReader`.
- Remaining warning: `github` INFO, `Pull-request metadata is unavailable.`
- Dashboard normal-use readiness: YES for repository/query/backlog/live-activity/recent-execution health; GitHub metadata remains a non-blocking provider-wiring limitation.
