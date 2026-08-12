# ENGPLAT-002C1 Governance Remediation — Detailed Archive

## Metadata

- **Date:** 2026-08-12
- **Time:** 00:10 UTC
- **Task:** ENGPLAT-002C1 — Git Adapter Implementation and Integration (Slice 1 of ENGPLAT-002C)
- **Branch:** `agent/engplat-002c1-git-adapter`
- **Status:** PENDING JOSH APPROVAL — governance only, no implementation
- **Base:** `main`

---

## Purpose

Replace `_DeferredGitAdapter` with concrete `GitAdapterImpl` wrapping `GitService`;
update `manager.py` to use `ctx.git` instead of constructing `GitService` directly.

---

## Pre-Planning Code Inspection Results

### GitReadAdapter Protocol — COMPLETE, no changes needed

File: `engineering/adapters.py`, lines 35–63

Already defined with all required methods:
- `current_branch() -> str`
- `is_clean() -> bool`
- `repository_state() -> RepositoryState`
- `branch_exists(branch: str) -> bool`
- `is_ancestor(ancestor: str, descendant: str) -> bool`

No protocol changes are authorized or needed.

### GitService — READY TO WRAP

File: `engineering/git_service.py`

`GitService` already implements all five `GitReadAdapter` methods exactly:
- `GitService.current_branch()` → matches `GitReadAdapter.current_branch()`
- `GitService.is_clean()` → matches `GitReadAdapter.is_clean()`
- `GitService.repository_state()` → matches `GitReadAdapter.repository_state()`
- `GitService.branch_exists(branch)` → matches `GitReadAdapter.branch_exists(branch)`
- `GitService.is_ancestor(ancestor, descendant)` → matches `GitReadAdapter.is_ancestor(ancestor, descendant)`

`GitService` also has `prepare_feature_branch()` and `create_and_checkout_branch()` — these are
mutation methods NOT part of `GitReadAdapter` and must NOT be exposed through the adapter.

### _DeferredGitAdapter — TO BE REPLACED

File: `engineering/context.py`, lines 52–67

Currently raises `CapabilityUnavailable(project_id, "git")` for all methods.
Will be replaced with `GitAdapterImpl` in `build_project_context()`.

### manager.py — ONE CALL PATTERN TO MIGRATE

File: `engineering/manager.py`, lines 110–112:
```python
from engineering.git_service import GitService
git = GitService(repo_root)
state = git.repository_state()
```

Migration: `state = ctx.git.repository_state()`

`repo_root` variable (from `config.repository_root`) is retained for print output.
No other `git` calls exist in `manager.py`.

### telegram_service.py Path.cwd() — EXCLUDED FROM 002C1

File: `engineering/telegram_service.py`, line 368:
```python
repo_root = Path.cwd().resolve()
```

**Decision: SHOULD BE SEPARATE SLICE**

Rationale:
1. The smoke launcher is a standalone entry point with no `ProjectConfig` context
2. `repo_root` is used for: (a) AGENT_BACKLOG.md existence check, (b) WorkflowStore path,
   (c) backlog_path argument to `EngineeringQueryService`
3. Unlike `manager.py` which uses `config.repository_root`, the smoke launcher has no
   config object to derive `repo_root` from
4. Replacing this requires either a new `--repo-root` CLI argument or a separate
   architectural convention for smoke-launcher discovery
5. Including it in 002C1 would expand scope significantly without clear benefit to
   the GitAdapter slice

This will be addressed as ENGPLAT-002C2 or a dedicated smoke-launcher slice.

---

## Allowed Areas

### REQUIRED (runtime)

**`engineering/context.py`**
- Add `GitAdapterImpl` class — thin wrapper around `GitService`
- Replace `_DeferredGitAdapter(config.project_id)` with `GitAdapterImpl(config.repository_root)`
  in `build_project_context()` return statement
- Do NOT change function signature of `build_project_context()`
- Do NOT modify any other adapter construction

