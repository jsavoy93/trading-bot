from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Callable

from engineering.executor import AgentMonitor, CommandAgentLauncher, validate_run_identity
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
    if any(
        not value
        for value in (
            workflow.delegation.request_id,
            workflow.delegation.deadline_at,
            workflow.delegation.stdout_path,
            workflow.delegation.stderr_path,
        )
    ):
        raise RuntimeError("WAIT_FOR_AGENT requires complete delegation metadata.")

    print(f"Executing workflow state: {workflow.state.value}")
    if workflow.delegation.status in {
        DelegationStatus.FAILED,
        DelegationStatus.TIMED_OUT,
    }:
        print(f"Run ID: {workflow.delegation.run_id}")
        print(f"Status: {workflow.delegation.status.value}")
        print("Next state: WAIT_FOR_AGENT")
        return workflow

    observed = (monitor or CommandAgentLauncher()).status(
        workflow.delegation.run_id
    )
    validate_run_identity(
        observed,
        request_id=workflow.delegation.request_id,
        run_id=workflow.delegation.run_id,
        agent_name=workflow.delegation.agent_name,
        feature_branch=workflow.feature_branch,
    )
    delegation = replace(
        workflow.delegation,
        status=observed.status,
        started_at=observed.started_at,
        updated_at=observed.updated_at,
        deadline_at=observed.deadline_at,
        stdout_path=observed.stdout_path,
        stderr_path=observed.stderr_path,
        exit_code=observed.exit_code,
        completed_at=observed.completed_at,
        failure_reason=observed.failure_reason,
    )
    next_state = (
        WorkflowState.QA
        if observed.status is DelegationStatus.COMPLETE
        else WorkflowState.WAIT_FOR_AGENT
    )

    print(f"Run ID: {delegation.run_id}")
    print(f"Status: {observed.status.value}")
    print(f"Next state: {next_state.value}")
    return replace(workflow, state=next_state, delegation=delegation)
