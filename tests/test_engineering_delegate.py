from datetime import UTC, datetime
from pathlib import Path

import pytest

from engineering.executor import LaunchedRun
from engineering.models import DelegationStatus, WorkflowState
from engineering.workflow.delegate import run
from engineering.workflow_store import DelegationRecord, StoredWorkflow


BACKLOG_CONTENT = """
### TEST-001 — Prevent live brokerage calls from tests

Status: TODO
Owner: trading-exec
Priority: P0

Acceptance criteria:

- Tests cannot call live endpoints.

Allowed areas:

- tests/
"""


class StubLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def launch(
        self,
        agent_name: str,
        branch: str,
        prompt: str,
        request_id: str,
    ) -> LaunchedRun:
        self.calls.append((agent_name, branch, prompt, request_id))
        return LaunchedRun("run-123", DelegationStatus.ACTIVE)


def write_backlog(tmp_path: Path) -> Path:
    path = tmp_path / "AGENT_BACKLOG.md"
    path.write_text(BACKLOG_CONTENT, encoding="utf-8")
    return path


def make_workflow() -> StoredWorkflow:
    return StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.DELEGATE,
    )


def test_delegate_launches_once_records_run_and_advances(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workflow = make_workflow()
    launcher = StubLauncher()

    result = run(
        workflow,
        backlog_path=write_backlog(tmp_path),
        launcher=launcher,
        clock=lambda: datetime(2026, 8, 2, 15, 0, tzinfo=UTC),
    )

    assert len(launcher.calls) == 1
    agent_name, branch, prompt, request_id = launcher.calls[0]
    assert agent_name == "trading-exec"
    assert branch == workflow.feature_branch
    assert "Tests cannot call live endpoints." in prompt
    assert request_id.startswith("delegation-")
    assert workflow.state is WorkflowState.DELEGATE
    assert workflow.delegation is None
    assert result.state is WorkflowState.WAIT_FOR_AGENT
    assert result.delegation == DelegationRecord(
        run_id="run-123",
        agent_name="trading-exec",
        started_at="2026-08-02T15:00:00+00:00",
        status=DelegationStatus.ACTIVE,
        request_id=request_id,
        updated_at="2026-08-02T15:00:00+00:00",
    )
    output = capsys.readouterr().out
    assert "Run ID: run-123\n" in output
    assert "Status: ACTIVE\n" in output


def test_delegate_rejects_existing_run_without_launching(tmp_path: Path) -> None:
    launcher = StubLauncher()
    workflow = StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.DELEGATE,
        delegation=DelegationRecord(
            run_id="existing-run",
            agent_name="trading-exec",
            started_at="2026-08-02T15:00:00+00:00",
            status=DelegationStatus.ACTIVE,
        ),
    )

    with pytest.raises(RuntimeError, match="Refusing duplicate delegation"):
        run(workflow, backlog_path=write_backlog(tmp_path), launcher=launcher)

    assert launcher.calls == []


def test_delegate_retries_use_the_same_idempotent_request_id(tmp_path: Path) -> None:
    first_launcher = StubLauncher()
    second_launcher = StubLauncher()
    workflow = make_workflow()

    run(workflow, backlog_path=write_backlog(tmp_path), launcher=first_launcher)
    run(workflow, backlog_path=write_backlog(tmp_path), launcher=second_launcher)

    assert first_launcher.calls[0][3] == second_launcher.calls[0][3]


def test_delegate_rejects_unknown_task(tmp_path: Path) -> None:
    workflow = StoredWorkflow(
        task_id="TEST-999",
        feature_branch="agent/test-999",
        state=WorkflowState.DELEGATE,
    )

    with pytest.raises(RuntimeError, match="unknown backlog task: TEST-999"):
        run(workflow, backlog_path=write_backlog(tmp_path), launcher=StubLauncher())


def test_delegate_rejects_wrong_workflow_state(tmp_path: Path) -> None:
    workflow = StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.PLAN,
    )

    with pytest.raises(RuntimeError, match="received workflow state: PLAN"):
        run(workflow, backlog_path=write_backlog(tmp_path), launcher=StubLauncher())
