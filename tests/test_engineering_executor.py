from types import SimpleNamespace

import pytest

from engineering.executor import CommandAgentLauncher, build_agent_prompt, select_specialist
from engineering.models import BacklogTask, DelegationStatus, Priority, TaskStatus


def make_task(owner: str = "trading-exec") -> BacklogTask:
    return BacklogTask(
        task_id="TEST-001",
        title="Prevent live brokerage calls from tests",
        status=TaskStatus.TODO,
        owner=owner,
        priority=Priority.P0,
        acceptance_criteria=("Automated tests cannot contact a live endpoint.",),
        allowed_areas=("tests/", "brokerage client abstractions"),
    )


def test_build_agent_prompt_contains_bounded_assignment() -> None:
    prompt = build_agent_prompt(make_task(), "agent/test-001")

    assert "Task: TEST-001 — Prevent live brokerage calls from tests" in prompt
    assert "Assigned specialist: trading-exec" in prompt
    assert "Branch: agent/test-001" in prompt
    assert "- Automated tests cannot contact a live endpoint." in prompt
    assert "- tests/" in prompt
    assert "Do not enable live trading" in prompt
    assert ".venv/bin/python -m pytest" in prompt
    assert "acceptance evidence" in prompt
    assert "Do not merge or mark your own work accepted." in prompt


@pytest.mark.parametrize("owner", ("trading-exec", "dashboard-agent"))
def test_select_specialist_accepts_approved_owners(owner: str) -> None:
    assert select_specialist(make_task(owner)) == owner


def test_select_specialist_rejects_unapproved_owner() -> None:
    with pytest.raises(RuntimeError, match="not an approved delegation specialist"):
        select_specialist(make_task("trading-manager"))


def test_command_launcher_requires_explicit_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENGINEERING_AGENT_COMMAND", raising=False)

    with pytest.raises(RuntimeError, match="Agent launching is not configured"):
        CommandAgentLauncher().launch("trading-exec", "agent/test-001", "prompt")


def test_command_launcher_invokes_configured_wrapper_and_parses_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        recorded["args"] = args
        recorded.update(kwargs)
        return SimpleNamespace(stdout='{"run_id":"run-123","status":"ACTIVE"}')

    monkeypatch.setattr("engineering.executor.subprocess.run", fake_run)

    launched = CommandAgentLauncher(("agent-wrapper", "launch")).launch(
        "trading-exec",
        "agent/test-001",
        "bounded prompt",
    )

    assert launched.run_id == "run-123"
    assert launched.status is DelegationStatus.ACTIVE
    assert recorded["args"] == [
        "agent-wrapper",
        "launch",
        "--agent",
        "trading-exec",
        "--branch",
        "agent/test-001",
    ]
    assert recorded["input"] == "bounded prompt"
    assert recorded["check"] is True
    assert recorded["timeout"] == 30
