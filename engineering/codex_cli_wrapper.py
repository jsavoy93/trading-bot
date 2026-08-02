from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator


TERMINAL_STATES = frozenset({"COMPLETE", "FAILED", "TIMED_OUT"})
ACTIVE_STATES = frozenset({"CLAIMED", "RUNNING"})
DEFAULT_TIMEOUT_SECONDS = 1800.0
DEFAULT_GRACE_SECONDS = 5.0
DEFAULT_STALE_CLAIM_SECONDS = 30.0
DEFAULT_CLAIM_PUBLICATION_WAIT_SECONDS = 1.0
DEFAULT_MAX_ARTIFACT_BYTES = 1_000_000
SUMMARY_BYTES = 2000


class WrapperError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_time(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _publish_json_once(path: Path, payload: dict[str, object]) -> bool:
    """Publish a complete initial record without replacing another claimant."""
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        os.link(temporary, path)
        return True
    except FileExistsError:
        return False
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WrapperError(f"Missing or malformed run record: {path}") from exc
    if not isinstance(payload, dict):
        raise WrapperError(f"Malformed run record: {path}")
    return payload


def _safe_id(value: str, label: str) -> str:
    if not value or len(value) > 160 or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        for character in value
    ):
        raise WrapperError(f"Invalid {label}.")
    return value


def build_request_id(task_id: str, feature_branch: str) -> str:
    identity = f"{task_id}\0{feature_branch}".encode("utf-8")
    return f"delegation-{hashlib.sha256(identity).hexdigest()[:24]}"


def _prompt_digest(prompt: bytes) -> str:
    return hashlib.sha256(prompt).hexdigest()


def _runtime_root(value: str | None) -> Path:
    root = Path(value or os.environ.get("CODEX_WRAPPER_RUNTIME_DIR", ".agent-state/codex-runs"))
    return root.resolve()


def _record_path(run_dir: Path) -> Path:
    return run_dir / "run.json"


