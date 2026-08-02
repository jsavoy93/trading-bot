from __future__ import annotations

from hashlib import sha256
import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from engineering.models import (
    CriterionStatus,
    DelegationStatus,
    ReviewRecommendation,
    WorkflowState,
)
from engineering.reviewer import CriterionEvidence


@dataclass(frozen=True)
class DelegationRecord:
    run_id: str
    agent_name: str
    started_at: str
    status: DelegationStatus
    request_id: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class QARecord:
    command: tuple[str, ...]
    exit_code: int
    duration_seconds: float
    output_summary: str
    changed_files: tuple[str, ...]
    completed_at: str
    timed_out: bool = False
    passed_count: int | None = None
    failed_count: int | None = None


@dataclass(frozen=True)
class ReviewRecord:
    criteria: tuple[CriterionEvidence, ...]
    recommendation: ReviewRecommendation
    completed_at: str


@dataclass(frozen=True)
class ReportRecord:
    task_id: str
    task_title: str
    branch: str
    agent_name: str
    elapsed_seconds: float
    changed_files: tuple[str, ...]
    test_command: tuple[str, ...]
    test_exit_code: int
    passed_count: int | None
    failed_count: int | None
    criteria: tuple[CriterionEvidence, ...]
    risks: tuple[str, ...]
    recommendation: ReviewRecommendation
    next_action: str
    generated_at: str
    rendered: str


@dataclass(frozen=True)
class StoredWorkflow:
    task_id: str
    feature_branch: str
    state: WorkflowState
    delegation: DelegationRecord | None = None
    qa: QARecord | None = None
    review: ReviewRecord | None = None
    report: ReportRecord | None = None


