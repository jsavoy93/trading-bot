from __future__ import annotations

import json
import os
import subprocess
import sys
from hashlib import sha256
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from engineering.models import BacklogTask, DelegationStatus


APPROVED_SPECIALISTS: frozenset[str] = frozenset(
    {"trading-exec", "dashboard-agent"}
)

SAFETY_CONSTRAINTS: tuple[str, ...] = (
    "Do not enable live trading or use live brokerage credentials.",
    "Do not merge branches or push directly to main.",
    "Do not modify secrets, .env files, database files, or OpenClaw "
    "configuration.",
    "Do not delete, move, or archive whole files.",
    "Work only on the assigned branch and allowed files; stop if scope must "
    "expand.",
    "Run the required tests and report exact results.",
)


@dataclass(frozen=True)
class AgentRun:
    request_id: str
    run_id: str
    agent_name: str
    feature_branch: str
    status: DelegationStatus
    started_at: str
    updated_at: str
    deadline_at: str
    stdout_path: str
    stderr_path: str
    exit_code: int | None = None
    completed_at: str | None = None
    failure_reason: str = ""


LaunchedRun = AgentRun


class AgentLauncher(Protocol):
    def launch(
        self,
        agent_name: str,
        branch: str,
        prompt: str,
        request_id: str,
    ) -> AgentRun: ...


class AgentMonitor(Protocol):
    def status(self, run_id: str) -> AgentRun: ...


def build_request_id(task_id: str, feature_branch: str) -> str:
    identity = f"{task_id}\0{feature_branch}".encode("utf-8")
    return f"delegation-{sha256(identity).hexdigest()[:24]}"


def select_specialist(task: BacklogTask) -> str:
    if task.owner not in APPROVED_SPECIALISTS:
        raise RuntimeError(
            f"Backlog owner {task.owner!r} is not an approved delegation specialist."
        )
    return task.owner


def _format_list(label: str, values: tuple[str, ...]) -> list[str]:
    rendered = [f"{label}:"]
    rendered.extend(f"- {value}" for value in values)
    if not values:
        rendered.append("- None specified; stop and report the missing scope.")
    return rendered


def build_agent_prompt(task: BacklogTask, feature_branch: str) -> str:
    lines = [
        "Implement exactly one approved trading-bot backlog task.",
        f"Task: {task.task_id} — {task.title}",
        f"Assigned specialist: {select_specialist(task)}",
        f"Branch: {feature_branch}",
        "",
        *_format_list("Acceptance criteria", task.acceptance_criteria),
        "",
        *_format_list("Allowed areas", task.allowed_areas),
        "",
        *_format_list("Safety constraints", SAFETY_CONSTRAINTS),
        "",
        "Required tests:",
        "- Add or update focused tests for the assigned behavior.",
        "- Run the focused tests and `.venv/bin/python -m pytest`.",
        "- Stop and report the failure if a required test fails.",
        "",
        "Required report:",
        "- Commit, files changed, tests and exact results, acceptance evidence, "
        "risks, and next step.",
        "- Do not merge or mark your own work accepted.",
    ]
    return "\n".join(lines) + "\n"


_WRAPPER_STATUS_MAP: dict[str, DelegationStatus] = {
    "CLAIMED": DelegationStatus.PENDING,
    "RUNNING": DelegationStatus.ACTIVE,
    "COMPLETE": DelegationStatus.COMPLETE,
    "FAILED": DelegationStatus.FAILED,
    "TIMED_OUT": DelegationStatus.TIMED_OUT,
}
MAX_REASON_CHARS = 2000
WRAPPER_COMMAND_TIMEOUT_SECONDS = 30


def _timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Agent wrapper returned invalid {field}.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(f"Agent wrapper returned invalid {field}.") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"Agent wrapper returned timezone-naive {field}.")
    return value