def _process_identity(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return f"{pid}:{fields[21]}"
    except (OSError, IndexError):
        return None


def _worker_alive(record: dict[str, object]) -> bool:
    pid = record.get("worker_pid")
    identity = record.get("worker_identity")
    return isinstance(pid, int) and isinstance(identity, str) and _process_identity(pid) == identity


@contextmanager
def _record_lock(run_dir: Path) -> Iterator[None]:
    lock = run_dir / ".lock"
    deadline = time.monotonic() + 5.0
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise WrapperError("Timed out acquiring run-record lock.")
            time.sleep(0.01)
    try:
        yield
    finally:
        try:
            lock.rmdir()
        except OSError:
            pass


def _bounded_reason(value: str) -> str:
    return value.strip()[:SUMMARY_BYTES]


def _artifact_summary(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-SUMMARY_BYTES:].decode("utf-8", errors="replace")


def _public_record(record: dict[str, object]) -> dict[str, object]:
    result = dict(record)
    for key in ("repo_path", "timeout_seconds", "grace_seconds", "max_artifact_bytes"):
        result.pop(key, None)
    return result


def _validate_record(record: dict[str, object], run_dir: Path) -> None:
    required_strings = (
        "request_id", "run_id", "agent_name", "feature_branch", "prompt_digest",
        "status", "started_at", "updated_at", "deadline_at", "stdout_path", "stderr_path",
    )
    if any(not isinstance(record.get(key), str) or not record[key] for key in required_strings):
        raise WrapperError("Malformed run record fields.")
    if record["status"] not in ACTIVE_STATES | TERMINAL_STATES:
        raise WrapperError("Malformed run status.")
    if record["run_id"] != run_dir.name:
        raise WrapperError("Conflicting run identity.")
    for key in ("stdout_path", "stderr_path"):
        path = Path(str(record[key])).resolve()
        if path.parent != run_dir.resolve():
            raise WrapperError("Artifact path escapes its run directory.")
    if record["status"] in TERMINAL_STATES:
        if not isinstance(record.get("exit_code"), int):
            raise WrapperError("Terminal record has no exit code.")
        if not isinstance(record.get("completed_at"), str):
            raise WrapperError("Terminal record has no completion time.")


def _await_claim_record(
    run_dir: Path,
    candidate: dict[str, object],
    wait_seconds: float,
) -> dict[str, object]:
    record_path = _record_path(run_dir)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if record_path.is_file():
            return _load_json(record_path)
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

    completed = _now()
    failed = dict(candidate)
    failed.update(
        status="FAILED",
        exit_code=125,
        completed_at=completed,
        updated_at=completed,
        failure_reason="Claim record publication did not complete within the bounded interval.",
    )
    _publish_json_once(record_path, failed)
    return _load_json(record_path)


def _terminal_update(run_dir: Path, status: str, exit_code: int, reason: str) -> dict[str, object]:
    with _record_lock(run_dir):
        record = _load_json(_record_path(run_dir))
        if record.get("status") in TERMINAL_STATES:
            return record
        completed = _now()
        record.update(
            status=status,
            exit_code=exit_code,
            failure_reason=_bounded_reason(reason),
            completed_at=completed,
            updated_at=completed,
            stdout_summary=_artifact_summary(Path(str(record["stdout_path"]))),
            stderr_summary=_artifact_summary(Path(str(record["stderr_path"]))),
        )
        _atomic_json(_record_path(run_dir), record)
        return record


def _pump(stream: object, path: Path, limit: int) -> None:
    written = 0
    with path.open("wb") as output:
        while True:
            chunk = stream.read(65536)  # type: ignore[attr-defined]
            if not chunk:
                break
            remaining = max(0, limit - written)
            if remaining:
                output.write(chunk[:remaining])
                written += min(len(chunk), remaining)


def _codex_command() -> list[str]:
    configured = os.environ.get("ENGINEERING_CODEX_COMMAND", "")
    command = shlex.split(configured) if configured else ["codex"]
    if os.environ.get("TESTING") == "1" or os.environ.get("UNIT_TESTING") == "1":
        if not configured:
            raise WrapperError("Tests must explicitly inject a fake Codex executable.")
        configured_name = Path(command[0]).name
        executable = Path(command[0]).resolve()
        if configured_name in {"codex", "codex.exe"} or executable.name in {"codex", "codex.exe"}:
            raise WrapperError("Real Codex invocation is forbidden during tests.")
    return command


def _validate_repository(repo: Path, branch: str) -> None:
    if not repo.is_dir():
        raise WrapperError("Repository path does not exist.")
    try:
        current = subprocess.run(
            ["git", "branch", "--show-current"], cwd=repo, capture_output=True,
            text=True, check=True, timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain=v1"], cwd=repo, capture_output=True,
            text=True, check=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise WrapperError("Cannot validate the assigned Git repository.") from exc
    if current != branch:
        raise WrapperError(f"Assigned branch {branch!r} is not checked out.")
    if dirty:
        raise WrapperError("Assigned repository is dirty.")


def _run_worker(runtime: Path, run_id: str) -> int:
    run_dir = runtime / _safe_id(run_id, "run ID")
    record_path = _record_path(run_dir)
    with _record_lock(run_dir):
        record = _load_json(record_path)
        _validate_record(record, run_dir)
        if record["status"] != "CLAIMED":
            return 0
        pid = os.getpid()
        identity = _process_identity(pid)
        if identity is None:
            completed = _now()
            record.update(status="FAILED", exit_code=125, completed_at=completed,
                          updated_at=completed, failure_reason="Cannot establish worker identity.")
            _atomic_json(record_path, record)
            return 125
        record.update(status="RUNNING", worker_pid=pid, worker_identity=identity, updated_at=_now())
        _atomic_json(record_path, record)

    stdout_path = Path(str(record["stdout_path"]))
    stderr_path = Path(str(record["stderr_path"]))
    prompt_path = run_dir / "prompt.txt"
    try:
        prompt = prompt_path.read_bytes()
        command = [
            *_codex_command(), "exec", "--sandbox", "workspace-write",
            "--cd", str(record["repo_path"]), "-",
        ]
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(record["repo_path"]), start_new_session=True,
        )
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        process.stdin.write(prompt)
        process.stdin.close()
        limit = int(record["max_artifact_bytes"])
        threads = [
            threading.Thread(target=_pump, args=(process.stdout, stdout_path, limit)),
            threading.Thread(target=_pump, args=(process.stderr, stderr_path, limit)),
        ]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            exit_code = process.wait(timeout=float(record["timeout_seconds"]))
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=float(record["grace_seconds"]))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            exit_code = 124
        for thread in threads:
            thread.join(timeout=5.0)
        if timed_out:
            _terminal_update(run_dir, "TIMED_OUT", 124, "Codex execution exceeded its deadline.")
        elif exit_code == 0:
            _terminal_update(run_dir, "COMPLETE", 0, "")
        else:
            _terminal_update(run_dir, "FAILED", exit_code, f"Codex exited with status {exit_code}.")
        return exit_code
    except Exception as exc:
        _terminal_update(run_dir, "FAILED", 125, f"Codex launch failed: {exc}")
        return 125


