from __future__ import annotations

from datetime import datetime

from engineering.models import BacklogTask, ReviewRecommendation
from engineering.workflow_store import ReportRecord, StoredWorkflow


NEXT_ACTION = "Request Josh's approval; do not merge, push, or deploy automatically."
RISKS = (
    "Criterion proof is authored externally and must remain reproducible.",
    "Human approval is required before merge, push, or deployment.",
)


def build_report(
    workflow: StoredWorkflow,
    task: BacklogTask,
    generated_at: datetime,
) -> ReportRecord:
    if workflow.delegation is None or workflow.qa is None or workflow.review is None:
        raise RuntimeError("REPORT requires delegation, QA, and review evidence.")
    if workflow.qa.exit_code != 0 or workflow.qa.timed_out:
        raise RuntimeError("REPORT requires successful QA evidence.")
    if workflow.review.recommendation is not ReviewRecommendation.ACCEPT:
        raise RuntimeError("REPORT requires an ACCEPT review recommendation.")
    if tuple(item.criterion for item in workflow.review.criteria) != task.acceptance_criteria:
        raise RuntimeError("REPORT review criteria do not match the authoritative task.")

    started_at = datetime.fromisoformat(workflow.delegation.started_at)
    if started_at.tzinfo is None or generated_at.tzinfo is None:
        raise RuntimeError("REPORT timestamps must include time zones.")
    elapsed_seconds = round((generated_at - started_at).total_seconds(), 3)
    if elapsed_seconds < 0:
        raise RuntimeError("REPORT completion cannot precede delegation start.")

    command = " ".join(workflow.qa.command)
    lines = [
        f"Task: {task.task_id} — {task.title}",
        f"Branch: {workflow.feature_branch}",
        f"Agent: {workflow.delegation.agent_name}",
        f"Elapsed seconds: {elapsed_seconds:.3f}",
        f"Tests: {command}",
        f"Test exit code: {workflow.qa.exit_code}",
        f"Passed: {workflow.qa.passed_count}",
        f"Failed: {workflow.qa.failed_count}",
        "Changed files:",
        *(f"- {path}" for path in workflow.qa.changed_files),
        "Acceptance evidence:",
        *(
            f"- [{item.status.value}] {item.criterion} | "
            f"{item.proof_method} | {item.exact_result}"
            for item in workflow.review.criteria
        ),
        "Risks:",
        *(f"- {risk}" for risk in RISKS),
        f"Recommendation: {workflow.review.recommendation.value}",
        f"Next action: {NEXT_ACTION}",
    ]
    return ReportRecord(
        task_id=task.task_id,
        task_title=task.title,
        branch=workflow.feature_branch,
        agent_name=workflow.delegation.agent_name,
        elapsed_seconds=elapsed_seconds,
        changed_files=workflow.qa.changed_files,
        test_command=workflow.qa.command,
        test_exit_code=workflow.qa.exit_code,
        passed_count=workflow.qa.passed_count,
        failed_count=workflow.qa.failed_count,
        criteria=workflow.review.criteria,
        risks=RISKS,
        recommendation=workflow.review.recommendation,
        next_action=NEXT_ACTION,
        generated_at=generated_at.isoformat(),
        rendered="\n".join(lines) + "\n",
    )
