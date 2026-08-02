from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from engineering.git_service import GitService
from engineering.models import WorkflowState
from engineering.workflow_store import StoredWorkflow


def run(
    workflow: StoredWorkflow,
    *,
    repo_root: Path | None = None,
    expected_source_branch: str = "main",
    git_service: GitService | None = None,
) -> StoredWorkflow:
    if workflow.state is not WorkflowState.PREPARE_BRANCH:
        raise RuntimeError(
            "PREPARE_BRANCH handler received workflow state: "
            f"{workflow.state.value}"
        )

    print(f"Executing workflow state: {workflow.state.value}")

    resolved_root = repo_root or Path.cwd()
    git = git_service or GitService(resolved_root)
    repository = git.prepare_feature_branch(
        workflow.feature_branch,
        expected_source_branch,
    )

    print(f"Repository: {repository.root}")
    print(f"Expected source branch: {expected_source_branch}")
    print(f"Feature branch: {repository.branch}")
    print(f"Repository clean: {repository.is_clean}")

    return replace(workflow, state=WorkflowState.DELEGATE)