def _launch(args: argparse.Namespace) -> dict[str, object]:
    request_id = _safe_id(args.request_id, "request ID")
    _safe_id(args.agent, "agent name")
    prompt = sys.stdin.buffer.read(args.max_prompt_bytes + 1)
    if len(prompt) > args.max_prompt_bytes:
        raise WrapperError("Delegation prompt exceeds the configured bound.")
    digest = _prompt_digest(prompt)
    runtime = _runtime_root(args.runtime_dir)
    repo = Path(args.repo).resolve()
    run_id = f"run-{hashlib.sha256(request_id.encode()).hexdigest()[:24]}"
    run_dir = runtime / run_id
    identity = (args.agent, args.branch, digest)
    started = _now()
    deadline = datetime.fromtimestamp(time.time() + args.timeout_seconds, UTC).isoformat()
    record: dict[str, object] = {
        "request_id": request_id,
        "run_id": run_id,
        "agent_name": args.agent,
        "feature_branch": args.branch,
        "prompt_digest": digest,
        "status": "CLAIMED",
        "worker_pid": None,
        "worker_identity": None,
        "started_at": started,
        "updated_at": started,
        "deadline_at": deadline,
        "stdout_path": str((run_dir / "stdout.log").resolve()),
        "stderr_path": str((run_dir / "stderr.log").resolve()),
        "exit_code": None,
        "completed_at": None,
        "failure_reason": "",
        "stdout_summary": "",
        "stderr_summary": "",
        "repo_path": str(repo),
        "timeout_seconds": args.timeout_seconds,
        "grace_seconds": args.grace_seconds,
        "max_artifact_bytes": args.max_artifact_bytes,
    }
    if run_dir.is_dir():
        existing_record = _await_claim_record(run_dir, record, args.claim_publication_wait_seconds)
        _validate_record(existing_record, run_dir)
        existing = (existing_record["agent_name"], existing_record["feature_branch"], existing_record["prompt_digest"])
        if existing_record["request_id"] != request_id or existing != identity:
            raise WrapperError("Request identity conflict.")
        return _public_record(_reconcile(run_dir, args.stale_claim_seconds))

    _validate_repository(repo, args.branch)
    try:
        run_dir.mkdir(parents=True)
        claimed = True
    except FileExistsError:
        claimed = False

    if not claimed:
        existing_record = _await_claim_record(run_dir, record, args.claim_publication_wait_seconds)
        _validate_record(existing_record, run_dir)
        existing = (existing_record["agent_name"], existing_record["feature_branch"], existing_record["prompt_digest"])
        if existing_record["request_id"] != request_id or existing != identity:
            raise WrapperError("Request identity conflict.")
        return _public_record(_reconcile(run_dir, args.stale_claim_seconds))

    try:
        if os.environ.get("CODEX_WRAPPER_TEST_CLAIM_DELAY_SECONDS") and os.environ.get("TESTING") == "1":
            time.sleep(float(os.environ["CODEX_WRAPPER_TEST_CLAIM_DELAY_SECONDS"]))
        if not _publish_json_once(_record_path(run_dir), record):
            existing_record = _load_json(_record_path(run_dir))
            _validate_record(existing_record, run_dir)
            existing = (existing_record["agent_name"], existing_record["feature_branch"], existing_record["prompt_digest"])
            if existing_record["request_id"] != request_id or existing != identity:
                raise WrapperError("Request identity conflict.")
            return _public_record(existing_record)
        (run_dir / "prompt.txt").write_bytes(prompt)
        worker = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_worker", "--runtime-dir", str(runtime), "--run-id", run_id],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
        with _record_lock(run_dir):
            current = _load_json(_record_path(run_dir))
            current["launcher_pid"] = worker.pid
            current["updated_at"] = _now()
            _atomic_json(_record_path(run_dir), current)
        return _public_record(current)
    except Exception as exc:
        return _public_record(_terminal_update(run_dir, "FAILED", 125, f"Worker launch failed: {exc}"))


