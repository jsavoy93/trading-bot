from __future__ import annotations

import argparse
import warnings
from pathlib import Path

from engineering.backlog import load_backlog
from engineering.config import missing_required_paths
from engineering.context import build_project_context
from engineering.manager_driver import DriverBounds, drive_workflow
from engineering.models import ProjectConfig, TRADING_BOT_PROJECT
from engineering.planner import build_execution_plan, select_next_task
from engineering.workflow_engine import dispatch_workflow
from engineering.workflow_store import StoredWorkflow


def persist_workflow_result(
    workflow_adapter,  # WorkflowAdapter
    workflow: StoredWorkflow,
) -> Path | None:
    """Persist workflow result using the WorkflowAdapter.

    Receives WorkflowAdapter (not WorkflowStore) per ENGPLAT-002B shallow
    propagation rule. Calls adapter.workflow_store().archive_completed() and
    adapter.workflow_store().clear().
    """
    from engineering.models import WorkflowState

    if workflow.state is not WorkflowState.COMPLETE:
        workflow_adapter.workflow_store().save(workflow)
        return None

    store = workflow_adapter.workflow_store()
    archive_path = store.archive_completed(workflow)
    store.clear()
    return archive_path


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0 or not parsed < float("inf"):
        raise argparse.ArgumentTypeError("must be finite and positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic engineering manager")
    parser.add_argument("--drive", action="store_true")
    parser.add_argument("--max-steps", type=_positive_int, default=8)
    parser.add_argument("--max-elapsed-seconds", type=_positive_float, default=900.0)
    parser.add_argument("--wait-poll-interval-seconds", type=_positive_float, default=30.0)
    parser.add_argument("--max-wait-polls", type=_positive_int, default=20)
    return parser


def _print_driver_result(result: object) -> None:
    workflow = result.workflow
    print()
    print("Workflow driver")
    print("---------------")
    print(f"Task:       {workflow.task_id}")
    print(f"State:      {workflow.state.value}")
    print(f"Steps:      {result.steps}")
    print(f"Wait polls: {result.wait_polls}")
    print(f"Elapsed:    {result.elapsed_seconds:.3f}s")
    print(f"Continuity: {workflow.driver.continuity}")
    print(f"Blocked:    {result.blocked}")
    print(f"Stale:      {result.stale}")
    print(f"Stop:       {result.stop_reason}")


def _bounds(args: argparse.Namespace) -> DriverBounds:
    return DriverBounds(
        max_steps=args.max_steps,
        max_elapsed_seconds=args.max_elapsed_seconds,
        wait_poll_interval_seconds=args.wait_poll_interval_seconds,
        max_wait_polls=args.max_wait_polls,
    )


def _manager_main(config: ProjectConfig, args: argparse.Namespace) -> int:
    """ProjectContext-aware manager entry point.

    Uses build_project_context(config) for all store access.
    Derives paths exclusively from config. No hardcoded paths.
    """
    repo_root = config.repository_root
    missing_paths = missing_required_paths(repo_root)

    if missing_paths:
        print("Engineering Manager")
        print("===================")
        print("Status: INVALID REPOSITORY")
        print("Missing required paths:")

        for missing_path in missing_paths:
            print(f"- {missing_path}")

        return 1

    ctx = build_project_context(config)

    state = ctx.git.repository_state()

    workflow_adapter = ctx.workflow
    event_adapter = ctx.events
    governance_adapter = ctx.governance

    tasks = governance_adapter.load_backlog()
    available_tasks = tuple(task for task in tasks if task.is_available)

    print("Engineering Manager")
    print("===================")
    print("Status:     READY")
    print(f"Repository: {state.root}")
    print(f"Branch:     {state.branch}")
    print(f"Clean:      {state.is_clean}")
    print(f"Tasks:      {len(tasks)}")
    print(f"Available:  {len(available_tasks)}")

    workflow_store = workflow_adapter.workflow_store()

    if workflow_store.exists():
        if args.drive:
            result = drive_workflow(workflow_store, _bounds(args))
            _print_driver_result(result)
            return 0
        workflow = workflow_store.load()
        workflow = dispatch_workflow(workflow)
        archive_path = persist_workflow_result(workflow_adapter, workflow)

        print()
        print("Workflow")
        print("--------")
        print("Action:         RESUME")
        print(f"Task:           {workflow.task_id}")
        print(f"Feature branch: {workflow.feature_branch}")
        print(f"State:          {workflow.state.value}")
        if archive_path is not None:
            print(f"Audit archive:  {archive_path}")

        return 0

    next_task = select_next_task(tasks)

    if next_task is None:
        print()
        print("Next task")
        print("---------")
        print("No available tasks.")
        return 0

    print()
    print("Next task")
    print("---------")
    print(f"ID:       {next_task.task_id}")
    print(f"Title:    {next_task.title}")
    print(f"Owner:    {next_task.owner}")
    print(f"Priority: {next_task.priority.value}")

    plan = build_execution_plan(next_task, state)

    workflow = StoredWorkflow(
        task_id=plan.task.task_id,
        feature_branch=plan.feature_branch,
        state=plan.workflow_state,
    )
    workflow_store.save(workflow)
    if args.drive:
        result = drive_workflow(workflow_store, _bounds(args))
        _print_driver_result(result)
        return 0
    workflow = dispatch_workflow(workflow)
    archive_path = persist_workflow_result(workflow_adapter, workflow)

    print()
    print("Workflow")
    print("--------")
    print("Action:            START")
    print(f"Task:              {workflow.task_id}")
    print(f"Source branch:     {plan.repository.branch}")
    print(f"Feature branch:    {workflow.feature_branch}")
    print(f"State:             {workflow.state.value}")
    if archive_path is not None:
        print(f"Audit archive:     {archive_path}")
    print("Repository action: NONE")

    return 0


def main(argv: list[str] | None = None) -> int:
    """Legacy entry point — emits DeprecationWarning then delegates to _manager_main."""
    args = _parser().parse_args(argv)

    warnings.warn(
        "engineering.manager.main() is deprecated and will be removed after "
        "ENGPLAT-002C is complete and a second managed project has passed "
        "integration testing. Use _manager_main(TRADING_BOT_PROJECT) directly.",
        DeprecationWarning,
        stacklevel=2,
    )

    return _manager_main(TRADING_BOT_PROJECT, args)


if __name__ == "__main__":
    raise SystemExit(main())
