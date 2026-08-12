# ENGPLAT-002C2 Governance Remediation — Detailed Archive

## Metadata

- **Date:** 2026-08-12
- **Time:** 01:24 UTC
- **Task:** ENGPLAT-002C2 — QAAdapter Implementation (Slice 2 of ENGPLAT-002C)
- **Branch:** `agent/engplat-002c2-qa-adapter-governance`
- **Status:** PENDING JOSH APPROVAL — governance planning only, no implementation
- **Base:** `main`

---

## Purpose

Replace `_DeferredQAAdapter` with concrete `QAAdapterImpl` exposing
`ProjectConfig.qa_commands` and `ProjectConfig.qa_timeout_seconds` through
`ProjectContext`.

---

## Pre-Planning Code Inspection Results

### QAAdapter Protocol — Already Complete, No Changes Needed

File: `engineering/adapters.py`, lines 109–121

```python
@runtime_checkable
class QAAdapter(Protocol):
    """QA configuration access.

    run_qa() execution is deferred to 002C. This adapter provides only
    configuration access (command assembly and timeout).
    """

    def configured_command(self) -> tuple[str, ...]:
        """Return the configured QA command as a tuple of string segments."""

    def timeout_seconds(self) -> int:
        """Return the configured QA timeout in seconds (always positive)."""
```

Methods: `configured_command()`, `timeout_seconds()` only. No `run_qa()`.
Protocol is explicitly config-only.

### qa_runner.py — Not Required for 002C2

```
engineering/qa_runner.py
├── QA_TIMEOUT_SECONDS = 300  ← hardcoded, not from ProjectConfig
├── run_qa(repo_root, command=None, clock=monotonic) → QAExecution
│   ├── Uses QA_TIMEOUT_SECONDS (hardcoded, no override)
│   ├── _configured_command() reads ENGINEERING_QA_COMMAND env var
│   └── Returns QAExecution dataclass
```

- `run_qa()` does NOT accept a `timeout` parameter
- `qa_runner.py` does NOT need to change for config-only QAAdapterImpl
- `run_qa()` is called by `workflow/qa.py` and `engineering/qa.py` (separate execution concern)

### _DeferredQAAdapter — To Be Replaced

File: `engineering/context.py`, lines 79–92

Currently raises `CapabilityUnavailable(project_id, "qa")` for all methods.
Will be replaced with `QAAdapterImpl` in `build_project_context()`.

---

## QAAdapterImpl Design

```python
class QAAdapterImpl(QAAdapter):
    """Concrete QAAdapter exposing ProjectConfig QA configuration.

    Does NOT execute QA. run_qa() execution is a separate concern addressed
    in a future slice. This adapter provides configuration access only.
    """

    def __init__(self, config: ProjectConfig) -> None:
        self._config = config

    def configured_command(self) -> tuple[str, ...]:
        return self._config.qa_commands

    def timeout_seconds(self) -> int:
        return self._config.qa_timeout_seconds
```

---

## Why CONFIG-ONLY

The `QAAdapter` protocol docstring explicitly states: "run_qa() execution is deferred to 002C."
This slice activates the CONFIGURATION ACCESS portion of the deferred work.
QA execution (`run_qa`) remains a separate slice.

---

## Allowed Areas

### REQUIRED (runtime)

**`engineering/context.py`**
- Add `QAAdapterImpl` class — thin wrapper exposing `config.qa_commands` and `config.qa_timeout_seconds`
- Replace `_DeferredQAAdapter(config.project_id)` with `QAAdapterImpl(config)`
  in `build_project_context()` return statement
- Do NOT change function signature of `build_project_context()`

### REQUIRED (test compatibility)

**`tests/test_engineering_project_context.py`**
- Replace `_DeferredQAAdapter` import/reference with `QAAdapterImpl`
- Update `_build_context_directly_for_test` helper to use `QAAdapterImpl`
- Remove stale assertions that `ctx.qa` raises `CapabilityUnavailable`
- Preserve `ctx.files` deferred `CapabilityUnavailable` assertions
- Preserve `GitAdapterImpl` expectations
- No unrelated refactoring

**`tests/test_engineering_git_adapter.py`**
- Replace `test_ctx_qa_still_raises_capability_unavailable` with
  `test_ctx_qa_is_qa_adapter_impl` verifying `ctx.qa` is concrete
- No other GitAdapter test changes

**`tests/test_engineering_qa_adapter.py`** (new file)
- Protocol conformance: `isinstance(QAAdapterImpl(...), QAAdapter)`
- `configured_command()` returns exactly `config.qa_commands`
- `timeout_seconds()` returns exactly `config.qa_timeout_seconds`
- `build_project_context(config).qa` is `QAAdapterImpl`
- `ctx.git` remains `GitAdapterImpl`
- `ctx.files` still raises `CapabilityUnavailable`
- Construction: no QA execution, no subprocess, no files, no `Path.cwd()`

### NOT AUTHORIZED

- `engineering/adapters.py` — QAAdapter protocol is already complete
- `engineering/qa_runner.py` — not required for config-only adapter
- `engineering/workflow/qa.py` — QA execution concern, separate slice
- `engineering/qa.py` — QA execution concern, separate slice
- `engineering/git_service.py` — unchanged
- `engineering/manager.py` — unchanged
- `engineering/telegram_service.py` — separate slice
- Any QA execution in this slice
- Any dashboard changes
- Any workflow_store, event_store, or backlog changes
- Any new routes or filesystem side effects

---

## Acceptance Criteria

1. `isinstance(QAAdapterImpl(...), QAAdapter)` returns `True`
2. `QAAdapterImpl(config).configured_command()` returns exactly `config.qa_commands`
3. `QAAdapterImpl(config).timeout_seconds()` returns exactly `config.qa_timeout_seconds`
4. `build_project_context(config).qa` is `QAAdapterImpl`
5. `ctx.git` is still `GitAdapterImpl`
6. `ctx.files` still raises `CapabilityUnavailable`
7. Invalid `ProjectConfig` still fails before adapter construction
8. `QAAdapterImpl` construction does not execute QA
9. `QAAdapterImpl` construction creates no files
10. `QAAdapterImpl` construction performs no subprocess execution
11. `QAAdapterImpl` construction performs no network or Git operation
12. No `Path.cwd()` or repository discovery is introduced
13. Existing QA runner behavior remains unchanged
14. Existing GitAdapter behavior remains unchanged
15. Full safe suite passes with the actual final test count reported

---

## Stop Criteria

Stop and report if:
- `QAAdapter` protocol signature changes
- Any behavioral change detected in existing test behavior
- Any new filesystem side effects or subprocess execution during construction
- Scope expansion requested
- `engineering/adapters.py` requires modification

---

## ENGSUP-001 Impact

ENGPLAT-002C2 advances supervisor readiness by making project QA configuration
available through `ProjectContext`. QA execution and result orchestration remain
separate concerns. This slice does not fully unblock ENGSUP-001 Phase 1 alone.

---

## ENGPLAT-004 Readiness Impact

002C2 advances:
- "All three deferred adapters become operational" — `ctx.qa` becomes `QAAdapterImpl`
- "ProjectContext provides configured command and timeout without hard-coding" — adapter exposes `config.qa_commands` and `config.qa_timeout_seconds`
- "Factory is side-effect free" — construction is unchanged (no side effects)
