from pathlib import Path

import pytest

from engineering.models import RepositoryState, WorkflowState
from engineering.workflow.prepare_branch import run
from engineering.workflow_store import StoredWorkflow


class StubGitService:
    def __init__(self, state: RepositoryState):
        self.state = state
        self.calls: list[tuple[str, str]] = []

    def prepare_feature_branch(
        self,
        branch: str,
        expected_source_branch: str,
    ) -> RepositoryState:
        self.calls.append((branch, expected_source_branch))
        return self.state


def make_workflow(state: WorkflowState = WorkflowState.PREPARE_BRANCH) -> StoredWorkflow:
    return StoredWorkflow(
        task_id="OPS-004",
        feature_branch="agent/ops-004-prepare-branch-workflow",
        state=state,
    )


def test_prepare_branch_prepares_repository_and_advances_to_delegate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workflow = make_workflow()
    git = StubGitService(
        RepositoryState(
            root=tmp_path,
            branch=workflow.feature_branch,
            is_clean=True,
        )
    )

    result = run(
        workflow,
        repo_root=tmp_path,
        expected_source_branch="main",
        git_service=git,  # type: ignore[arg-type]
    )

    assert git.calls == [(workflow.feature_branch, "main")]
    assert workflow.state is WorkflowState.PREPARE_BRANCH
    assert result.state is WorkflowState.DELEGATE
    assert result.task_id == workflow.task_id
    assert result.feature_branch == workflow.feature_branch
    output = capsys.readouterr().out
    assert output.startswith("Executing workflow state: PREPARE_BRANCH\n")
    assert "Expected source branch: main\n" in output
    assert f"Feature branch: {workflow.feature_branch}\n" in output
    assert "Repository clean: True\n" in output


def test_prepare_branch_rejects_wrong_workflow_state(tmp_path: Path) -> None:
    workflow = make_workflow(WorkflowState.PLAN)

    with pytest.raises(RuntimeError, match="received workflow state: PLAN"):
        run(workflow, repo_root=tmp_path)
