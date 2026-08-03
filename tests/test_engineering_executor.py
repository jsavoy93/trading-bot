from types import SimpleNamespace

import pytest

from engineering.executor import (
    CommandAgentLauncher,
    build_agent_prompt,
    build_request_id,
    select_specialist,
)
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


def run_payload(status: str = "RUNNING", **overrides: object) -> str:
    terminal = status in {"COMPLETE", "FAILED", "TIMED_OUT"}
    payload: dict[str, object] = {
        "request_id": "request-123",
        "run_id": "run-123",
        "agent_name": "trading-exec",
        "feature_branch": "agent/test-001",
        "status": status,
        "started_at": "2026-08-03T00:00:00+00:00",
        "updated_at": "2026-08-03T00:01:00+00:00",
        "deadline_at": "2026-08-03T00:30:00+00:00",
        "stdout_path": "/tmp/run-123/stdout.log",
        "stderr_path": "/tmp/run-123/stderr.log",
        "exit_code": 0 if status == "COMPLETE" else (17 if terminal else None),
        "completed_at": "2026-08-03T00:02:00+00:00" if terminal else None,
        "failure_reason": "failed" if terminal and status != "COMPLETE" else "",
    }
    if status == "TIMED_OUT":
        payload["exit_code"] = 124
    payload.update(overrides)
    return __import__("json").dumps(payload)


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


def test_command_launcher_defaults_to_repository_owned_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        recorded["args"] = args
        return SimpleNamespace(stdout=run_payload())

    monkeypatch.setattr("engineering.executor.subprocess.run", fake_run)
    CommandAgentLauncher(repo_root=__import__("pathlib").Path("/tmp/repo")).launch(
        "trading-exec", "agent/test-001", "prompt", "request-123"
    )

    args = recorded["args"]
    assert isinstance(args, list)
    assert args[1].endswith("engineering/codex_cli_wrapper.py")


def test_command_launcher_invokes_configured_wrapper_and_parses_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        recorded["args"] = args
        recorded.update(kwargs)
        return SimpleNamespace(stdout=run_payload())

    monkeypatch.setattr("engineering.executor.subprocess.run", fake_run)

    launched = CommandAgentLauncher(("agent-wrapper",)).launch(
        "trading-exec",
        "agent/test-001",
        "bounded prompt",
        "request-123",
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
        "--request-id",
        "request-123",
        "--repo",
        str(__import__("pathlib").Path.cwd()),
    ]
    assert recorded["input"] == "bounded prompt"
    assert recorded["check"] is True
    assert recorded["timeout"] == 30
    assert launched.request_id == "request-123"
    assert launched.deadline_at == "2026-08-03T00:30:00+00:00"


def test_build_request_id_is_deterministic_and_task_scoped() -> None:
    first = build_request_id("TEST-001", "agent/test-001")

    assert first == build_request_id("TEST-001", "agent/test-001")
    assert first != build_request_id("TEST-002", "agent/test-001")


def test_command_monitor_requests_and_parses_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        recorded["args"] = args
        recorded.update(kwargs)
        return SimpleNamespace(stdout=run_payload("COMPLETE"))

    monkeypatch.setattr("engineering.executor.subprocess.run", fake_run)

    status = CommandAgentLauncher(("agent-wrapper",)).status("run-123")

    assert status.status is DelegationStatus.COMPLETE
    assert status.exit_code == 0
    assert recorded["args"] == [
        "agent-wrapper",
        "status",
        "--run-id",
        "run-123",
    ]
    assert recorded["timeout"] == 30


@pytest.mark.parametrize(
    "stdout",
    (
        "not-json",
        "[]",
        "{}",
        run_payload("UNKNOWN"),
    ),
)
def test_command_monitor_rejects_malformed_status_metadata(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr("engineering.executor.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="invalid (JSON|run metadata|lifecycle status)"):
        CommandAgentLauncher(("agent-wrapper",)).status("run-123")


@pytest.mark.parametrize(
    "payload",
    (
        run_payload("RUNNING", request_id="other"),
        run_payload("COMPLETE", exit_code=17),
        run_payload("TIMED_OUT", exit_code=17),
        run_payload("FAILED", completed_at=None),
    ),
)
def test_command_launcher_rejects_conflicting_or_incomplete_metadata(
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
) -> None:
    monkeypatch.setattr(
        "engineering.executor.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=payload),
    )
    with pytest.raises(RuntimeError):
        CommandAgentLauncher(("fake-wrapper",)).launch(
            "trading-exec", "agent/test-001", "prompt", "request-123"
        )


def test_command_monitor_rejects_mismatched_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "engineering.executor.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=run_payload("RUNNING", run_id="other")
        ),
    )

    with pytest.raises(RuntimeError, match="mismatched status run ID"):
        CommandAgentLauncher(("fake-wrapper",)).status("run-123")


def test_command_wrapper_failure_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise __import__("subprocess").CalledProcessError(
            125, ["fake-wrapper"], stderr="x" * 3000
        )

    monkeypatch.setattr("engineering.executor.subprocess.run", fail)

    with pytest.raises(RuntimeError) as error:
        CommandAgentLauncher(("fake-wrapper",)).status("run-123")

    assert len(str(error.value)) <= 2040
