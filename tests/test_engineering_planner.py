from engineering.models import BacklogTask, Priority, TaskStatus
from engineering.planner import select_next_task


def make_task(
    task_id: str,
    priority: Priority,
    status: TaskStatus = TaskStatus.TODO,
) -> BacklogTask:
    return BacklogTask(
        task_id=task_id,
        title=task_id,
        status=status,
        owner="test-agent",
        priority=priority,
    )


def test_selects_highest_priority_available_task() -> None:
    tasks = (
        make_task("TASK-001", Priority.P2),
        make_task("TASK-002", Priority.P0),
        make_task("TASK-003", Priority.P1),
    )

    selected = select_next_task(tasks)

    assert selected is not None
    assert selected.task_id == "TASK-002"


def test_uses_backlog_order_for_equal_priority() -> None:
    tasks = (
        make_task("TASK-001", Priority.P0),
        make_task("TASK-002", Priority.P0),
    )

    selected = select_next_task(tasks)

    assert selected is not None
    assert selected.task_id == "TASK-001"


def test_ignores_unavailable_tasks() -> None:
    tasks = (
        make_task("TASK-001", Priority.P0, TaskStatus.DONE),
        make_task("TASK-002", Priority.P1),
    )

    selected = select_next_task(tasks)

    assert selected is not None
    assert selected.task_id == "TASK-002"


def test_returns_none_when_no_tasks_are_available() -> None:
    tasks = (
        make_task("TASK-001", Priority.P0, TaskStatus.DONE),
        make_task("TASK-002", Priority.P1, TaskStatus.BLOCKED),
    )

    assert select_next_task(tasks) is None


def test_build_feature_branch_creates_safe_slug() -> None:
    task = make_task("TEST-001", Priority.P0)

    task = BacklogTask(
        task_id=task.task_id,
        title="Prevent live brokerage calls from tests",
        status=task.status,
        owner=task.owner,
        priority=task.priority,
    )

    from engineering.planner import build_feature_branch

    assert build_feature_branch(task) == (
        "agent/test-001-prevent-live-brokerage-calls-from-tests"
    )


def test_build_execution_plan_combines_task_and_repository() -> None:
    from pathlib import Path

    from engineering.models import RepositoryState
    from engineering.planner import build_execution_plan

    task = make_task("TEST-001", Priority.P0)
    repository = RepositoryState(
        root=Path("/tmp/trading-bot"),
        branch="moose/dev",
        is_clean=True,
    )

    plan = build_execution_plan(task, repository)

    assert plan.task is task
    assert plan.repository is repository
    assert plan.feature_branch == "agent/test-001-test-001"
