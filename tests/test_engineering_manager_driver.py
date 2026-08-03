from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engineering.manager_driver import DriverBounds, drive_workflow
from engineering.models import DelegationStatus, ReviewRecommendation, WorkflowState
from engineering.reviewer import CriterionEvidence
from engineering.models import CriterionStatus
from engineering.workflow_store import (
    DelegationRecord,
    DriverRecord,
    QARecord,
    ReviewRecord,
    StoredWorkflow,
    WorkflowStore,
)


class FakeTime:
    def __init__(self) -> None:
        self.utc = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
        self.elapsed = 0.0
        self.sleeps: list[float] = []

    def utc_clock(self) -> datetime:
        return self.utc + timedelta(seconds=self.elapsed)

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds


def store_with(tmp_path: Path, workflow: StoredWorkflow) -> WorkflowStore:
    store = WorkflowStore(tmp_path / "workflow.json")
    store.save(workflow)
    return store


def run_fake(store: WorkflowStore, dispatcher, bounds: DriverBounds = DriverBounds()):
    clock = FakeTime()
    result = drive_workflow(
        store,
        bounds,
        dispatcher=dispatcher,
        utc_clock=clock.utc_clock,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )
    return result, clock


def test_advances_reloading_and_persisting_each_step_then_stops_after_report(tmp_path: Path) -> None:
    store = store_with(tmp_path, StoredWorkflow("OPS-013", "agent/ops-013", WorkflowState.PLAN))
    seen: list[WorkflowState] = []
    transitions = {
        WorkflowState.PLAN: WorkflowState.PREPARE_BRANCH,
        WorkflowState.PREPARE_BRANCH: WorkflowState.DELEGATE,
        WorkflowState.DELEGATE: WorkflowState.QA,
        WorkflowState.QA: WorkflowState.REVIEW,
        WorkflowState.REVIEW: WorkflowState.REPORT,
        WorkflowState.REPORT: WorkflowState.COMPLETE,
    }

    def dispatch(workflow: StoredWorkflow) -> StoredWorkflow:
        assert store.load() == workflow
        seen.append(workflow.state)
        return replace(workflow, state=transitions[workflow.state])

    result, _ = run_fake(store, dispatch)

    assert seen == list(transitions)
    assert result.workflow.state is WorkflowState.COMPLETE
    assert result.stop_reason.startswith("REPORT persisted COMPLETE")
    assert store.exists()


@pytest.mark.parametrize("status", (DelegationStatus.FAILED, DelegationStatus.TIMED_OUT))
def test_terminal_delegation_stops_without_dispatch(tmp_path: Path, status: DelegationStatus) -> None:
    delegation = DelegationRecord("run", "trading-exec", "2026-08-03T11:00:00+00:00", status)
    store = store_with(tmp_path, StoredWorkflow("OPS-013", "agent/ops", WorkflowState.WAIT_FOR_AGENT, delegation))

    result, _ = run_fake(store, lambda workflow: pytest.fail("must not dispatch"))

    assert result.steps == 0
    assert result.blocked
    assert status.value in result.stop_reason


def test_wait_polling_is_finite_and_uses_injected_sleeper(tmp_path: Path) -> None:
    delegation = DelegationRecord("run", "trading-exec", "2026-08-03T11:00:00+00:00", DelegationStatus.ACTIVE)
    store = store_with(tmp_path, StoredWorkflow("OPS-013", "agent/ops", WorkflowState.WAIT_FOR_AGENT, delegation))

    result, clock = run_fake(
        store,
        lambda workflow: workflow,
        DriverBounds(max_steps=8, max_elapsed_seconds=100, wait_poll_interval_seconds=7, max_wait_polls=3),
    )

    assert result.wait_polls == 3
    assert result.stop_reason == "Maximum WAIT_FOR_AGENT polls reached."
    assert clock.sleeps == [7, 7]


def test_elapsed_bound_caps_last_sleep(tmp_path: Path) -> None:
    delegation = DelegationRecord("run", "trading-exec", "2026-08-03T11:00:00+00:00", DelegationStatus.PENDING)
    store = store_with(tmp_path, StoredWorkflow("OPS-013", "agent/ops", WorkflowState.WAIT_FOR_AGENT, delegation))

    result, clock = run_fake(
        store,
        lambda workflow: workflow,
        DriverBounds(max_steps=8, max_elapsed_seconds=5, wait_poll_interval_seconds=30, max_wait_polls=8),
    )

    assert clock.sleeps == [5]
    assert result.stop_reason == "Maximum driver elapsed time reached."


