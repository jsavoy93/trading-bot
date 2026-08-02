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


class WorkflowState(str, Enum):
    DISCOVER = "DISCOVER"
    PLAN = "PLAN"
    PREPARE_BRANCH = "PREPARE_BRANCH"
    DELEGATE = "DELEGATE"
    WAIT_FOR_AGENT = "WAIT_FOR_AGENT"
    QA = "QA"
    REVIEW = "REVIEW"
    REPORT = "REPORT"
    COMPLETE = "COMPLETE"


class DelegationStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Complexity(str, Enum):
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"


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
    acceptance_criteria: tuple[str, ...]
    allowed_areas: tuple[str, ...]
    risk: RiskLevel
    complexity: Complexity
    workflow_state: WorkflowState = WorkflowState.DISCOVER
