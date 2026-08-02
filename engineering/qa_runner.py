from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Callable


MAX_OUTPUT_CHARS = 4000
QA_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class QAExecution:
    command: tuple[str, ...]
    exit_code: int
    duration_seconds: float
    output_summary: str
    changed_files: tuple[str, ...]
    timed_out: bool
    passed_count: int | None = None
    failed_count: int | None = None


def _configured_command(command: tuple[str, ...] | None) -> tuple[str, ...]:
    configured = (
        command
        if command is not None
        else tuple(shlex.split(os.environ.get("ENGINEERING_QA_COMMAND", "")))
    )
    if not configured:
        raise RuntimeError(
            "QA is not configured; set ENGINEERING_QA_COMMAND to a pytest command."
        )
    executable = Path(configured[0]).name
    if (
        len(configured) < 3
        or not executable.startswith("python")
        or configured[1:3] != ("-m", "pytest")
    ):
        raise RuntimeError("QA command must invoke Python with `-m pytest`.")
    return configured


def _changed_files(repo_root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return tuple(
        line[3:] for line in result.stdout.splitlines() if len(line) >= 4
    )


def _result_count(output: str, result: str) -> int | None:
    matches = re.findall(rf"\b(\d+) {result}\b", output)
    return int(matches[-1]) if matches else None


def run_qa(
    repo_root: Path,
    *,
    command: tuple[str, ...] | None = None,
    clock: Callable[[], float] = monotonic,
) -> QAExecution:
    resolved_command = _configured_command(command)
    environment = os.environ.copy()
    environment["TESTING"] = "1"
    environment["UNIT_TESTING"] = "1"
    started = clock()
    timed_out = False

    try:
        result = subprocess.run(
            list(resolved_command),
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=QA_TIMEOUT_SECONDS,
            env=environment,
        )
        exit_code = result.returncode
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr
        output = (stdout or "") + (stderr or "") + "\nQA command timed out."

    duration = round(clock() - started, 3)
    return QAExecution(
        command=resolved_command,
        exit_code=exit_code,
        duration_seconds=duration,
        output_summary=output[-MAX_OUTPUT_CHARS:],
        changed_files=_changed_files(repo_root),
        timed_out=timed_out,
        passed_count=_result_count(output, "passed"),
        failed_count=_result_count(output, "failed"),
    )
