from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from engineering.backlog import load_backlog
from engineering.models import ReviewRecommendation, WorkflowState
from engineering.reviewer import ReviewDecision, review_criteria
from engineering.workflow_store import ReviewRecord, StoredWorkflow


def run(
    workflow: StoredWorkflow,
    *,
    backlog_path: Path | None = None,
    reviewer: Callable[[Path, tuple[str, ...]], ReviewDecision] | None = None,
    clock: Callable[[], datetime] | None = None,
    repository_root: Path,
) -> StoredWorkflow:
    if workflow.state is not WorkflowState.REVIEW:
        raise RuntimeError(
            f"REVIEW handler received workflow state: {workflow.state.value}"
        )
    if (
        workflow.qa is None
        or workflow.qa.exit_code != 0
        or workflow.qa.timed_out
    ):
        raise RuntimeError("REVIEW requires successful persisted QA evidence.")
    if workflow.review is not None:
        return workflow

    print(f"Executing workflow state: {workflow.state.value}")
    tasks = load_backlog(backlog_path or repository_root / "AGENT_BACKLOG.md")
    task = next((item for item in tasks if item.task_id == workflow.task_id), None)
    if task is None:
        raise RuntimeError(f"Cannot review unknown backlog task: {workflow.task_id}")
    decision = (reviewer or review_criteria)(repository_root, task.acceptance_criteria)
    completed_at = (
        (clock or (lambda: datetime.now(UTC)))().astimezone(UTC).isoformat()
    )
    review = ReviewRecord(
        criteria=decision.criteria,
        recommendation=decision.recommendation,
        completed_at=completed_at,
    )
    next_state = (
        WorkflowState.REPORT
        if decision.recommendation is ReviewRecommendation.ACCEPT
        else WorkflowState.REVIEW
    )

    print(f"Criteria reviewed: {len(review.criteria)}")
    print(f"Recommendation: {review.recommendation.value}")
    print(f"Next state: {next_state.value}")
    return replace(workflow, state=next_state, review=review)
