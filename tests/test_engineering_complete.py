from __future__ import annotations

from dataclasses import replace

import pytest

from engineering.models import CriterionStatus, ReviewRecommendation, WorkflowState
from engineering.reviewer import CriterionEvidence
from engineering.workflow.complete import run
from engineering.workflow_store import ReportRecord, StoredWorkflow


def completed_workflow() -> StoredWorkflow:
    criterion = CriterionEvidence(
        "Criterion one", "pytest -q", "1 passed", CriterionStatus.PASS
    )
    return StoredWorkflow(
        "OPS-010",
        "agent/ops-010-complete-workflow",
        WorkflowState.COMPLETE,
        report=ReportRecord(
            "OPS-010", "Complete workflows", "agent/ops-010-complete-workflow",
            "trading-manager", 10.0, ("engineering/manager.py",),
            ("python", "-m", "pytest"), 0, 1, 0, (criterion,), (),
            ReviewRecommendation.ACCEPT, "Request approval",
            "2026-08-02T17:45:00+00:00", "Task: OPS-010\n",
        ),
    )


def test_complete_validates_and_prints_persisted_report(
    capsys: pytest.CaptureFixture[str],
    tmp_path: pytest.Path,
) -> None:
    workflow = completed_workflow()

    assert run(workflow, repository_root=tmp_path) is workflow
    assert capsys.readouterr().out == (
        "Executing workflow state: COMPLETE\n"
        "Task: OPS-010\n"
        "Workflow complete; active state will be archived and cleared.\n"
    )


@pytest.mark.parametrize(
    "workflow, message",
    (
        (StoredWorkflow("OPS-010", "agent/test", WorkflowState.REPORT), "received workflow state"),
        (StoredWorkflow("OPS-010", "agent/test", WorkflowState.COMPLETE), "persisted report"),
        (
            replace(completed_workflow(), task_id="OTHER"),
            "report task does not match",
        ),
        (
            replace(completed_workflow(), feature_branch="agent/other"),
            "report branch does not match",
        ),
    ),
)
def test_complete_rejects_inconsistent_evidence(
    workflow: StoredWorkflow,
    message: str,
    tmp_path: pytest.Path,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        run(workflow, repository_root=tmp_path)
