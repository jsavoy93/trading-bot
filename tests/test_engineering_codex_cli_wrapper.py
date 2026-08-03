from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from engineering.codex_cli_wrapper import build_request_id


WRAPPER = Path(__file__).parents[1] / "engineering" / "codex_cli_wrapper.py"


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "ops-test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Codex Wrapper Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def fake_codex(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text(
        """#!/usr/bin/env python3
import json, os, subprocess, sys, time
capture = os.environ.get('FAKE_CODEX_CAPTURE')
if capture:
    with open(capture, 'w', encoding='utf-8') as handle:
        json.dump({'argv': sys.argv[1:], 'stdin': sys.stdin.read()}, handle)
else:
    sys.stdin.read()
mode = os.environ.get('FAKE_CODEX_MODE', 'success')
if mode == 'slow':
    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    time.sleep(60)
if mode == 'nonzero':
    print('fake failure', file=sys.stderr)
    raise SystemExit(17)
if mode == 'large':
    print('x' * 10000)
print('fake success')
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _environment(fake_codex: Path, **values: str) -> dict[str, str]:
    return {
        **os.environ,
        "TESTING": "1",
        "UNIT_TESTING": "1",
        "ENGINEERING_CODEX_COMMAND": str(fake_codex),
        **values,
    }


def _invoke(
    runtime: Path,
    arguments: list[str],
    *,
    env: dict[str, str],
    prompt: str = "",
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WRAPPER), *arguments, "--runtime-dir", str(runtime)],
        input=prompt,
        text=True,
        capture_output=True,
        env=env,
        check=check,
        timeout=10,
    )


def _launch(runtime: Path, repository: Path, env: dict[str, str], request_id: str, prompt: str = "do work") -> dict[str, object]:
    result = _invoke(
        runtime,
        [
            "launch", "--agent", "trading-exec", "--branch", "ops-test",
            "--request-id", request_id, "--repo", str(repository),
            "--timeout-seconds", "2", "--grace-seconds", "0.1",
        ],
        env=env,
        prompt=prompt,
    )
    return json.loads(result.stdout)


def _wait_terminal(runtime: Path, env: dict[str, str], run_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = _invoke(runtime, ["status", "--run-id", run_id], env=env)
        payload = json.loads(result.stdout)
        if payload["status"] in {"COMPLETE", "FAILED", "TIMED_OUT"}:
            return payload
        time.sleep(0.03)
    raise AssertionError("fake Codex run did not become terminal")


def test_request_ids_are_deterministic_and_task_branch_scoped() -> None:
    assert build_request_id("OPS-011", "agent/ops-011") == build_request_id("OPS-011", "agent/ops-011")
    assert build_request_id("OPS-011", "agent/ops-011") != build_request_id("OPS-012", "agent/ops-011")


def test_launch_invokes_only_injected_fake_with_bounded_safe_arguments(
    tmp_path: Path, repository: Path, fake_codex: Path,
) -> None:
    runtime = tmp_path / "runtime"
    capture = tmp_path / "capture.json"
    env = _environment(fake_codex, FAKE_CODEX_CAPTURE=str(capture))
    launched = _launch(runtime, repository, env, "request-safe", "bounded prompt")
    terminal = _wait_terminal(runtime, env, str(launched["run_id"]))

    invocation = json.loads(capture.read_text(encoding="utf-8"))
    assert invocation["argv"] == ["exec", "--sandbox", "workspace-write", "--cd", str(repository), "-"]
    assert invocation["stdin"] == "bounded prompt"
    assert "--dangerously-bypass-approvals-and-sandbox" not in invocation["argv"]
    assert terminal["status"] == "COMPLETE"
    assert terminal["exit_code"] == 0
    assert Path(str(terminal["stdout_path"])).read_text(encoding="utf-8").endswith("fake success\n")
    assert Path(str(terminal["stderr_path"])).read_text(encoding="utf-8") == ""


def test_concurrent_matching_launches_claim_once_and_return_one_run(
    tmp_path: Path, repository: Path, fake_codex: Path,
) -> None:
    runtime = tmp_path / "runtime"
    capture = tmp_path / "capture.json"
    env = _environment(fake_codex, FAKE_CODEX_CAPTURE=str(capture))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _launch(runtime, repository, env, "request-one"), range(2)))

    assert results[0]["run_id"] == results[1]["run_id"]
    _wait_terminal(runtime, env, str(results[0]["run_id"]))
    assert len(list(runtime.glob("run-*"))) == 1


def test_matching_launcher_waits_for_atomic_claim_publication(
    tmp_path: Path, repository: Path, fake_codex: Path,
) -> None:
    runtime = tmp_path / "runtime"
    delayed_env = _environment(
        fake_codex,
        CODEX_WRAPPER_TEST_CLAIM_DELAY_SECONDS="0.2",
    )
    arguments = [
        sys.executable, str(WRAPPER), "launch", "--agent", "trading-exec",
        "--branch", "ops-test", "--request-id", "request-publication-race",
        "--repo", str(repository), "--timeout-seconds", "2",
        "--runtime-dir", str(runtime),
    ]
    winner = subprocess.Popen(
        arguments,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=delayed_env,
    )
    assert winner.stdin is not None
    winner.stdin.write("same prompt")
    winner.stdin.close()
    winner.stdin = None
    deadline = time.monotonic() + 2
    while not list(runtime.glob("run-*")) and time.monotonic() < deadline:
        time.sleep(0.005)
    assert list(runtime.glob("run-*"))
    assert not list(runtime.glob("run-*/run.json"))

    matching = _launch(
        runtime,
        repository,
        _environment(fake_codex),
        "request-publication-race",
        "same prompt",
    )
    stdout, stderr = winner.communicate(timeout=5)
    assert winner.returncode == 0, stderr
    claimed = json.loads(stdout)
    assert matching["run_id"] == claimed["run_id"]
    assert matching["status"] in {"CLAIMED", "RUNNING", "COMPLETE"}
    _wait_terminal(runtime, _environment(fake_codex), str(claimed["run_id"]))


def test_incomplete_claim_beyond_bound_is_persisted_failed(
    tmp_path: Path, repository: Path, fake_codex: Path,
) -> None:
    runtime = tmp_path / "runtime"
    run_id = "run-" + __import__("hashlib").sha256(b"request-incomplete").hexdigest()[:24]
    (runtime / run_id).mkdir(parents=True)
    result = _invoke(
        runtime,
        [
            "launch", "--agent", "trading-exec", "--branch", "ops-test",
            "--request-id", "request-incomplete", "--repo", str(repository),
            "--claim-publication-wait-seconds", "0.05",
        ],
        env=_environment(fake_codex),
        prompt="work",
    )
    record = json.loads(result.stdout)
    assert record["status"] == "FAILED"
    assert record["exit_code"] == 125
    assert "publication did not complete" in record["failure_reason"]


@pytest.mark.parametrize(
    ("changed_argument", "changed_prompt"),
    [(["--agent", "dashboard-agent"], "same"), (["--branch", "other"], "same"), ([], "different")],
)
def test_reused_request_identity_conflicts_are_rejected_before_new_branch_validation(
    tmp_path: Path,
    repository: Path,
    fake_codex: Path,
    changed_argument: list[str],
    changed_prompt: str,
) -> None:
    runtime = tmp_path / "runtime"
    env = _environment(fake_codex)
    first = _launch(runtime, repository, env, "request-conflict", "same")
    _wait_terminal(runtime, env, str(first["run_id"]))
    arguments = [
        "launch", "--agent", "trading-exec", "--branch", "ops-test",
        "--request-id", "request-conflict", "--repo", str(repository),
    ]
    if changed_argument:
        index = arguments.index(changed_argument[0])
        arguments[index + 1] = changed_argument[1]
    result = _invoke(runtime, arguments, env=env, prompt=changed_prompt, check=False)
    assert result.returncode == 2
    assert "identity conflict" in result.stderr


def test_nonzero_exit_and_bounded_artifacts_are_persisted(
    tmp_path: Path, repository: Path, fake_codex: Path,
) -> None:
    runtime = tmp_path / "runtime"
    failed_env = _environment(fake_codex, FAKE_CODEX_MODE="nonzero")
    failed = _launch(runtime, repository, failed_env, "request-failed")
    terminal = _wait_terminal(runtime, failed_env, str(failed["run_id"]))
    assert terminal["status"] == "FAILED"
    assert terminal["exit_code"] == 17
    assert "fake failure" in terminal["stderr_summary"]

    large_env = _environment(fake_codex, FAKE_CODEX_MODE="large")
    result = _invoke(
        runtime,
        [
            "launch", "--agent", "trading-exec", "--branch", "ops-test",
            "--request-id", "request-large", "--repo", str(repository),
            "--timeout-seconds", "2", "--max-artifact-bytes", "100",
        ],
        env=large_env,
        prompt="work",
    )
    launched = json.loads(result.stdout)
    bounded = _wait_terminal(runtime, large_env, str(launched["run_id"]))
    assert Path(str(bounded["stdout_path"])).stat().st_size == 100


def test_timeout_terminates_process_group_and_persists_124(
    tmp_path: Path, repository: Path, fake_codex: Path,
) -> None:
    runtime = tmp_path / "runtime"
    env = _environment(fake_codex, FAKE_CODEX_MODE="slow")
    result = _invoke(
        runtime,
        [
            "launch", "--agent", "trading-exec", "--branch", "ops-test",
            "--request-id", "request-timeout", "--repo", str(repository),
            "--timeout-seconds", "0.15", "--grace-seconds", "0.05",
        ],
        env=env,
        prompt="work",
    )
    launched = json.loads(result.stdout)
    terminal = _wait_terminal(runtime, env, str(launched["run_id"]))
    assert terminal["status"] == "TIMED_OUT"
    assert terminal["exit_code"] == 124


def test_status_reconciles_stale_claim_and_rejects_bad_records(
    tmp_path: Path, repository: Path, fake_codex: Path,
) -> None:
    runtime = tmp_path / "runtime"
    env = _environment(fake_codex)
    launched = _launch(runtime, repository, env, "request-stale")
    terminal = _wait_terminal(runtime, env, str(launched["run_id"]))
    record_path = runtime / str(terminal["run_id"]) / "run.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record.update(status="CLAIMED", exit_code=None, completed_at=None, started_at="2000-01-01T00:00:00+00:00")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    reconciled = _invoke(
        runtime,
        ["status", "--run-id", str(terminal["run_id"]), "--stale-claim-seconds", "0.01"],
        env=env,
    )
    assert json.loads(reconciled.stdout)["status"] in {"FAILED", "TIMED_OUT"}

    record_path.write_text("not json", encoding="utf-8")
    malformed = _invoke(runtime, ["status", "--run-id", str(terminal["run_id"])], env=env, check=False)
    assert malformed.returncode == 2
    unknown = _invoke(runtime, ["status", "--run-id", "run-unknown"], env=env, check=False)
    assert unknown.returncode == 2


def test_test_mode_fails_closed_before_real_codex_can_run(
    tmp_path: Path, repository: Path, fake_codex: Path,
) -> None:
    runtime = tmp_path / "runtime"
    env = _environment(fake_codex)
    env["ENGINEERING_CODEX_COMMAND"] = "/usr/bin/codex"
    launched = _launch(runtime, repository, env, "request-guard")
    terminal = _wait_terminal(runtime, env, str(launched["run_id"]))
    assert terminal["status"] == "FAILED"
    assert terminal["exit_code"] == 125
    assert "Real Codex invocation is forbidden during tests" in terminal["failure_reason"]


def test_dirty_or_wrong_branch_repository_is_rejected_before_claim(
    tmp_path: Path, repository: Path, fake_codex: Path,
) -> None:
    runtime = tmp_path / "runtime"
    env = _environment(fake_codex)
    (repository / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    result = _invoke(
        runtime,
        [
            "launch", "--agent", "trading-exec", "--branch", "ops-test",
            "--request-id", "request-dirty", "--repo", str(repository),
        ],
        env=env,
        prompt="work",
        check=False,
    )
    assert result.returncode == 2
    assert "repository is dirty" in result.stderr
    assert not runtime.exists()
