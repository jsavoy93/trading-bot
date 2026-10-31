# Agent Branching Rules

## Branch prefixes

Trading backend and strategy work:

`agent/trading-<issue-id>-<YYYYMMDD-HHMM>`

Dashboard work:

`agent/dashboard-<issue-id>-<YYYYMMDD-HHMM>`

Integration work:

`agent/integration-<issue-id>-<YYYYMMDD-HHMM>`

## Rules

- Start from the approved base branch.
- Never work directly on main.
- Never force-push main.
- One backlog item per branch.
- One bounded iteration per invocation.
- Commits must mention the backlog issue ID.
- Agents may push branches.
- Agents may prepare pull requests.
- Agents may not merge pull requests.
- Josh approves or rejects every merge.

## Worktrees and Git Identity (ENG-08)

### The clean clone is the only worktree source

Every task worktree MUST be created from the standalone clean clone at
`/root/.openclaw/clean/trading-bot`:

```bash
git -C /root/.openclaw/clean/trading-bot fetch origin -q
git -C /root/.openclaw/clean/trading-bot worktree add \
  /root/.openclaw/worktrees/<TASKID> \
  -b agent/trading-<issue-id>-<YYYYMMDD-HHMM> \
  origin/main
```

Never run `git worktree add` from `/root/deploy/trading-bot-main` or from
`/root/.openclaw/workspace/trading-bot`. Both are prohibited sources, and
running Git in either one risks mutating a checkout that other processes
depend on.

### Protected checkouts — never modify

| Path | What it is | Rule |
|---|---|---|
| `/root/.openclaw/workspace/trading-bot` | Protected dirty checkout | Never modify. Never `git checkout`, `git stash`, `git worktree add`, or `git config` there. Treat its dirty state as someone else's active work. |
| `/root/deploy/trading-bot-main` | Live paper bot deployment (`main.py --continuous`) | Never modify. Never `git worktree add`, `git checkout`, `git reset`, or `git config` there. The running bot reads this tree. |

All Git operations for a task are executed from
`/root/.openclaw/clean/trading-bot` (to create/inspect worktrees) or from
the task's own worktree directory (to commit and push). No exceptions.

### Per-worktree Git identity

Each worktree sets its own local Git identity so commits identify the
authoring agent:

```bash
git -C /root/.openclaw/worktrees/<TASKID> config user.name <agent-id>
git -C /root/.openclaw/worktrees/<TASKID> config user.email <agent-id>@openclaw.local
```

Examples: `trading-exec` / `trading-exec@openclaw.local`,
`trading-manager` / `trading-manager@openclaw.local`,
`dashboard-agent` / `dashboard-agent@openclaw.local`.

These are **worktree-local** settings (`--worktree` scope where supported,
otherwise repo-local in the clean clone). They never affect the protected
checkouts. The email is a local identity, not a real mailbox; do not
substitute a personal or shared address.

Do not rely on the global Git identity for agent commits. If `git config
user.name` is empty inside the worktree, the commit will be attributed to
the host user and the agent becomes unidentifiable.

## Review Gate Before Asking Josh to Merge (ENG-07)

An **independent reviewer-agent verdict** is a required step before an agent
asks Josh for merge approval. The reviewer is a separate agent session that
did not author the change.

### How the verdict is obtained

1. The implementing agent pushes the branch and opens (or names) the pull
   request.
2. The implementing agent dispatches a **read-only** reviewer-agent session
   with the PR diff and the task's acceptance criteria.
3. The reviewer-agent reads the PR diff against the acceptance criteria and
   returns exactly one verdict:
   - `APPROVE` — the diff satisfies every acceptance criterion, contains no
     scope creep, and introduces no new failure.
   - `BLOCK` — with explicit reasons naming the unmet criterion, the
     offending file/line, and what change would resolve it.
4. The reviewer-agent must NOT edit files, commit, push, or merge. It is a
   read-only review.

A reviewer verdict of `BLOCK` MUST be resolved — the finding fixed and
re-reviewed, or the finding explicitly accepted by Josh with the reason
recorded — **before** the merge request is presented to Josh. A merge request
that carries an unresolved `BLOCK` is a process violation and must be
stopped, not escalated.

A `BLOCK` verdict that cannot be resolved within the task's scope becomes a
`REWORK` / `BLOCKED` status in the iteration log with the reviewer's
reasons copied verbatim, and the task stops. It does not become an
"approve anyway" request.

The review verdict, the reviewer agent id, and its reasons are recorded in
the task's report and in the `ITERATION_PROGRESS_LOG.md` entry.