**`engineering/manager.py`**
- Remove: `from engineering.git_service import GitService`
- Remove: `git = GitService(repo_root)`
- Change: `state = git.repository_state()` → `state = ctx.git.repository_state()`
- `repo_root` from `config.repository_root` is retained for print output
- Do NOT change task selection, branch safety checks, approval gates, or workflow logic

### REQUIRED (tests)

**`tests/test_engineering_git_adapter.py`** (new file)
- `isinstance(GitAdapterImpl(...), GitReadAdapter)` → True
- `git_adapter.repository_state()` matches `GitService(repo_root).repository_state()` — round-trip
- All five protocol methods delegate correctly
- Existing `tests/test_engineering_git_service.py` tests remain unchanged and pass
- `tests/test_engineering_manager.py` passes unchanged

### NOT AUTHORIZED

- `engineering/adapters.py` — GitReadAdapter protocol is complete, no changes
- `engineering/git_service.py` — GitService class unchanged, GitAdapterImpl wraps it
- `engineering/telegram_service.py` — smoke launcher excluded (separate slice)
- Any QA, File, or reporter migration
- Any dashboard or API changes
- Any workflow_store, event_store, or backlog changes
- Any new routes, filesystem side effects, or Git mutations

---

## GitAdapterImpl Design

```python
class GitAdapterImpl(GitReadAdapter):
    """Concrete GitReadAdapter wrapping GitService.

    repo_root is supplied explicitly. No Path.cwd(), no discovery.
    All methods delegate to the wrapped GitService instance.
    """

    def __init__(self, repo_root: Path) -> None:
        self._git = GitService(repo_root)

    def current_branch(self) -> str:
        return self._git.current_branch()

    def is_clean(self) -> bool:
        return self._git.is_clean()

    def repository_state(self) -> RepositoryState:
        return self._git.repository_state()

    def branch_exists(self, branch: str) -> bool:
        return self._git.branch_exists(branch)

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return self._git.is_ancestor(ancestor, descendant)
```

---

## Acceptance Criteria

1. `isinstance(GitAdapterImpl(Path(...)), GitReadAdapter)` returns `True`
2. `GitAdapterImpl(repo_root).repository_state()` returns identical `RepositoryState`
   to `GitService(repo_root).repository_state()` for the same `repo_root`
3. `manager.py` contains no direct import or instantiation of `GitService`
4. `manager.py` calls `ctx.git.repository_state()`
5. `tests/test_engineering_git_adapter.py` exists and passes
6. `tests/test_engineering_git_service.py` passes unchanged
7. `tests/test_engineering_manager.py` passes unchanged
8. No `Path.cwd()` introduced in any changed file
9. `ctx.qa` and `ctx.files` remain `CapabilityUnavailable`

---

## Stop Criteria

Stop and report if:
- `GitReadAdapter` protocol signature changes
- Any behavioral change detected in manager output or existing tests
- Any new filesystem side effects or Git mutations
- Scope expansion requested

---

## Extraction-Readiness Impact (ENGPLAT-004)

ENGPLAT-002C1 advances the following ENGPLAT-004 readiness criteria:
- "No runtime service may directly call `Path.cwd()`" — manager.py Path.cwd() removed
- "All path resolution derives from ProjectConfig" — ctx.git used instead of GitService instantiation
- "Adapter boundary established for Git operations" — GitAdapterImpl operational

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| GitAdapterImpl introduces subtle behavioral difference | Low | Medium | Round-trip test verifies identical output |
| Manager behavior changes inadvertently | Low | High | Only one call pattern changes; existing tests cover behavior |
| Backward compatibility with existing GitService callers | Low | Low | GitService remains unchanged; only manager.py migrates |

---

## Decisions

1. **GitReadAdapter protocol:** Complete — no changes authorized
2. **GitAdapterImpl location:** `engineering/context.py` (consistent with GovernanceAdapterImpl, EventAdapterImpl, WorkflowAdapterImpl)
3. **telegram_service.py:** Excluded from 002C1 — separate architectural concern (smoke launcher discovery)
4. **Implementation scope:** Minimal — only ctx.py + manager.py + one new test file
