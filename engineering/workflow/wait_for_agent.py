from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Callable

from engineering.executor import AgentMonitor, CommandAgentLauncher
from engineering.models import DelegationStatus, WorkflowState
from engineering.workflow_store import StoredWorkflow


def run(
    workflow: StoredWorkflow,
    *,
    monitor: AgentMonitor | None = None,
    clock: Callable[[], datetime] | None = None,
) -> StoredWorkflow:
    if workflow.state is not WorkflowState.WAIT_FOR_AGENT:
        raise RuntimeError(
            f"WAIT_FOR_AGENT handler received workflow state: {workflow.state.value}"
        )
    if workflow.delegation is None:
        raise RuntimeError("WAIT_FOR_AGENT requires persisted delegation metadata.")

    print(f"Executing workflow state: {workflow.state.value}")
    if workflow.delegation.status in {
        DelegationStatus.FAILED,
        DelegationStatus.TIMED_OUT,
    }:
        print(f"Run ID: {workflow.delegation.run_id}")
        print(f"Status: {workflow.delegation.status.value}")
        print("Next state: WAIT_FOR_AGENT")
        return workflow

    status = (monitor or CommandAgentLauncher()).status(
        workflow.delegation.run_id
    )
    checked_at = (
        (clock or (lambda: datetime.now(UTC)))()
        .astimezone(UTC)
        .isoformat()
    )
    delegation = replace(
        workflow.delegation,
        status=status,
        updated_at=checked_at,
    )
    next_state = (
        WorkflowState.QA
        if status is DelegationStatus.COMPLETE
        else WorkflowState.WAIT_FOR_AGENT
    )

    print(f"Run ID: {delegation.run_id}")
    print(f"Status: {status.value}")
    print(f"Next state: {next_state.value}")
    return replace(workflow, state=next_state, delegation=delegation)
