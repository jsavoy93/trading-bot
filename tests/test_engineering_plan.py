from pathlib import Path

import pytest

from engineering.models import RepositoryState, WorkflowState
from engineering.workflow.plan import run
from engineering.workflow_store import StoredWorkflow


BACKLOG_CONTENT = """
### OPS-003 — Implement deterministic PLAN workflow

Status: IN_PROGRESS
Owner: trading-manager
Priority: P0

Acceptance criteria:

- PLAN resolves its task.
- PLAN advances safely.

Allowed areas:

- engineering/workflow/
- tests/
"""


def write_backlog(tmp_path: Path) -> Path:
    backlog_path = tmp_path / "AGENT_BACKLOG.md"
    backlog_path.write_text(BACKLOG_CONTENT, encoding="utf-8")
    return backlog_path


def make_workflow() -> StoredWorkflow:
    return StoredWorkflow(
        task_id="OPS-003",
        feature_branch="agent/ops-003-implement-deterministic-plan-workflow",
        state=WorkflowState.PLAN,
    )


def make_repository(tmp_path: Path) -> RepositoryState:
    return RepositoryState(
        root=tmp_path,
        branch="agent/ops-autonomous-workflow-v1",
        is_clean=True,
    )


def test_plan_presents_plan_and_advances_to_prepare_branch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workflow = make_workflow()

    result = run(
        workflow,
        backlog_path=write_backlog(tmp_path),
        repository=make_repository(tmp_path),
    )

    output = capsys.readouterr().out
    assert workflow.state is WorkflowState.PLAN
    assert result.state is WorkflowState.PREPARE_BRANCH
    assert result.task_id == workflow.task_id
    assert result.feature_branch == workflow.feature_branch
    assert "Task: OPS-003 — Implement deterministic PLAN workflow" in output
    assert "Risk: MEDIUM" in output
    assert "Complexity: MEDIUM" in output
    assert "- PLAN resolves its task." in output
    assert "- engineering/workflow/" in output


def test_plan_rejects_unknown_task(tmp_path: Path) -> None:
    workflow = make_workflow()
    unknown_workflow = replace_task_id(workflow, "OPS-999")

    with pytest.raises(RuntimeError, match="unknown backlog task: OPS-999"):
        run(
            unknown_workflow,
            backlog_path=write_backlog(tmp_path),
            repository=make_repository(tmp_path),
        )


def test_plan_rejects_inconsistent_feature_branch(tmp_path: Path) -> None:
    workflow = StoredWorkflow(
        task_id="OPS-003",
        feature_branch="agent/wrong-branch",
        state=WorkflowState.PLAN,
    )

    with pytest.raises(RuntimeError, match="does not match the backlog task"):
        run(
            workflow,
            backlog_path=write_backlog(tmp_path),
            repository=make_repository(tmp_path),
        )


def test_plan_rejects_wrong_workflow_state(tmp_path: Path) -> None:
    workflow = StoredWorkflow(
        task_id="OPS-003",
        feature_branch="agent/ops-003-implement-deterministic-plan-workflow",
        state=WorkflowState.DISCOVER,
    )

    with pytest.raises(RuntimeError, match="received workflow state: DISCOVER"):
        run(
            workflow,
            backlog_path=write_backlog(tmp_path),
            repository=make_repository(tmp_path),
        )


def replace_task_id(workflow: StoredWorkflow, task_id: str) -> StoredWorkflow:
    return StoredWorkflow(
        task_id=task_id,
        feature_branch=workflow.feature_branch,
        state=workflow.state,
    )