def parse_agent_run(payload: dict[str, object]) -> AgentRun:
    try:
        wrapper_status = payload["status"]
        if not isinstance(wrapper_status, str):
            raise TypeError
        status = _WRAPPER_STATUS_MAP[wrapper_status]
        required = {
            field: payload[field]
            for field in (
                "request_id", "run_id", "agent_name", "feature_branch",
                "stdout_path", "stderr_path",
            )
        }
    except TypeError as exc:
        raise RuntimeError("Agent wrapper returned invalid run metadata.") from exc
    except KeyError as exc:
        if isinstance(payload.get("status"), str) and payload.get("status") not in _WRAPPER_STATUS_MAP:
            raise RuntimeError("Agent wrapper returned invalid lifecycle status.") from exc
        raise RuntimeError("Agent wrapper returned invalid run metadata.") from exc

    if any(not isinstance(value, str) or not value for value in required.values()):
        raise RuntimeError("Agent wrapper returned blank run metadata.")
    started_at = _timestamp(payload.get("started_at"), "start time")
    updated_at = _timestamp(payload.get("updated_at"), "update time")
    deadline_at = _timestamp(payload.get("deadline_at"), "deadline")
    exit_code = payload.get("exit_code")
    completed_at = payload.get("completed_at")
    reason = payload.get("failure_reason", "")
    if exit_code is not None and not isinstance(exit_code, int):
        raise RuntimeError("Agent wrapper returned invalid exit code.")
    if completed_at is not None:
        completed_at = _timestamp(completed_at, "completion time")
    if not isinstance(reason, str) or len(reason) > MAX_REASON_CHARS:
        raise RuntimeError("Agent wrapper returned invalid terminal reason.")
    terminal = status in {
        DelegationStatus.COMPLETE,
        DelegationStatus.FAILED,
        DelegationStatus.TIMED_OUT,
    }
    if terminal != (exit_code is not None and completed_at is not None):
        raise RuntimeError("Agent wrapper returned incomplete terminal metadata.")
    if status is DelegationStatus.COMPLETE and exit_code != 0:
        raise RuntimeError("Completed agent run must have exit code zero.")
    if status is DelegationStatus.TIMED_OUT and exit_code != 124:
        raise RuntimeError("Timed-out agent run must have exit code 124.")
    if status is DelegationStatus.FAILED and exit_code == 0:
        raise RuntimeError("Failed agent run cannot have exit code zero.")
    return AgentRun(
        request_id=str(required["request_id"]),
        run_id=str(required["run_id"]),
        agent_name=str(required["agent_name"]),
        feature_branch=str(required["feature_branch"]),
        status=status,
        started_at=started_at,
        updated_at=updated_at,
        deadline_at=deadline_at,
        stdout_path=str(required["stdout_path"]),
        stderr_path=str(required["stderr_path"]),
        exit_code=exit_code,
        completed_at=completed_at,
        failure_reason=reason,
    )


def validate_run_identity(
    run: AgentRun,
    *,
    request_id: str,
    run_id: str | None,
    agent_name: str,
    feature_branch: str,
) -> None:
    if run.request_id != request_id:
        raise RuntimeError("Agent wrapper request identity does not match delegation.")
    if run_id is not None and run.run_id != run_id:
        raise RuntimeError("Agent wrapper run identity does not match delegation.")
    if run.agent_name != agent_name or run.feature_branch != feature_branch:
        raise RuntimeError("Agent wrapper assignment identity does not match delegation.")


class CommandAgentLauncher:
    def __init__(
        self,
        command: tuple[str, ...] | None = None,
        *,
        repo_root: Path | None = None,
        timeout_seconds: int = WRAPPER_COMMAND_TIMEOUT_SECONDS,
    ):
        wrapper = Path(__file__).with_name("codex_cli_wrapper.py").resolve()
        if command is not None and not (
            os.environ.get("TESTING") == "1" or os.environ.get("UNIT_TESTING") == "1"
        ):
            raise RuntimeError("Injected wrapper commands are allowed only in tests.")
        self.command = command or (sys.executable, str(wrapper))
        self.repo_root = (repo_root or Path.cwd()).resolve()
        self.timeout_seconds = timeout_seconds

    def _run(self, *args: str, input_text: str | None = None) -> dict[str, object]:
        if not self.command:
            raise RuntimeError("Repository-owned agent wrapper command is unavailable.")

        try:
            result = subprocess.run(
                [*self.command, *args],
                input=input_text,
                capture_output=True,
                text=True,
                check=True,
                timeout=self.timeout_seconds,
                cwd=self.repo_root,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Agent wrapper command timed out.") from exc
        except subprocess.CalledProcessError as exc:
            diagnostic = (exc.stderr or "").strip()[-MAX_REASON_CHARS:]
            detail = f": {diagnostic}" if diagnostic else ""
            raise RuntimeError(f"Agent wrapper command failed{detail}") from exc

        try:
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict):
                raise TypeError("launcher result must be an object")
            return payload
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("Agent wrapper returned invalid JSON metadata.") from exc

    def launch(
        self,
        agent_name: str,
        branch: str,
        prompt: str,
        request_id: str,
    ) -> AgentRun:
        payload = self._run(
            "launch",
            "--agent",
            agent_name,
            "--branch",
            branch,
            "--request-id",
            request_id,
            "--repo",
            str(self.repo_root),
            input_text=prompt,
        )
        run = parse_agent_run(payload)
        validate_run_identity(
            run,
            request_id=request_id,
            run_id=None,
            agent_name=agent_name,
            feature_branch=branch,
        )
        if run.status not in {DelegationStatus.PENDING, DelegationStatus.ACTIVE}:
            raise RuntimeError(
                f"Agent launcher returned unexpected initial status: {run.status.value}"
            )
        return run

    def status(self, run_id: str) -> AgentRun:
        payload = self._run("status", "--run-id", run_id)
        run = parse_agent_run(payload)
        if run.run_id != run_id:
            raise RuntimeError("Agent wrapper returned mismatched status run ID.")
        return run
