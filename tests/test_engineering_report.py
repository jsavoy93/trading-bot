from datetime import UTC, datetime
from pathlib import Path

import pytest

from engineering.models import WorkflowState
from engineering.workflow.report import run
from engineering.workflow_store import ReportRecord, StoredWorkflow
from tests.test_engineering_reporter import TASK, workflow


def write_backlog(tmp_path: Path, task_id: str = "TEST-001") -> Path:
    path = tmp_path / "AGENT_BACKLOG.md"
    path.write_text(f"""# Backlog

### {task_id} — Example

Status: IN_PROGRESS
Owner: trading-exec
Priority: P0

Acceptance criteria:

- Criterion one

Allowed areas:

- engineering/
""", encoding="utf-8")
    return path


def test_report_persists_output_and_advances_to_complete(tmp_path: Path) -> None:
    original = workflow()
    expected = ReportRecord(
        TASK.task_id, TASK.title, original.feature_branch, "trading-exec", 300.0,
        ("src/a.py",), ("python", "-m", "pytest"), 0, 1, 0,
        original.review.criteria, ("risk",), original.review.recommendation,
        "next", "2026-08-02T16:05:00+00:00", "rendered\n",
    )

    result = run(
        original,
        backlog_path=write_backlog(tmp_path),
        reporter=lambda stored, task, now: expected,
        clock=lambda: datetime(2026, 8, 2, 16, 5, tzinfo=UTC),
    )

    assert original.report is None
    assert result.state is WorkflowState.COMPLETE
    assert result.report == expected


def test_existing_report_is_not_regenerated() -> None:
    stored = workflow()
    existing = ReportRecord("T", "t", "b", "a", 1, (), (), 0, 0, 0, (), (), stored.review.recommendation, "n", "now", "r")
    stored = StoredWorkflow(stored.task_id, stored.feature_branch, WorkflowState.REPORT, stored.delegation, stored.qa, stored.review, existing)
    calls: list[object] = []
    assert run(stored, reporter=lambda *args: calls.append(args)) is stored
    assert calls == []


def test_report_rejects_unknown_task_and_wrong_state(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="unknown backlog task"):
        run(workflow(), backlog_path=write_backlog(tmp_path, "TEST-999"))
    wrong = StoredWorkflow("TEST-001", "agent/test", WorkflowState.COMPLETE)
    with pytest.raises(RuntimeError, match="received workflow state: COMPLETE"):
        run(wrong)
