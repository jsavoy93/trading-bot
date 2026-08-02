from __future__ import annotations

from dataclasses import replace

from engineering.models import WorkflowState
from engineering.workflow_store import StoredWorkflow


def run(workflow: StoredWorkflow) -> StoredWorkflow:
    print(f"Executing workflow state: {workflow.state.value}")

    return replace(
        workflow,
        state=WorkflowState.PLAN,
    )
