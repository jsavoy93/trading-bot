from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from engineering.backlog import load_backlog
from engineering.event_projection import timeline_projection
from engineering.event_store import EngineeringEventStore
from engineering.planner import select_next_task
from engineering.workflow_store import StoredWorkflow, WorkflowStore

if TYPE_CHECKING:
    from engineering.adapters import EventAdapter, WorkflowAdapter
else:
    # Import at runtime for isinstance checks; TYPE_CHECKING-only would cause NameError
    from engineering.adapters import EventAdapter, WorkflowAdapter  # noqa: F401


MAX_BACKLOG_TASKS = 500
MAX_CRITERIA = 100


class EngineeringQueryService:
    def __init__(
        self,
        *,
        event_source: EngineeringEventStore | "EventAdapter" | None = None,
        workflow_source: WorkflowStore | "WorkflowAdapter" | None = None,
        backlog_path: Path | None = None,
        # Backward-compat aliases for existing concrete-store callers.
        event_store: EngineeringEventStore | None = None,
        workflow_store: WorkflowStore | None = None,
    ) -> None:
        if event_source is None and event_store is not None:
            event_source = event_store
        if workflow_source is None and workflow_store is not None:
            workflow_source = workflow_store
        if event_source is None:
            raise TypeError("event_source is required")
        if workflow_source is None:
            raise TypeError("workflow_source is required")
        if backlog_path is None:
            raise TypeError("backlog_path is required")

        self.event_store = event_source
        self._workflow_store: WorkflowStore = (
            workflow_source.workflow_store()
            if isinstance(workflow_source, WorkflowAdapter)
            else workflow_source
        )
        self.backlog_path = backlog_path

    def _workflow(self) -> StoredWorkflow | None:
        return self._workflow_store.load() if self._workflow_store.exists() else None

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
            "timeline": self._timeline(timeline_limit),
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

    def _timeline(self, timeline_limit: int) -> list[dict[str, object]]:
        timeline = timeline_projection(
            self.event_store.list_events(limit=timeline_limit), limit=timeline_limit
        )
        timeline.sort(
            key=lambda event: (
                _safe_occurred_at(event.get("occurred_at")),
                -_safe_sequence(event.get("sequence")),
                str(event.get("event_id") or ""),
            )
        )
        return timeline


def _safe_occurred_at(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return ""


def _safe_sequence(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
