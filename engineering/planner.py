from __future__ import annotations

import re

from engineering.models import (
    BacklogTask,
    Complexity,
    ExecutionPlan,
    RepositoryState,
    RiskLevel,
)


PRIORITY_ORDER: dict[str, int] = {
    "P0": 0,
    "P1": 1,
    "P2": 2,
    "P3": 3,
    "P4": 4,
}

HIGH_RISK_TERMS: tuple[str, ...] = (
    "brokerage",
    "credential",
    "destructive",
    "execution",
    "live trading",
    "migration",
    "order path",
    "risk control",
    "secret",
)

MEDIUM_RISK_TERMS: tuple[str, ...] = (
    "branch",
    "dashboard",
    "database",
    "git",
    "settings",
    "strategy",
    "workflow",
)


def select_next_task(
    tasks: tuple[BacklogTask, ...],
) -> BacklogTask | None:
    available_tasks = [
        (index, task)
        for index, task in enumerate(tasks)
        if task.is_available
    ]

    if not available_tasks:
        return None

    _, selected_task = min(
        available_tasks,
        key=lambda item: (
            PRIORITY_ORDER[item[1].priority.value],
            item[0],
        ),
    )

    return selected_task


def build_feature_branch(task: BacklogTask) -> str:
    title_slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        task.title.lower(),
    ).strip("-")

    return f"agent/{task.task_id.lower()}-{title_slug}"


def estimate_risk(task: BacklogTask) -> RiskLevel:
    searchable_text = " ".join(
        (task.title, *task.allowed_areas)
    ).lower()

    if any(term in searchable_text for term in HIGH_RISK_TERMS):
        return RiskLevel.HIGH

    if any(term in searchable_text for term in MEDIUM_RISK_TERMS):
        return RiskLevel.MEDIUM

    return RiskLevel.LOW


def estimate_complexity(task: BacklogTask) -> Complexity:
    criteria_count = len(task.acceptance_criteria)
    area_count = len(task.allowed_areas)

    if criteria_count >= 5 or area_count >= 4:
        return Complexity.LARGE

    if criteria_count >= 3 or area_count >= 2:
        return Complexity.MEDIUM

    return Complexity.SMALL


def build_execution_plan(
    task: BacklogTask,
    repository: RepositoryState,
) -> ExecutionPlan:
    return ExecutionPlan(
        task=task,
        repository=repository,
        feature_branch=build_feature_branch(task),
        acceptance_criteria=task.acceptance_criteria,
        allowed_areas=task.allowed_areas,
        risk=estimate_risk(task),
        complexity=estimate_complexity(task),
    )
