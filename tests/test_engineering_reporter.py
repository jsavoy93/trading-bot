from datetime import UTC, datetime

import pytest

from engineering.models import (
    BacklogTask,
    CriterionStatus,
    DelegationStatus,
    Priority,
    ReviewRecommendation,
    TaskStatus,
    WorkflowState,
)
from engineering.reporter import NEXT_ACTION, build_report
from engineering.reviewer import CriterionEvidence
from engineering.workflow_store import (
    DelegationRecord,
    QARecord,
    ReviewRecord,
    StoredWorkflow,
)


CRITERION = "Criterion one"
TASK = BacklogTask(
    "TEST-001", "Example", TaskStatus.IN_PROGRESS, "trading-exec", Priority.P0,
    (CRITERION,), ("engineering/",),
)


def workflow(*, exit_code: int = 0, recommendation: ReviewRecommendation = ReviewRecommendation.ACCEPT) -> StoredWorkflow:
    evidence = CriterionEvidence(CRITERION, "pytest -k one", "1 passed", CriterionStatus.PASS)
    return StoredWorkflow(
        "TEST-001", "agent/test-001", WorkflowState.REPORT,
        delegation=DelegationRecord("run-1", "trading-exec", "2026-08-02T16:00:00+00:00", DelegationStatus.COMPLETE),
        qa=QARecord(("python", "-m", "pytest"), exit_code, 2.0, "1 passed", ("src/a.py",), "2026-08-02T16:01:00+00:00", passed_count=1, failed_count=0),
        review=ReviewRecord((evidence,), recommendation, "2026-08-02T16:02:00+00:00"),
    )


def test_build_report_contains_required_deterministic_fields() -> None:
    report = build_report(workflow(), TASK, datetime(2026, 8, 2, 16, 5, tzinfo=UTC))

    assert report.elapsed_seconds == 300.0
    assert report.changed_files == ("src/a.py",)
    assert report.passed_count == 1
    assert report.recommendation is ReviewRecommendation.ACCEPT
    assert report.next_action == NEXT_ACTION
    for text in ("Task: TEST-001", "Branch: agent/test-001", "Agent: trading-exec", "[PASS] Criterion one", "Risks:"):
        assert text in report.rendered


@pytest.mark.parametrize(
    "stored, message",
    (
        (workflow(exit_code=1), "successful QA"),
        (workflow(recommendation=ReviewRecommendation.REWORK), "ACCEPT review"),
    ),
)
def test_build_report_rejects_incomplete_evidence(stored: StoredWorkflow, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        build_report(stored, TASK, datetime(2026, 8, 2, 16, 5, tzinfo=UTC))


def test_build_report_rejects_mismatched_criteria_and_invalid_time() -> None:
    other_task = BacklogTask("TEST-001", "Example", TaskStatus.IN_PROGRESS, "trading-exec", Priority.P0, ("Other",), ())
    with pytest.raises(RuntimeError, match="do not match"):
        build_report(workflow(), other_task, datetime(2026, 8, 2, 16, 5, tzinfo=UTC))
    with pytest.raises(RuntimeError, match="cannot precede"):
        build_report(workflow(), TASK, datetime(2026, 8, 2, 15, 59, tzinfo=UTC))
