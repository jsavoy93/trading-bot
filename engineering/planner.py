from __future__ import annotations

import re

from engineering.models import (
    BacklogTask,
    ExecutionPlan,
    RepositoryState,
)


PRIORITY_ORDER: dict[str, int] = {
    "P0": 0,
    "P1": 1,
    "P2": 2,
    "P3": 3,
    "P4": 4,
}


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


def build_execution_plan(
    task: BacklogTask,
    repository: RepositoryState,
) -> ExecutionPlan:
    return ExecutionPlan(
        task=task,
        repository=repository,
        feature_branch=build_feature_branch(task),
    )
