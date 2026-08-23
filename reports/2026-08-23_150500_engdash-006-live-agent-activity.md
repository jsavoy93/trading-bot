# ENGDASH-006 — Live Agent Activity and Execution Visibility

Task: Implement ENGDASH-006 as one bounded read-only dashboard slice.
Branch: `agent/engdash-006-live-agent-activity`
Commit: `final branch commit at PR creation`
Status: DONE — pending Josh review/PR merge

## Summary

Added normalized live/recent engineering-agent activity to the existing read-only Engineering Dashboard without introducing a second activity-state system.

## Activity model

- `AgentActivitySummary`: project, task, agent, role, workflow/run identity, branch, phase, derived status, timing, latest activity, last completed action, blocker, timeout state, recovery state, and bounded safe detail.
- `RecentExecutionSummary`: project, task, agent, run, branch, final status, timing, last completed action, and bounded result summary.

## Validation

- Focused dashboard/query tests: `68 passed, 2 warnings`
- Full safe suite: `774 passed, 81 warnings`
- Manual persisted workflow/dashboard exercise: PASS
- `git diff --check`: PASS

## Safety

No new authoritative state store. No write controls. No trading runtime behavior changes. No process inspection. No raw stdout/stderr, prompt, secret, credential, arbitrary shell/process output, or private reasoning exposure.

## Next action

Open PR for Josh review. Do not merge automatically.
