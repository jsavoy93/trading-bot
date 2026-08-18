from datetime import UTC, datetime
from pathlib import Path

import pytest

from engineering.models import (
    CriterionStatus,
    ReviewRecommendation,
    WorkflowState,
)
from engineering.reviewer import CriterionEvidence, ReviewDecision
from engineering.workflow.review import run
from engineering.workflow_store import QARecord, ReviewRecord, StoredWorkflow


CRITERION = "Evidence covers the criterion."


def write_backlog(tmp_path: Path) -> Path:
    path = tmp_path / "AGENT_BACKLOG.md"
    path.write_text(
        f"""# Backlog

### TEST-001 — Example

Status: IN_PROGRESS
Owner: trading-exec
Priority: P0

Acceptance criteria:

- {CRITERION}

Allowed areas:

- engineering/
""",
        encoding="utf-8",
    )
    return path


def qa_record(exit_code: int = 0, timed_out: bool = False) -> QARecord:
    return QARecord(
        command=("python", "-m", "pytest"),
        exit_code=exit_code,
        duration_seconds=1.0,
        output_summary="1 passed",
        changed_files=(),
        completed_at="2026-08-02T16:20:00+00:00",
        timed_out=timed_out,
    )


def decision(status: CriterionStatus) -> ReviewDecision:
    evidence = CriterionEvidence(CRITERION, "pytest -k example", "1 passed", status)
    recommendation = (
        ReviewRecommendation.ACCEPT
        if status is CriterionStatus.PASS
        else ReviewRecommendation.REWORK
    )
    return ReviewDecision((evidence,), recommendation)


def workflow(*, qa: QARecord | None = None, review: ReviewRecord | None = None) -> StoredWorkflow:
    return StoredWorkflow(
        "TEST-001",
        "agent/test-001",
        WorkflowState.REVIEW,
        qa=qa,
        review=review,
    )


def test_accept_records_criteria_and_advances_to_report(tmp_path: Path) -> None:
    original = workflow(qa=qa_record())

    result = run(
        original,
        repository_root=tmp_path,
        backlog_path=write_backlog(tmp_path),
        reviewer=lambda root, criteria: decision(CriterionStatus.PASS),
        clock=lambda: datetime(2026, 8, 2, 16, 25, tzinfo=UTC),
    )

    assert original.review is None
    assert result.state is WorkflowState.REPORT
    assert result.review is not None
    assert result.review.recommendation is ReviewRecommendation.ACCEPT
    assert result.review.completed_at == "2026-08-02T16:25:00+00:00"


def test_failure_records_rework_and_stays_in_review(tmp_path: Path) -> None:
    result = run(
        workflow(qa=qa_record()),
        repository_root=tmp_path,
        backlog_path=write_backlog(tmp_path),
        reviewer=lambda root, criteria: decision(CriterionStatus.FAIL),
    )

    assert result.state is WorkflowState.REVIEW
    assert result.review is not None
    assert result.review.recommendation is ReviewRecommendation.REWORK


def test_existing_review_stops_without_regeneration(tmp_path: Path) -> None:
    existing = ReviewRecord(
        decision(CriterionStatus.FAIL).criteria,
        ReviewRecommendation.REWORK,
        "2026-08-02T16:25:00+00:00",
    )
    calls: list[Path] = []
    original = workflow(qa=qa_record(), review=existing)

    result = run(original, repository_root=tmp_path, reviewer=lambda root, criteria: calls.append(root))

    assert result is original
    assert calls == []


@pytest.mark.parametrize("qa", (None, qa_record(1), qa_record(124, True)))
def test_review_requires_successful_qa(qa: QARecord | None, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="requires successful persisted QA"):
        run(workflow(qa=qa), repository_root=tmp_path, reviewer=lambda root, criteria: decision(CriterionStatus.PASS))


def test_review_rejects_unknown_task(tmp_path: Path) -> None:
    backlog_path = write_backlog(tmp_path)
    backlog_path.write_text(
        backlog_path.read_text(encoding="utf-8").replace("TEST-001", "TEST-999"),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="unknown backlog task"):
        run(
            workflow(qa=qa_record()),
            repository_root=tmp_path,
            backlog_path=backlog_path,
        )


def test_review_rejects_wrong_state(tmp_path: Path) -> None:
    original = workflow(qa=qa_record())
    wrong = StoredWorkflow(original.task_id, original.feature_branch, WorkflowState.REPORT, qa=original.qa)
    with pytest.raises(RuntimeError, match="received workflow state: REPORT"):
        run(wrong, repository_root=tmp_path)
