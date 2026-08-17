from pathlib import Path
from subprocess import TimeoutExpired
from types import SimpleNamespace

import pytest

from engineering.qa_runner import MAX_OUTPUT_CHARS, QA_TIMEOUT_SECONDS, run_qa


def test_run_qa_is_bounded_safe_and_records_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        if args[:2] == ["git", "status"]:
            return SimpleNamespace(stdout=" M src/example.py\n?? tests/new_test.py\n")
        return SimpleNamespace(
            returncode=0,
            stdout="================ 5 passed, 2 failed in 1.50s ================\n",
            stderr="",
        )

    ticks = iter((10.0, 11.5))
    monkeypatch.setattr("engineering.qa_runner.subprocess.run", fake_run)

    result = run_qa(
        tmp_path,
        command=("python", "-m", "pytest", "tests/test_example.py"),
        clock=lambda: next(ticks),
    )

    assert result.exit_code == 0
    assert result.duration_seconds == 1.5
    assert "5 passed, 2 failed" in result.output_summary
    assert result.passed_count == 5
    assert result.failed_count == 2
    assert result.changed_files == ("src/example.py", "tests/new_test.py")
    assert result.timed_out is False
    qa_args, qa_kwargs = calls[0]
    assert qa_args == ["python", "-m", "pytest", "tests/test_example.py"]
    assert qa_kwargs["timeout"] == QA_TIMEOUT_SECONDS
    assert qa_kwargs["env"]["TESTING"] == "1"
    assert qa_kwargs["env"]["UNIT_TESTING"] == "1"


def test_run_qa_rejects_missing_or_non_pytest_commands(tmp_path: Path) -> None:
    """Empty command rejects with 'not configured'; single-token rejects with 'at least 2 tokens'."""
    with pytest.raises(RuntimeError, match="QA is not configured"):
        run_qa(tmp_path, command=())
    with pytest.raises(RuntimeError, match="at least 2 tokens"):
        run_qa(tmp_path, command=("npm",))  # too few tokens
    with pytest.raises(RuntimeError, match="at least 2 tokens"):
        run_qa(tmp_path, command=("python",))  # too few tokens
    with pytest.raises(RuntimeError, match="Invalid QA executable"):
        run_qa(tmp_path, command=(".", "run", "test"))
    with pytest.raises(RuntimeError, match="Invalid QA executable"):
        run_qa(tmp_path, command=("..", "run", "test"))


def test_run_qa_accepts_npm_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """npm family commands are accepted and executed via subprocess argv."""
    qa_calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "status"]:
            return SimpleNamespace(stdout="")
        qa_calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("engineering.qa_runner.subprocess.run", fake_run)

    for cmd in [
        ("npm", "test"),
        ("npm", "run", "test"),
        ("npm", "run", "typecheck"),
        ("npm", "run", "build"),
    ]:
        result = run_qa(tmp_path, command=cmd, clock=lambda: 0.0)
        assert result.exit_code == 0, f"command {cmd!r} should succeed"
        qa_args, qa_kwargs = qa_calls[-1]
        assert list(qa_args) == list(cmd)
        assert qa_kwargs["cwd"] == tmp_path
        assert qa_kwargs["timeout"] == QA_TIMEOUT_SECONDS


def test_run_qa_accepts_npx_vitest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """npx vitest command is accepted and executed via subprocess argv."""
    qa_calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "status"]:
            return SimpleNamespace(stdout="")
        qa_calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("engineering.qa_runner.subprocess.run", fake_run)

    result = run_qa(tmp_path, command=("npx", "vitest", "run"), clock=lambda: 0.0)
    assert result.exit_code == 0
    vitest_call = [(args, kw) for args, kw in qa_calls if args[0] == "npx"][0]
    assert list(vitest_call[0]) == ["npx", "vitest", "run"]
    assert vitest_call[1]["cwd"] == tmp_path


def test_run_qa_records_timeout_and_bounds_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "status"]:
            return SimpleNamespace(stdout="")
        raise TimeoutExpired(args, QA_TIMEOUT_SECONDS, output="x" * 5000)

    ticks = iter((1.0, 301.0))
    monkeypatch.setattr("engineering.qa_runner.subprocess.run", fake_run)

    result = run_qa(
        tmp_path,
        command=("python", "-m", "pytest"),
        clock=lambda: next(ticks),
    )

    assert result.exit_code == 124
    assert result.timed_out is True
    assert len(result.output_summary) == MAX_OUTPUT_CHARS
    assert result.output_summary.endswith("QA command timed out.")
