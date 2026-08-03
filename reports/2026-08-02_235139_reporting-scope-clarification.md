# Executive summary

Added the approved reporting-scope clarification and explicit prohibition against printing full `REPORT.md` contents to the terminal. The reporting-rule changes are approved for commit.

## Task or purpose

Commit the approved persistent reporting rules while keeping terminal output concise and leaving the ignored rolling report unstaged.

## Branch

`agent/trading-ops-011-codex-wrapper`

## Commit

`Add concise persistent reporting rules` (created as part of this task; final hash reported after commit).

## Files changed

- `AGENTS.md`
- `REPORT.md` (rolling reporting artifact)
- `reports/2026-08-02_235139_reporting-scope-clarification.md` (archived reporting artifact)

## Tests run

- `git diff --check` before staging
- Inspection of the exact `AGENTS.md` diff
- Inspection of the exact staged file list
- Verification that `REPORT.md` is ignored and unstaged

## Exact test results

- `git diff --check`: PASS, no output.
- Exact diff inspection: PASS, the reporting-scope paragraphs and explicit terminal-output prohibition appear under `Reporting Requirements`.

## Acceptance evidence

- Criterion: Add the supplied reporting applicability text under `Reporting Requirements`.
  - Proof method: `git diff -- AGENTS.md`
  - Exact result: Three requested paragraphs added before the numbered reporting procedure.
  - Status: PASS
- Criterion: Add exactly `Do not print REPORT.md contents to the terminal.` without changing other policy text.
  - Proof method: `git diff -- AGENTS.md`
  - Exact result: The exact sentence appears once immediately after the concise terminal-response instruction.
  - Status: PASS
- Criterion: Commit only `AGENTS.md` and the archived report; do not commit `REPORT.md` or unrelated files.
  - Proof method: `git diff --cached --name-only` and `git check-ignore -v REPORT.md` before commit.
  - Exact result: Staged paths are limited to `AGENTS.md` and `reports/2026-08-02_235139_reporting-scope-clarification.md`; `REPORT.md` is ignored and unstaged.
  - Status: PASS

## Known risks

No runtime or trading behavior is affected. This documentation-only commit is being made on the existing OPS-011 branch and does not begin OPS-012 or implementation work.

## Manager decision

APPROVED FOR COMMIT with the exact requested subject and restricted staged file set.

## Next recommended action

Stop after reporting the commit result. Do not begin OPS-012 or any other task.
