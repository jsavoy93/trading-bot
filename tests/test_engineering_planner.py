from engineering.models import (
    BacklogTask,
    Complexity,
    Priority,
    RiskLevel,
    TaskStatus,
)
from engineering.planner import (
    estimate_complexity,
    estimate_risk,
    select_next_task,
)


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
    assert plan.acceptance_criteria == task.acceptance_criteria
    assert plan.allowed_areas == task.allowed_areas
    assert plan.risk is RiskLevel.LOW
    assert plan.complexity is Complexity.SMALL


def test_execution_plan_starts_in_discover_state() -> None:
    from pathlib import Path

    from engineering.models import (
        BacklogTask,
        Priority,
        RepositoryState,
        TaskStatus,
        WorkflowState,
    )
    from engineering.planner import build_execution_plan

    task = BacklogTask(
        task_id="TEST-001",
        title="Prevent live brokerage calls from tests",
        status=TaskStatus.TODO,
        owner="trading-exec",
        priority=Priority.P0,
        acceptance_criteria=["Live brokerage calls are blocked."],
        allowed_areas=["tests"],
    )

    repository = RepositoryState(
        root=Path("/tmp/trading-bot"),
        branch="source-branch",
        is_clean=True,
    )

    plan = build_execution_plan(task, repository)

    assert plan.workflow_state is WorkflowState.DISCOVER


def test_estimate_risk_uses_deterministic_scope_terms() -> None:
    low_risk_task = make_task("DOCS-001", Priority.P3)
    medium_risk_task = BacklogTask(
        task_id="OPS-003",
        title="Implement workflow planning",
        status=TaskStatus.TODO,
        owner="trading-manager",
        priority=Priority.P0,
    )
    high_risk_task = BacklogTask(
        task_id="TEST-001",
        title="Prevent live brokerage calls from tests",
        status=TaskStatus.TODO,
        owner="trading-exec",
        priority=Priority.P0,
    )

    assert estimate_risk(low_risk_task) is RiskLevel.LOW
    assert estimate_risk(medium_risk_task) is RiskLevel.MEDIUM
    assert estimate_risk(high_risk_task) is RiskLevel.HIGH


def test_estimate_complexity_uses_criteria_and_area_counts() -> None:
    small_task = make_task("TASK-001", Priority.P3)
    medium_task = BacklogTask(
        task_id="TASK-002",
        title="Medium task",
        status=TaskStatus.TODO,
        owner="test-agent",
        priority=Priority.P2,
        acceptance_criteria=("one", "two", "three"),
    )
    large_task = BacklogTask(
        task_id="TASK-003",
        title="Large task",
        status=TaskStatus.TODO,
        owner="test-agent",
        priority=Priority.P2,
        allowed_areas=("one", "two", "three", "four"),
    )

    assert estimate_complexity(small_task) is Complexity.SMALL
    assert estimate_complexity(medium_task) is Complexity.MEDIUM
    assert estimate_complexity(large_task) is Complexity.LARGE
