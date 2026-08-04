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
    deadline_at: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    exit_code: int | None = None
    completed_at: str | None = None
    failure_reason: str = ""


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
class DriverRecord:
    started_at: str
    updated_at: str
    accumulated_elapsed_seconds: float = 0.0
    total_steps: int = 0
    wait_polls: int = 0
    continuity: str = "CONTINUOUS"
    last_stop_reason: str = ""
    blocked: bool = False
    stale: bool = False
    resume_explanation: str = ""


@dataclass(frozen=True)
class StoredWorkflow:
    task_id: str
    feature_branch: str
    state: WorkflowState
    delegation: DelegationRecord | None = None
    qa: QARecord | None = None
    review: ReviewRecord | None = None
    report: ReportRecord | None = None
    driver: DriverRecord | None = None


class WorkflowStore:
    def __init__(
        self,
        state_path: Path,
        *,
        event_store: object | None = None,
        notification_destinations: tuple[str, ...] = ("telegram",),
    ):
        self.state_path = state_path
        self.event_store = event_store
        self.notification_destinations = notification_destinations

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
                for field_name in (
                    "run_id", "agent_name", "started_at", "status",
                ):
                    if not isinstance(delegation_data.get(field_name), str):
                        raise TypeError(f"Delegation {field_name} must be a string")
                for field_name in (
                    "request_id", "updated_at", "deadline_at", "stdout_path",
                    "stderr_path", "completed_at",
                ):
                    value = delegation_data.get(field_name)
                    if value is not None and not isinstance(value, str):
                        raise TypeError(
                            f"Delegation {field_name} must be a string or null"
                        )
                exit_code = delegation_data.get("exit_code")
                if exit_code is not None and not isinstance(exit_code, int):
                    raise TypeError("Delegation exit_code must be an integer or null")
                failure_reason = delegation_data.get("failure_reason", "")
                if not isinstance(failure_reason, str) or len(failure_reason) > 2000:
                    raise TypeError("Delegation failure_reason must be a string")
                delegation = DelegationRecord(
                    run_id=delegation_data["run_id"],
                    agent_name=delegation_data["agent_name"],
                    started_at=delegation_data["started_at"],
                    status=DelegationStatus(delegation_data["status"]),
                    request_id=delegation_data.get("request_id"),
                    updated_at=delegation_data.get("updated_at"),
                    deadline_at=delegation_data.get("deadline_at"),
                    stdout_path=delegation_data.get("stdout_path"),
                    stderr_path=delegation_data.get("stderr_path"),
                    exit_code=exit_code,
                    completed_at=delegation_data.get("completed_at"),
                    failure_reason=failure_reason,
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

            driver_data = data.get("driver")
            driver = None
            if driver_data is not None:
                for field_name in (
                    "started_at", "updated_at", "continuity",
                    "last_stop_reason", "resume_explanation",
                ):
                    if not isinstance(driver_data.get(field_name), str):
                        raise TypeError(f"Driver {field_name} must be a string")
                if not isinstance(
                    driver_data.get("accumulated_elapsed_seconds"), (int, float)
                ):
                    raise TypeError("Driver elapsed time must be numeric")
                for field_name in ("total_steps", "wait_polls"):
                    if not isinstance(driver_data.get(field_name), int):
                        raise TypeError(f"Driver {field_name} must be an integer")
                for field_name in ("blocked", "stale"):
                    if not isinstance(driver_data.get(field_name), bool):
                        raise TypeError(f"Driver {field_name} must be boolean")
                if driver_data["continuity"] not in {"CONTINUOUS", "RESUMED"}:
                    raise TypeError("Driver continuity is invalid")
                if (
                    driver_data["accumulated_elapsed_seconds"] < 0
                    or driver_data["total_steps"] < 0
                    or driver_data["wait_polls"] < 0
                ):
                    raise TypeError("Driver counters cannot be negative")
                driver = DriverRecord(
                    started_at=driver_data["started_at"],
                    updated_at=driver_data["updated_at"],
                    accumulated_elapsed_seconds=driver_data[
                        "accumulated_elapsed_seconds"
                    ],
                    total_steps=driver_data["total_steps"],
                    wait_polls=driver_data["wait_polls"],
                    continuity=driver_data["continuity"],
                    last_stop_reason=driver_data["last_stop_reason"],
                    blocked=driver_data["blocked"],
                    stale=driver_data["stale"],
                    resume_explanation=driver_data["resume_explanation"],
                )

            return StoredWorkflow(
                task_id=data["task_id"],
                feature_branch=data["feature_branch"],
                state=WorkflowState(data["state"]),
                delegation=delegation,
                qa=qa,
                review=review,
                report=report,
                driver=driver,
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
            if workflow.delegation.deadline_at is not None:
                payload["delegation"]["deadline_at"] = workflow.delegation.deadline_at
            if workflow.delegation.stdout_path is not None:
                payload["delegation"]["stdout_path"] = workflow.delegation.stdout_path
            if workflow.delegation.stderr_path is not None:
                payload["delegation"]["stderr_path"] = workflow.delegation.stderr_path
            if workflow.delegation.exit_code is not None:
                payload["delegation"]["exit_code"] = workflow.delegation.exit_code
            if workflow.delegation.completed_at is not None:
                payload["delegation"]["completed_at"] = workflow.delegation.completed_at
            if workflow.delegation.failure_reason:
                payload["delegation"]["failure_reason"] = workflow.delegation.failure_reason

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

        if workflow.driver is not None:
            payload["driver"] = {
                "started_at": workflow.driver.started_at,
                "updated_at": workflow.driver.updated_at,
                "accumulated_elapsed_seconds": workflow.driver.accumulated_elapsed_seconds,
                "total_steps": workflow.driver.total_steps,
                "wait_polls": workflow.driver.wait_polls,
                "continuity": workflow.driver.continuity,
                "last_stop_reason": workflow.driver.last_stop_reason,
                "blocked": workflow.driver.blocked,
                "stale": workflow.driver.stale,
                "resume_explanation": workflow.driver.resume_explanation,
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
        self.reconcile(workflow)

    def reconcile(self, workflow: StoredWorkflow) -> int:
        if self.event_store is None:
            return 0
        from engineering.event_projection import reconcile_workflow

        return reconcile_workflow(
            workflow,
            self.event_store,  # type: ignore[arg-type]
            notification_destinations=self.notification_destinations,
        )

    def pause_state(self) -> dict[str, object] | None:
        if self.event_store is None:
            return None
        return self.event_store.pause_state()  # type: ignore[attr-defined,no-any-return]

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
        self.reconcile(workflow)
        archive_store = WorkflowStore(archive_path)
        if archive_store.exists():
            if archive_store.load() != workflow:
                raise RuntimeError(
                    f"Completed workflow archive already exists with different evidence: {archive_path}"
                )
        else:
            archive_store.save(workflow)
        return archive_path
