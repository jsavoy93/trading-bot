from __future__ import annotations

from engineering.workflow_store import StoredWorkflow


def run(workflow: StoredWorkflow) -> StoredWorkflow:
    print(f"Executing workflow state: {workflow.state.value}")
    return workflow