def test_step_bound_prevents_endless_advancement(tmp_path: Path) -> None:
    store = store_with(tmp_path, StoredWorkflow("OPS-013", "agent/ops", WorkflowState.PLAN))
    states = iter((WorkflowState.PREPARE_BRANCH, WorkflowState.DELEGATE))
    result, _ = run_fake(store, lambda workflow: replace(workflow, state=next(states)), DriverBounds(max_steps=2))
    assert result.steps == 2
    assert result.stop_reason == "Maximum driver steps reached."


def test_qa_failure_and_review_rework_stop_without_rerun(tmp_path: Path) -> None:
    qa = QARecord(("python", "-m", "pytest"), 1, 1.0, "failed", (), "2026-08-03T11:00:00+00:00")
    qa_store = store_with(tmp_path, StoredWorkflow("OPS-013", "agent/ops", WorkflowState.QA, qa=qa))
    qa_result, _ = run_fake(qa_store, lambda workflow: pytest.fail("must not dispatch"))
    assert qa_result.blocked and qa_result.steps == 0

    evidence = CriterionEvidence("criterion", "proof", "failed", CriterionStatus.FAIL)
    review = ReviewRecord((evidence,), ReviewRecommendation.REWORK, "2026-08-03T11:00:00+00:00")
    review_store = store_with(tmp_path / "review", StoredWorkflow("OPS-013", "agent/ops", WorkflowState.REVIEW, review=review))
    review_result, _ = run_fake(review_store, lambda workflow: pytest.fail("must not dispatch"))
    assert review_result.blocked and review_result.steps == 0


def test_complete_at_start_requires_one_state_handling(tmp_path: Path) -> None:
    store = store_with(tmp_path, StoredWorkflow("OPS-013", "agent/ops", WorkflowState.COMPLETE))
    result, _ = run_fake(store, lambda workflow: pytest.fail("must not dispatch"))
    assert result.steps == 0
    assert result.stop_reason.startswith("COMPLETE requires explicit")


def test_restart_records_continuity_and_stale_workflow_stops(tmp_path: Path) -> None:
    old = DriverRecord(
        started_at="2026-08-01T00:00:00+00:00",
        updated_at="2026-08-01T11:59:59+00:00",
        last_stop_reason="Maximum driver steps reached.",
    )
    store = store_with(tmp_path, StoredWorkflow("OPS-013", "agent/ops", WorkflowState.PLAN, driver=old))
    result, _ = run_fake(store, lambda workflow: pytest.fail("must not dispatch"))
    assert result.stale and result.blocked
    assert result.workflow.driver.continuity == "RESUMED"
    assert "Maximum driver steps" in result.workflow.driver.resume_explanation


def test_handler_exception_is_persisted_as_blocked_stop(tmp_path: Path) -> None:
    store = store_with(tmp_path, StoredWorkflow("OPS-013", "agent/ops", WorkflowState.PLAN))
    result, _ = run_fake(store, lambda workflow: (_ for _ in ()).throw(RuntimeError("boom")))
    assert result.blocked
    assert "RuntimeError: boom" in store.load().driver.last_stop_reason


@pytest.mark.parametrize(
    "bounds",
    (
        DriverBounds(max_steps=0),
        DriverBounds(max_elapsed_seconds=float("inf")),
        DriverBounds(wait_poll_interval_seconds=-1),
        DriverBounds(max_wait_polls=0),
    ),
)
def test_invalid_bounds_fail_before_dispatch(tmp_path: Path, bounds: DriverBounds) -> None:
    store = store_with(tmp_path, StoredWorkflow("OPS-013", "agent/ops", WorkflowState.PLAN))
    with pytest.raises(ValueError, match="finite positive"):
        drive_workflow(store, bounds, dispatcher=lambda workflow: pytest.fail("must not dispatch"))


def test_no_external_service_boundaries_are_needed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("real Codex, brokerage, subprocess, or network boundary used")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    store = store_with(tmp_path, StoredWorkflow("OPS-013", "agent/ops", WorkflowState.PLAN))
    result, _ = run_fake(store, lambda workflow: replace(workflow, state=WorkflowState.PREPARE_BRANCH), DriverBounds(max_steps=1))
    assert result.steps == 1
