from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from engineering.models import DelegationStatus, WorkflowState


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
class StoredWorkflow:
    task_id: str
    feature_branch: str
    state: WorkflowState
    delegation: DelegationRecord | None = None
    qa: QARecord | None = None


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

            return StoredWorkflow(
                task_id=data["task_id"],
                feature_branch=data["feature_branch"],
                state=WorkflowState(data["state"]),
                delegation=delegation,
                qa=qa,
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
