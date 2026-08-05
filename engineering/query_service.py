from __future__ import annotations

from pathlib import Path

from engineering.backlog import load_backlog
from engineering.event_projection import timeline_projection
from engineering.event_store import EngineeringEventStore
from engineering.planner import select_next_task
from engineering.workflow_store import StoredWorkflow, WorkflowStore


MAX_BACKLOG_TASKS = 500
MAX_CRITERIA = 100


class EngineeringQueryService:
    def __init__(
        self,
        *,
        event_store: EngineeringEventStore,
        workflow_store: WorkflowStore,
        backlog_path: Path,
    ):
        self.event_store = event_store
        self.workflow_store = workflow_store
        self.backlog_path = backlog_path

    def _workflow(self) -> StoredWorkflow | None:
        return self.workflow_store.load() if self.workflow_store.exists() else None

    def snapshot(self, *, timeline_limit: int = 100) -> dict[str, object]:
        workflow = self._workflow()
        tasks = load_backlog(self.backlog_path)[:MAX_BACKLOG_TASKS]
        current_task = next(
            (task for task in tasks if workflow and task.task_id == workflow.task_id),
            None,
        )
        next_task = select_next_task(tasks)
        report = workflow.report if workflow else None
        delegation = workflow.delegation if workflow else None
        qa = workflow.qa if workflow else None
        pause = self.event_store.pause_state()
        gaps = []
        if workflow is None:
            gaps.append("No active workflow is recorded.")
        if report is None:
            gaps.append("No active workflow report is recorded.")
        gaps.extend(("No durable PR link is recorded.", "No current engineering goal is recorded."))
        return {
            "current_task": (
                {
                    "id": current_task.task_id,
                    "title": current_task.title,
                    "priority": current_task.priority.value,
                    "owner": current_task.owner,
                    "state": workflow.state.value,
                    "feature_branch": workflow.feature_branch,
                }
                if workflow and current_task
                else None
            ),
            "timeline": timeline_projection(
                self.event_store.list_events(limit=timeline_limit), limit=timeline_limit
            ),
            "agent_run": (
                {
                    "agent_name": delegation.agent_name,
                    "run_id": delegation.run_id,
                    "started_at": delegation.started_at,
                    "status": delegation.status.value,
                    "updated_at": delegation.updated_at,
                    "completed_at": delegation.completed_at,
                    "exit_code": delegation.exit_code,
                    "failure_reason": delegation.failure_reason[:2_000],
                }
                if delegation
                else None
            ),
            "backlog": [
                {
                    "id": task.task_id,
                    "title": task.title,
                    "status": task.status.value,
                    "priority": task.priority.value,
                    "owner": task.owner,
                }
                for task in tasks
            ],
            "acceptance_criteria": list(current_task.acceptance_criteria[:MAX_CRITERIA])
            if current_task
            else [],
            "tests": (
                {
                    "command": list(qa.command),
                    "exit_code": qa.exit_code,
                    "duration_seconds": qa.duration_seconds,
                    "passed_count": qa.passed_count,
                    "failed_count": qa.failed_count,
                    "timed_out": qa.timed_out,
                    "completed_at": qa.completed_at,
                    "output_summary": qa.output_summary[:2_000],
                }
                if qa
                else None
            ),
            "report": (
                {
                    "task_id": report.task_id,
                    "recommendation": report.recommendation.value,
                    "next_action": report.next_action[:2_000],
                    "generated_at": report.generated_at,
                }
                if report
                else None
            ),
            "pr_links": [],
            "current_goals": [],
            "remaining_gaps": gaps,
            "recommended_next_step": (
                report.next_action[:2_000]
                if report
                else (
                    f"Start approved task {next_task.task_id}."
                    if next_task
                    else "No available backlog task."
                )
            ),
            "pause": pause,
        }
