from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import time
from typing import Callable

from engineering.models import DelegationStatus, ReviewRecommendation, WorkflowState
from engineering.workflow_engine import dispatch_workflow
from engineering.workflow_store import DriverRecord, StoredWorkflow, WorkflowStore


@dataclass(frozen=True)
class DriverBounds:
    max_steps: int = 8
    max_elapsed_seconds: float = 900.0
    wait_poll_interval_seconds: float = 30.0
    max_wait_polls: int = 20

    def validate(self) -> None:
        values = (
            self.max_steps,
            self.max_elapsed_seconds,
            self.wait_poll_interval_seconds,
            self.max_wait_polls,
        )
        if any(isinstance(value, bool) or value <= 0 for value in values):
            raise ValueError("Driver bounds must be finite positive values.")
        if any(not float(value) < float("inf") for value in values):
            raise ValueError("Driver bounds must be finite positive values.")


@dataclass(frozen=True)
class DriverResult:
    workflow: StoredWorkflow
    steps: int
    wait_polls: int
    elapsed_seconds: float
    stop_reason: str
    blocked: bool
    stale: bool


def _utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise ValueError("Driver UTC clock must return a timezone-aware datetime.")
    return value.astimezone(UTC)


def _is_stale(workflow: StoredWorkflow, now: datetime) -> bool:
    if workflow.driver is None:
        return False
    try:
        updated = datetime.fromisoformat(workflow.driver.updated_at).astimezone(UTC)
    except ValueError as exc:
        raise RuntimeError("Persisted driver update time is invalid.") from exc
    return now - updated > timedelta(hours=48)


def _pre_dispatch_stop(workflow: StoredWorkflow) -> tuple[str, bool] | None:
    if workflow.state is WorkflowState.COMPLETE:
        return "COMPLETE requires explicit one-state completion handling.", False
    if (
        workflow.state is WorkflowState.WAIT_FOR_AGENT
        and workflow.delegation is not None
        and workflow.delegation.status
        in {DelegationStatus.FAILED, DelegationStatus.TIMED_OUT}
    ):
        return f"Delegated run is {workflow.delegation.status.value}; human review required.", True
    if workflow.state is WorkflowState.QA and workflow.qa is not None:
        return "Persisted QA did not pass; human review required.", True
    if (
        workflow.state is WorkflowState.REVIEW
        and workflow.review is not None
        and workflow.review.recommendation is ReviewRecommendation.REWORK
    ):
        return "REVIEW requires rework; human review required.", True
    return None


def drive_workflow(
    store: WorkflowStore,
    bounds: DriverBounds,
    *,
    dispatcher: Callable[[StoredWorkflow], StoredWorkflow] = dispatch_workflow,
    utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> DriverResult:
    bounds.validate()
    started_monotonic = monotonic()
    now = _utc(utc_clock)
    workflow = store.load()
    stale = _is_stale(workflow, now)
    previous = workflow.driver
    driver = DriverRecord(
        started_at=previous.started_at if previous else now.isoformat(),
        updated_at=now.isoformat(),
        accumulated_elapsed_seconds=previous.accumulated_elapsed_seconds if previous else 0.0,
        total_steps=previous.total_steps if previous else 0,
        wait_polls=previous.wait_polls if previous else 0,
        continuity="RESUMED" if previous else "CONTINUOUS",
        last_stop_reason=previous.last_stop_reason if previous else "",
        blocked=stale,
        stale=stale,
        resume_explanation=(
            f"Explicit drive invocation resumed after: {previous.last_stop_reason or 'process stop'}"
            if previous else ""
        ),
    )
    workflow = replace(workflow, driver=driver)
    store.save(workflow)
    steps = 0
    polls = 0

    def finish(reason: str, *, blocked: bool = False, stale_result: bool = False) -> DriverResult:
        nonlocal workflow
        elapsed = max(0.0, monotonic() - started_monotonic)
        current = workflow.driver
        assert current is not None
        workflow = replace(
            workflow,
            driver=replace(
                current,
                updated_at=_utc(utc_clock).isoformat(),
                accumulated_elapsed_seconds=current.accumulated_elapsed_seconds + elapsed,
                last_stop_reason=reason,
                blocked=blocked,
                stale=stale_result,
            ),
        )
        store.save(workflow)
        return DriverResult(workflow, steps, polls, elapsed, reason, blocked, stale_result)

    if stale:
        return finish("Workflow is stale after more than 48 hours; Josh review required.", blocked=True, stale_result=True)

    while True:
        workflow = store.load()
        stopped = _pre_dispatch_stop(workflow)
        if stopped is not None:
            return finish(stopped[0], blocked=stopped[1])
        elapsed = max(0.0, monotonic() - started_monotonic)
        if steps >= bounds.max_steps:
            return finish("Maximum driver steps reached.")
        if elapsed >= bounds.max_elapsed_seconds:
            return finish("Maximum driver elapsed time reached.")
        if workflow.state is WorkflowState.WAIT_FOR_AGENT and polls >= bounds.max_wait_polls:
            return finish("Maximum WAIT_FOR_AGENT polls reached.")

        prior = workflow
        try:
            updated = dispatcher(workflow)
        except Exception as exc:
            workflow = prior
            return finish(f"Workflow handler failed: {type(exc).__name__}: {exc}", blocked=True)
        steps += 1
        if prior.state is WorkflowState.WAIT_FOR_AGENT:
            polls += 1
        current_driver = updated.driver or prior.driver
        assert current_driver is not None
        workflow = replace(
            updated,
            driver=replace(
                current_driver,
                updated_at=_utc(utc_clock).isoformat(),
                total_steps=current_driver.total_steps + 1,
                wait_polls=current_driver.wait_polls + (1 if prior.state is WorkflowState.WAIT_FOR_AGENT else 0),
            ),
        )
        store.save(workflow)

        if prior.state is WorkflowState.REPORT and workflow.state is WorkflowState.COMPLETE:
            return finish("REPORT persisted COMPLETE; explicit completion approval required.")
        stopped = _pre_dispatch_stop(workflow)
        if stopped is not None:
            return finish(stopped[0], blocked=stopped[1])
        if workflow.state is prior.state and workflow.state is not WorkflowState.WAIT_FOR_AGENT:
            return finish(f"Workflow state {workflow.state.value} did not advance.", blocked=True)
        if workflow.state is WorkflowState.WAIT_FOR_AGENT:
            elapsed = max(0.0, monotonic() - started_monotonic)
            if polls >= bounds.max_wait_polls:
                return finish("Maximum WAIT_FOR_AGENT polls reached.")
            remaining = bounds.max_elapsed_seconds - elapsed
            if remaining <= 0:
                return finish("Maximum driver elapsed time reached.")
            sleeper(min(bounds.wait_poll_interval_seconds, remaining))
