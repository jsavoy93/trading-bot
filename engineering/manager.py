from __future__ import annotations

from pathlib import Path

from engineering.backlog import load_backlog
from engineering.config import missing_required_paths
from engineering.git_service import GitService
from engineering.planner import build_execution_plan, select_next_task
from engineering.workflow_store import StoredWorkflow, WorkflowStore


def main() -> int:
    repo_root = Path.cwd()
    missing_paths = missing_required_paths(repo_root)

    if missing_paths:
        print("Engineering Manager")
        print("===================")
        print("Status: INVALID REPOSITORY")
        print("Missing required paths:")

        for missing_path in missing_paths:
            print(f"- {missing_path}")

        return 1

    git = GitService(repo_root)
    state = git.repository_state()
    workflow_store = WorkflowStore(
        repo_root / ".git" / "engineering-workflow.json"
    )

    tasks = load_backlog(repo_root / "AGENT_BACKLOG.md")
    available_tasks = tuple(task for task in tasks if task.is_available)

    print("Engineering Manager")
    print("===================")
    print("Status:     READY")
    print(f"Repository: {state.root}")
    print(f"Branch:     {state.branch}")
    print(f"Clean:      {state.is_clean}")
    print(f"Tasks:      {len(tasks)}")
    print(f"Available:  {len(available_tasks)}")

    if workflow_store.exists():
        workflow = workflow_store.load()

        print()
        print("Workflow")
        print("--------")
        print("Action:         RESUME")
        print(f"Task:           {workflow.task_id}")
        print(f"Feature branch: {workflow.feature_branch}")
        print(f"State:          {workflow.state.value}")

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

    print()
    print("Workflow")
    print("--------")
    print("Action:            START")
    print(f"Task:              {workflow.task_id}")
    print(f"Source branch:     {plan.repository.branch}")
    print(f"Feature branch:    {workflow.feature_branch}")
    print(f"State:             {workflow.state.value}")
    print("Repository action: NONE")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
