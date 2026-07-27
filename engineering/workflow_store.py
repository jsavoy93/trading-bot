from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from engineering.models import WorkflowState


@dataclass(frozen=True)
class StoredWorkflow:
    task_id: str
    feature_branch: str
    state: WorkflowState


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
            return StoredWorkflow(
                task_id=data["task_id"],
                feature_branch=data["feature_branch"],
                state=WorkflowState(data["state"]),
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