class WorkflowStore:
    def __init__(self, state_path: Path):
        self.state_path = state_path

    def exists(self) -> bool:
        return self.state_path.is_file()

    def load(self) -> StoredWorkflow:
        if not self.exists():
            raise FileNotFoundError(
                f"Workflow state file does not exist: {self.state_path}"
            )

        data = json.loads(self.state_path.read_text(encoding="utf-8"))

        try:
            delegation_data = data.get("delegation")
            delegation = None
            if delegation_data is not None:
                delegation = DelegationRecord(
                    run_id=delegation_data["run_id"],
                    agent_name=delegation_data["agent_name"],
                    started_at=delegation_data["started_at"],
                    status=DelegationStatus(delegation_data["status"]),
                    request_id=delegation_data.get("request_id"),
                    updated_at=delegation_data.get("updated_at"),
                )

            qa_data = data.get("qa")
            qa = None
            if qa_data is not None:
                command = qa_data["command"]
                changed_files = qa_data["changed_files"]
                if not isinstance(command, list) or not all(
                    isinstance(value, str) for value in command
                ):
                    raise TypeError("QA command must be a list of strings")
                if not isinstance(changed_files, list) or not all(
                    isinstance(value, str) for value in changed_files
                ):
                    raise TypeError("QA changed files must be a list of strings")
                if not isinstance(qa_data["exit_code"], int):
                    raise TypeError("QA exit code must be an integer")
                if not isinstance(qa_data["duration_seconds"], (int, float)):
                    raise TypeError("QA duration must be numeric")
                if not isinstance(qa_data["output_summary"], str):
                    raise TypeError("QA output summary must be a string")
                if not isinstance(qa_data["completed_at"], str):
                    raise TypeError("QA completion time must be a string")
                if not isinstance(qa_data.get("timed_out", False), bool):
                    raise TypeError("QA timeout flag must be boolean")
                for count_name in ("passed_count", "failed_count"):
                    count = qa_data.get(count_name)
                    if count is not None and not isinstance(count, int):
                        raise TypeError(f"QA {count_name} must be an integer or null")
                qa = QARecord(
                    command=tuple(command),
                    exit_code=qa_data["exit_code"],
                    duration_seconds=qa_data["duration_seconds"],
                    output_summary=qa_data["output_summary"],
                    changed_files=tuple(changed_files),
                    completed_at=qa_data["completed_at"],
                    timed_out=qa_data.get("timed_out", False),
                    passed_count=qa_data.get("passed_count"),
                    failed_count=qa_data.get("failed_count"),
                )

            review_data = data.get("review")
            review = None
            if review_data is not None:
                raw_criteria = review_data["criteria"]
                if not isinstance(raw_criteria, list):
                    raise TypeError("Review criteria must be a list")
                if any(
                    not isinstance(item, dict)
                    or not all(
                        isinstance(item.get(field), str)
                        for field in (
                            "criterion",
                            "proof_method",
                            "exact_result",
                            "status",
                        )
                    )
                    for item in raw_criteria
                ):
                    raise TypeError("Review criterion fields must be strings")
                if not isinstance(review_data["recommendation"], str) or not isinstance(
                    review_data["completed_at"], str
                ):
                    raise TypeError("Review metadata fields must be strings")
                review = ReviewRecord(
                    criteria=tuple(
                        CriterionEvidence(
                            criterion=item["criterion"],
                            proof_method=item["proof_method"],
                            exact_result=item["exact_result"],
                            status=CriterionStatus(item["status"]),
                        )
                        for item in raw_criteria
                    ),
                    recommendation=ReviewRecommendation(
                        review_data["recommendation"]
                    ),
                    completed_at=review_data["completed_at"],
                )

            report_data = data.get("report")
            report = None
            if report_data is not None:
                for field_name in (
                    "changed_files",
                    "test_command",
                    "risks",
                ):
                    values = report_data[field_name]
                    if not isinstance(values, list) or not all(
                        isinstance(value, str) for value in values
                    ):
                        raise TypeError(f"Report {field_name} must be a list of strings")
                if not isinstance(report_data["criteria"], list):
                    raise TypeError("Report criteria must be a list")
                for field_name in (
                    "task_id", "task_title", "branch", "agent_name",
                    "next_action", "generated_at", "rendered", "recommendation",
                ):
                    if not isinstance(report_data[field_name], str):
                        raise TypeError(f"Report {field_name} must be a string")
                if not isinstance(report_data["elapsed_seconds"], (int, float)):
                    raise TypeError("Report elapsed time must be numeric")
                if not isinstance(report_data["test_exit_code"], int):
                    raise TypeError("Report exit code must be an integer")
                report = ReportRecord(
                    task_id=report_data["task_id"],
                    task_title=report_data["task_title"],
                    branch=report_data["branch"],
                    agent_name=report_data["agent_name"],
                    elapsed_seconds=report_data["elapsed_seconds"],
                    changed_files=tuple(report_data["changed_files"]),
                    test_command=tuple(report_data["test_command"]),
                    test_exit_code=report_data["test_exit_code"],
                    passed_count=report_data.get("passed_count"),
                    failed_count=report_data.get("failed_count"),
                    criteria=tuple(
                        CriterionEvidence(
                            item["criterion"],
                            item["proof_method"],
                            item["exact_result"],
                            CriterionStatus(item["status"]),
                        )
                        for item in report_data["criteria"]
                    ),
                    risks=tuple(report_data["risks"]),
                    recommendation=ReviewRecommendation(
                        report_data["recommendation"]
                    ),
                    next_action=report_data["next_action"],
                    generated_at=report_data["generated_at"],
                    rendered=report_data["rendered"],
                )

            return StoredWorkflow(
                task_id=data["task_id"],
                feature_branch=data["feature_branch"],
                state=WorkflowState(data["state"]),
                delegation=delegation,
                qa=qa,
                review=review,
                report=report,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Invalid workflow state file: {self.state_path}"
            ) from exc

    def save(self, workflow: StoredWorkflow) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "task_id": workflow.task_id,
            "feature_branch": workflow.feature_branch,
            "state": workflow.state.value,
        }

        if workflow.delegation is not None:
            payload["delegation"] = {
                "run_id": workflow.delegation.run_id,
                "agent_name": workflow.delegation.agent_name,
                "started_at": workflow.delegation.started_at,
                "status": workflow.delegation.status.value,
            }
            if workflow.delegation.request_id is not None:
                payload["delegation"]["request_id"] = workflow.delegation.request_id
            if workflow.delegation.updated_at is not None:
                payload["delegation"]["updated_at"] = workflow.delegation.updated_at

        if workflow.qa is not None:
            payload["qa"] = {
                "command": list(workflow.qa.command),
                "exit_code": workflow.qa.exit_code,
                "duration_seconds": workflow.qa.duration_seconds,
                "output_summary": workflow.qa.output_summary,
                "changed_files": list(workflow.qa.changed_files),
                "completed_at": workflow.qa.completed_at,
                "timed_out": workflow.qa.timed_out,
            }
            if workflow.qa.passed_count is not None:
                payload["qa"]["passed_count"] = workflow.qa.passed_count
            if workflow.qa.failed_count is not None:
                payload["qa"]["failed_count"] = workflow.qa.failed_count

        if workflow.review is not None:
            payload["review"] = {
                "criteria": [
                    {
                        "criterion": item.criterion,
                        "proof_method": item.proof_method,
                        "exact_result": item.exact_result,
                        "status": item.status.value,
                    }
                    for item in workflow.review.criteria
                ],
                "recommendation": workflow.review.recommendation.value,
                "completed_at": workflow.review.completed_at,
            }

        if workflow.report is not None:
            payload["report"] = {
                "task_id": workflow.report.task_id,
                "task_title": workflow.report.task_title,
                "branch": workflow.report.branch,
                "agent_name": workflow.report.agent_name,
                "elapsed_seconds": workflow.report.elapsed_seconds,
                "changed_files": list(workflow.report.changed_files),
                "test_command": list(workflow.report.test_command),
                "test_exit_code": workflow.report.test_exit_code,
                "passed_count": workflow.report.passed_count,
                "failed_count": workflow.report.failed_count,
                "criteria": [
                    {
                        "criterion": item.criterion,
                        "proof_method": item.proof_method,
                        "exact_result": item.exact_result,
                        "status": item.status.value,
                    }
                    for item in workflow.report.criteria
                ],
                "risks": list(workflow.report.risks),
                "recommendation": workflow.report.recommendation.value,
                "next_action": workflow.report.next_action,
                "generated_at": workflow.report.generated_at,
                "rendered": workflow.report.rendered,
            }

        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.state_path.parent,
            prefix=f".{self.state_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(payload, temporary_file, indent=2, sort_keys=True)
            temporary_file.write("\n")
            temporary_path = Path(temporary_file.name)

        try:
            os.replace(temporary_path, self.state_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        self.state_path.unlink(missing_ok=True)

    def archive_completed(self, workflow: StoredWorkflow) -> Path:
        if workflow.state is not WorkflowState.COMPLETE or workflow.report is None:
            raise RuntimeError("Only a completed workflow with a report can be archived.")
        if workflow.report.task_id != workflow.task_id:
            raise RuntimeError("Completed report task does not match the workflow.")

        fingerprint = sha256(
            workflow.report.generated_at.encode("utf-8")
        ).hexdigest()[:12]
        archive_path = (
            self.state_path.parent
            / "engineering-reports"
            / f"{workflow.task_id.lower()}-{fingerprint}.json"
        )
        archive_store = WorkflowStore(archive_path)
        if archive_store.exists():
            if archive_store.load() != workflow:
                raise RuntimeError(
                    f"Completed workflow archive already exists with different evidence: {archive_path}"
                )
        else:
            archive_store.save(workflow)
        return archive_path
