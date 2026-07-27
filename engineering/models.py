from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class TaskStatus(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    REVIEW = "REVIEW"
    DONE = "DONE"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


@dataclass(frozen=True)
class BacklogTask:
    task_id: str
    title: str
    status: TaskStatus
    owner: str
    priority: Priority
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    allowed_areas: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_available(self) -> bool:
        return self.status is TaskStatus.TODO


@dataclass(frozen=True)
class RepositoryState:
    root: Path
    branch: str
    is_clean: bool


@dataclass(frozen=True)
class ExecutionPlan:
    task: BacklogTask
    repository: RepositoryState
    feature_branch: str
