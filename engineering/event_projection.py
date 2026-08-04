from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from typing import TYPE_CHECKING

from engineering.engineering_events import (
    EngineeringEvent,
    EventSeverity,
    EventType,
    NOTIFICATION_EVENT_TYPES,
    build_event,
)
from engineering.event_store import EngineeringEventStore, StoredEvent
from engineering.models import DelegationStatus, ReviewRecommendation, WorkflowState

if TYPE_CHECKING:
    from engineering.workflow_store import StoredWorkflow


DEFAULT_NOTIFICATION_DESTINATIONS = ("telegram",)


def _workflow_identity(workflow: StoredWorkflow) -> str:
    payload = asdict(workflow)
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _event_time(workflow: StoredWorkflow) -> str:
    candidates = (
        workflow.report.generated_at if workflow.report else None,
        workflow.review.completed_at if workflow.review else None,
        workflow.qa.completed_at if workflow.qa else None,
        workflow.delegation.updated_at if workflow.delegation else None,
        workflow.delegation.started_at if workflow.delegation else None,
        workflow.driver.updated_at if workflow.driver else None,
        workflow.driver.started_at if workflow.driver else None,
    )
    return next((value for value in candidates if value), "1970-01-01T00:00:00+00:00")


def workflow_events(workflow: StoredWorkflow) -> tuple[EngineeringEvent, ...]:
    digest = _workflow_identity(workflow)
    workflow_id = f"{workflow.task_id}:{workflow.feature_branch}"
    occurred_at = _event_time(workflow)
    request_id = workflow.delegation.request_id or "" if workflow.delegation else ""
    run_id = workflow.delegation.run_id if workflow.delegation else ""
    common = dict(
        occurred_at=occurred_at,
        task_id=workflow.task_id,
        workflow_id=workflow_id,
        request_id=request_id,
        run_id=run_id,
    )
    events = [
        build_event(
            EventType.WORKFLOW_TRANSITION,
            identity=f"{workflow.state.value}:{digest}",
            payload={
                "state": workflow.state.value,
                "feature_branch": workflow.feature_branch,
            },
            **common,
        )
    ]

    if workflow.delegation is not None:
        delegation = workflow.delegation
        severity = (
            EventSeverity.ERROR
            if delegation.status in {DelegationStatus.FAILED, DelegationStatus.TIMED_OUT}
            else EventSeverity.INFO
        )
        events.append(
            build_event(
                EventType.DELEGATION_STATUS,
                identity=f"{delegation.run_id}:{delegation.status.value}:{delegation.updated_at}",
                severity=severity,
                payload={
                    "state": workflow.state.value,
                    "feature_branch": workflow.feature_branch,
                    "agent_name": delegation.agent_name,
                    "delegation_status": delegation.status.value,
                    "exit_code": delegation.exit_code,
                    "failure_reason": delegation.failure_reason,
                },
                **common,
            )
        )
        if delegation.status in {DelegationStatus.FAILED, DelegationStatus.TIMED_OUT}:
            events.append(
                build_event(
                    EventType.TASK_FAILED,
                    identity=f"delegation:{delegation.run_id}:{delegation.status.value}",
                    severity=EventSeverity.ERROR,
                    payload={
                        "state": workflow.state.value,
                        "feature_branch": workflow.feature_branch,
                        "delegation_status": delegation.status.value,
                        "exit_code": delegation.exit_code,
                        "failure_reason": delegation.failure_reason,
                    },
                    **common,
                )
            )

    if workflow.qa is not None:
        events.append(
            build_event(
                EventType.QA_RESULT,
                identity=f"{workflow.qa.completed_at}:{workflow.qa.exit_code}:{workflow.qa.timed_out}",
                severity=EventSeverity.INFO if workflow.qa.exit_code == 0 else EventSeverity.ERROR,
                payload={
                    "state": workflow.state.value,
                    "exit_code": workflow.qa.exit_code,
                    "passed_count": workflow.qa.passed_count,
                    "failed_count": workflow.qa.failed_count,
                    "timed_out": workflow.qa.timed_out,
                },
                **common,
            )
        )

    if workflow.report is not None:
        report = workflow.report
        events.append(
            build_event(
                EventType.REPORT_GENERATED,
                identity=report.generated_at,
                payload={
                    "state": workflow.state.value,
                    "feature_branch": workflow.feature_branch,
                    "recommendation": report.recommendation.value,
                    "next_action": report.next_action,
                    "report_generated_at": report.generated_at,
                },
                **common,
            )
        )
        if report.recommendation is ReviewRecommendation.ACCEPT:
            events.append(
                build_event(
                    EventType.APPROVAL_REQUIRED,
                    identity=f"report:{report.generated_at}",
                    severity=EventSeverity.WARNING,
                    payload={
                        "state": workflow.state.value,
                        "feature_branch": workflow.feature_branch,
                        "recommendation": report.recommendation.value,
                        "next_action": report.next_action,
                    },
                    **common,
                )
            )

    if workflow.state is WorkflowState.COMPLETE and workflow.report is not None:
        events.append(
            build_event(
                EventType.TASK_COMPLETED,
                identity=f"complete:{workflow.report.generated_at}",
                payload={
                    "state": workflow.state.value,
                    "feature_branch": workflow.feature_branch,
                    "recommendation": workflow.report.recommendation.value,
                    "next_action": workflow.report.next_action,
                },
                **common,
            )
        )

    if workflow.driver is not None:
        driver = workflow.driver
        if driver.blocked:
            events.append(
                build_event(
                    EventType.WORKFLOW_BLOCKED,
                    identity=f"{driver.updated_at}:{driver.last_stop_reason}",
                    severity=EventSeverity.WARNING,
                    payload={
                        "state": workflow.state.value,
                        "stop_reason": driver.last_stop_reason,
                        "continuity": driver.continuity,
                    },
                    **common,
                )
            )
        if driver.stale:
            events.append(
                build_event(
                    EventType.WORKFLOW_STALE,
                    identity=f"{driver.updated_at}:{driver.last_stop_reason}",
                    severity=EventSeverity.WARNING,
                    payload={
                        "state": workflow.state.value,
                        "stop_reason": driver.last_stop_reason,
                        "continuity": driver.continuity,
                    },
                    **common,
                )
            )
    return tuple(events)


def reconcile_workflow(
    workflow: StoredWorkflow,
    event_store: EngineeringEventStore,
    *,
    notification_destinations: tuple[str, ...] = DEFAULT_NOTIFICATION_DESTINATIONS,
) -> int:
    inserted = 0
    for event in workflow_events(workflow):
        destinations = (
            notification_destinations
            if event.event_type in NOTIFICATION_EVENT_TYPES
            else ()
        )
        inserted += int(event_store.append(event, destinations))
    return inserted


def timeline_projection(events: tuple[StoredEvent, ...], *, limit: int = 100) -> list[dict[str, object]]:
    if not 1 <= limit <= 500:
        raise ValueError("Timeline limit must be between 1 and 500")
    return [
        {
            "sequence": item.sequence,
            "event_id": item.event.event_id,
            "type": item.event.event_type.value,
            "severity": item.event.severity.value,
            "occurred_at": item.event.occurred_at,
            "task_id": item.event.task_id,
            "payload": dict(item.event.payload),
        }
        for item in events[-limit:]
    ]
