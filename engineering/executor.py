from __future__ import annotations

import json
import os
import shlex
import subprocess
from hashlib import sha256
from dataclasses import dataclass
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
class LaunchedRun:
    run_id: str
    status: DelegationStatus


class AgentLauncher(Protocol):
    def launch(
        self,
        agent_name: str,
        branch: str,
        prompt: str,
        request_id: str,
    ) -> LaunchedRun: ...


class AgentMonitor(Protocol):
    def status(self, run_id: str) -> DelegationStatus: ...


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


class CommandAgentLauncher:
    def __init__(self, command: tuple[str, ...] | None = None):
        configured = os.environ.get("ENGINEERING_AGENT_COMMAND", "")
        self.command = command or tuple(shlex.split(configured))

    def _run(self, *args: str, input_text: str | None = None) -> dict[str, object]:
        if not self.command:
            raise RuntimeError(
                "Agent launching is not configured; set ENGINEERING_AGENT_COMMAND."
            )

        result = subprocess.run(
            [*self.command, *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )

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
    ) -> LaunchedRun:
        payload = self._run(
            "launch",
            "--agent",
            agent_name,
            "--branch",
            branch,
            "--request-id",
            request_id,
            input_text=prompt,
        )

        try:
            run_id = payload["run_id"]
            status = DelegationStatus(payload["status"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Agent launcher returned invalid run metadata.") from exc

        if not isinstance(run_id, str) or not run_id.strip():
            raise RuntimeError("Agent launcher returned an empty run ID.")
        if status not in {DelegationStatus.PENDING, DelegationStatus.ACTIVE}:
            raise RuntimeError(
                f"Agent launcher returned unexpected initial status: {status.value}"
            )

        return LaunchedRun(run_id=run_id, status=status)

    def status(self, run_id: str) -> DelegationStatus:
        payload = self._run("status", "--run-id", run_id)
        try:
            return DelegationStatus(payload["status"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Agent wrapper returned invalid status metadata.") from exc