def _reconcile(run_dir: Path, stale_claim_seconds: float) -> dict[str, object]:
    with _record_lock(run_dir):
        record = _load_json(_record_path(run_dir))
        _validate_record(record, run_dir)
        if record["status"] in TERMINAL_STATES:
            return record
        now = time.time()
        if now >= _parse_time(str(record["deadline_at"])):
            status, code, reason = "TIMED_OUT", 124, "Run deadline elapsed without a live worker."
        elif record["status"] == "RUNNING" and not _worker_alive(record):
            status, code, reason = "FAILED", 125, "Recorded worker is no longer running."
        elif record["status"] == "CLAIMED" and now - _parse_time(str(record["started_at"])) > stale_claim_seconds:
            status, code, reason = "FAILED", 125, "Claimed run did not start within the stale-claim bound."
        else:
            return record
        completed = _now()
        record.update(status=status, exit_code=code, failure_reason=reason, completed_at=completed, updated_at=completed)
        _atomic_json(_record_path(run_dir), record)
        return record


def _status(args: argparse.Namespace) -> dict[str, object]:
    run_id = _safe_id(args.run_id, "run ID")
    runtime = _runtime_root(args.runtime_dir)
    run_dir = runtime / run_id
    if not run_dir.is_dir():
        raise WrapperError(f"Unknown run ID: {run_id}")
    return _public_record(_reconcile(run_dir, args.stale_claim_seconds))


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded, idempotent Codex CLI wrapper")
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--runtime-dir")
    common.add_argument("--stale-claim-seconds", type=_positive_float, default=DEFAULT_STALE_CLAIM_SECONDS)
    launch = subparsers.add_parser("launch", parents=[common])
    launch.add_argument("--agent", required=True)
    launch.add_argument("--branch", required=True)
    launch.add_argument("--request-id", required=True)
    launch.add_argument("--repo", default=".")
    launch.add_argument("--timeout-seconds", type=_positive_float, default=DEFAULT_TIMEOUT_SECONDS)
    launch.add_argument("--grace-seconds", type=_positive_float, default=DEFAULT_GRACE_SECONDS)
    launch.add_argument("--max-artifact-bytes", type=int, default=DEFAULT_MAX_ARTIFACT_BYTES)
    launch.add_argument("--max-prompt-bytes", type=int, default=100_000)
    launch.add_argument(
        "--claim-publication-wait-seconds", type=_positive_float,
        default=DEFAULT_CLAIM_PUBLICATION_WAIT_SECONDS,
    )
    status = subparsers.add_parser("status", parents=[common])
    status.add_argument("--run-id", required=True)
    worker = subparsers.add_parser("_worker")
    worker.add_argument("--runtime-dir", required=True)
    worker.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "launch":
            result = _launch(args)
        elif args.command == "status":
            result = _status(args)
        else:
            return _run_worker(_runtime_root(args.runtime_dir), args.run_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except WrapperError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
