from __future__ import annotations

from engineering.models import CriterionStatus, ReviewRecommendation, WorkflowState
from engineering.workflow_store import StoredWorkflow


def run(workflow: StoredWorkflow) -> StoredWorkflow:
    if workflow.state is not WorkflowState.COMPLETE:
        raise RuntimeError(
            f"COMPLETE handler received workflow state: {workflow.state.value}"
        )
    if workflow.report is None:
        raise RuntimeError("COMPLETE requires persisted report evidence.")
    if workflow.report.task_id != workflow.task_id:
        raise RuntimeError("COMPLETE report task does not match the workflow.")
    if workflow.report.branch != workflow.feature_branch:
        raise RuntimeError("COMPLETE report branch does not match the workflow.")
    if workflow.report.recommendation is not ReviewRecommendation.ACCEPT:
        raise RuntimeError("COMPLETE requires an ACCEPT report recommendation.")
    if not workflow.report.criteria or any(
        item.status is not CriterionStatus.PASS for item in workflow.report.criteria
    ):
        raise RuntimeError("COMPLETE requires passing criterion evidence.")

    print(f"Executing workflow state: {workflow.state.value}")
    print(workflow.report.rendered, end="")
    print("Workflow complete; active state will be archived and cleared.")
    return workflow
