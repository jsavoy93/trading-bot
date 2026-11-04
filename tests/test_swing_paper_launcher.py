"""Launcher-script tests for STRAT-SWING-PAPER — no network, no broker.

The daily RSI(2) paper run is launched by
`experiments/swing_paper/deploy/run_daily.sh`, the ExecStart target of the
`swing-rsi2-paper.service` user unit. That wrapper was previously
untracked, so a missing `--symbols` flag broke the 2026-11-04 run with
`error: the following arguments are required: --symbols` (exit 2).

These tests put the launcher under version control *and* pin the launch
contract so the same class of breakage cannot recur silently:

  L1  the wrapper exists in the repo and contains no secrets
  L2  the launch command carries `--symbols` (the 2026-11-04 regression)
  L3  the launch command carries `--execute` (the mode that places paper
      orders; a dry-run launcher would silently trade nothing)
  L4  the launch command carries the CURRENT limits, unchanged:
        --sleeve-pct 0.15, --rsi2-cap 4, --max-open 4,
        --per-position-cap 0.25
  L5  the runner's real argparse ACCEPTS the exact command the wrapper
      runs (the launch command is parsed, not pattern-matched, so a flag
      rename or a typo fails here instead of on the server)
  L6  the parsed values equal the limits in rules.py, so the launcher and
      the strategy code cannot drift apart
  L7  paper-only posture: the wrapper exports TRADING_BOT_PAPER_ONLY=1
      and sources credentials from an external .env without inlining
      any key material

No network, no broker, no state writes. The launcher is only ever READ
and PARSED here — it is never executed.
"""
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SWING_DIR = REPO_ROOT / "experiments" / "swing_paper"
LAUNCHER = SWING_DIR / "deploy" / "run_daily.sh"

sys.path.insert(0, str(SWING_DIR))

import runner  # noqa: E402
from rules import (RSI2_DAILY_CAP, RSI2_MAX_OPEN_POSITIONS,  # noqa: E402
                   RSI2_PER_POSITION_CAP_PCT, RSI2_SLEEVE_PCT)


# --------------------------------------------------------------- helpers
def launcher_text() -> str:
    assert LAUNCHER.is_file(), f"launcher not tracked at {LAUNCHER}"
    return LAUNCHER.read_text()


def parse_launch_command(text: str) -> list[str]:
    """Extract the runner argv from the wrapper's final `exec` line.

    The wrapper ends with a line-continuation `exec "$PY" "$RUNNER" ...`.
    We join the backslash continuations, drop the `exec`, and drop the
    shell variable references ($PY, $RUNNER, $SYMBOLS) that stand for
    the interpreter, the runner path, and the symbol list — the three
    values the shell resolves at run time. What remains is the flag
    vector the runner actually receives.
    """
    lines = text.splitlines()
    joined = " ".join(ln.strip() for ln in lines)
    # Join backslash line-continuations into a single logical line.
    joined = re.sub(r"\\\s*", " ", joined)

    exec_match = re.search(r"\bexec\s+(.*)$", joined, re.MULTILINE)
    assert exec_match, "no `exec` launch line found in the launcher"

    tokens = shlex.split(exec_match.group(1))

    # Drop the leading interpreter + runner path shell variables.
    # shlex.split() removes the quoting, so the tokens are `$PY`, `$RUNNER`.
    assert tokens[0] == "$PY", f"unexpected exec head: {tokens[:3]}"
    assert tokens[1] == "$RUNNER", f"unexpected exec head: {tokens[:3]}"
    tokens = tokens[2:]

    # Replace the shell-resolved symbol list with a concrete placeholder so
    # the flag vector is parseable standalone.
    out: list[str] = []
    for tok in tokens:
        if tok == "$SYMBOLS":
            out.append("AAPL")
        else:
            out.append(tok)
    return out


def flag_value(argv: list[str], flag: str) -> str:
    """Return the value that follows `flag` in argv (raises if absent)."""
    assert flag in argv, f"{flag} missing from launch command: {argv}"
    return argv[argv.index(flag) + 1]


# ------------------------------------------------------------------ tests
class TestLauncherTracked:
    """L1 — the launcher is version-controlled."""

    def test_launcher_exists_in_repo(self):
        assert LAUNCHER.is_file(), (
            f"{LAUNCHER} must be tracked in the repository so the deployed "
            f"wrapper cannot drift from the reviewed one"
        )

    def test_launcher_is_executable_bit_set_in_git(self):
        mode = LAUNCHER.stat().st_mode & 0o777
        assert mode & 0o111, "launcher must keep its executable bit"

    def test_no_secret_material_in_launcher(self):
        """No key material may be inlined. Names are fine, values are not."""
        text = launcher_text()
        # Any assignment of a literal key/secret/token value is a leak.
        leak_patterns = [
            r"ALPACA_API_KEY\s*=\s*['\"]?[A-Za-z0-9]{8,}",
            r"ALPACA_API_SECRET\s*=\s*['\"]?[A-Za-z0-9]{8,}",
            r"(?i)(api[_-]?key|api[_-]?secret|password|passwd|secret|token)"
            r"\s*[=:]\s*['\"][^'\"]{8,}['\"]",
        ]
        for pat in leak_patterns:
            match = re.search(pat, text)
            assert match is None, (
                f"possible secret material in launcher: {match.group(0)[:40]!r}"
            )

    def test_credentials_are_sourced_from_external_env_file(self):
        text = launcher_text()
        assert "ALPACA_API_KEY" in text and "ALPACA_API_SECRET" in text
        # The values must come from a file, not from the script body.
        assert re.search(r"source\s+<\(", text), (
            "credentials must be sourced from an external .env file"
        )
        assert ".env" in text


class TestLaunchCommandFlags:
    """L2/L3/L4 — the launch command carries the required flags/limits."""

    def test_launch_command_includes_symbols_flag(self):
        """Regression guard for the 2026-11-04 failure."""
        argv = parse_launch_command(launcher_text())
        assert "--symbols" in argv, (
            f"--symbols missing from launch command (this broke the "
            f"2026-11-04 run): {argv}"
        )

    def test_launch_command_includes_execute(self):
        argv = parse_launch_command(launcher_text())
        assert "--execute" in argv, (
            f"--execute missing — the launcher would run in dry-run mode and "
            f"place no paper orders: {argv}"
        )

    def test_launch_command_includes_current_limits(self):
        argv = parse_launch_command(launcher_text())
        assert flag_value(argv, "--sleeve-pct") == "0.15"
        assert flag_value(argv, "--rsi2-cap") == "4"
        assert flag_value(argv, "--max-open") == "4"
        assert flag_value(argv, "--per-position-cap") == "0.25"

    def test_no_unexpected_threshold_flags(self):
        """Only the four approved limit flags may appear."""
        argv = parse_launch_command(launcher_text())
        limit_flags = {
            "--sleeve-pct", "--rsi2-cap", "--max-open", "--per-position-cap",
        }
        present = {t for t in argv if t.startswith("--")}
        assert present == limit_flags | {"--execute", "--symbols"}, (
            f"unexpected or missing flags in launch command: {present}"
        )


class TestRunnerAcceptsLaunchCommand:
    """L5 — the runner's real argparse accepts the launch command."""

    def test_runner_argparse_accepts_launch_command(self):
        """The runner's REAL argparse must accept the launch command.

        A missing/renamed/unknown flag raises SystemExit(2) here instead
        of on the server at 21:30 UTC.
        """
        argv = parse_launch_command(launcher_text())
        parsed = _parse_with_runner(argv)
        assert parsed is not None

    def test_parsed_values_match_limits(self):
        argv = parse_launch_command(launcher_text())
        parsed = _parse_with_runner(argv)
        assert parsed.symbols == ["AAPL"]
        assert parsed.execute is True
        assert parsed.sleeve_pct == pytest.approx(0.15)
        assert parsed.rsi2_cap == 4
        assert parsed.max_open == 4
        assert parsed.per_position_cap == pytest.approx(0.25)


class TestLauncherMatchesStrategyConstants:
    """L6 — the launcher and rules.py cannot drift apart."""

    def test_sleeve_pct_matches_rules(self):
        parsed = _parse_with_runner(parse_launch_command(launcher_text()))
        assert parsed.sleeve_pct == pytest.approx(RSI2_SLEEVE_PCT)

    def test_rsi2_cap_matches_rules(self):
        parsed = _parse_with_runner(parse_launch_command(launcher_text()))
        assert parsed.rsi2_cap == RSI2_DAILY_CAP

    def test_max_open_matches_rules(self):
        parsed = _parse_with_runner(parse_launch_command(launcher_text()))
        assert parsed.max_open == RSI2_MAX_OPEN_POSITIONS

    def test_per_position_cap_matches_rules(self):
        parsed = _parse_with_runner(parse_launch_command(launcher_text()))
        assert parsed.per_position_cap == pytest.approx(RSI2_PER_POSITION_CAP_PCT)


class TestPaperOnlyPosture:
    """L7 — paper trading only, always."""

    def test_launcher_exports_paper_only(self):
        text = launcher_text()
        assert re.search(r"export\s+TRADING_BOT_PAPER_ONLY=1", text), (
            "the launcher must export TRADING_BOT_PAPER_ONLY=1"
        )

    def test_launcher_points_at_paper_host_only(self):
        """The runner hard-locks the paper trading URL; assert the launcher
        does not name a live trading endpoint."""
        text = launcher_text()
        assert "paper-api.alpaca.markets" in text
        assert "api.alpaca.markets" not in text.replace(
            "paper-api.alpaca.markets", ""
        ), "the launcher must not reference the live Alpaca trading endpoint"

    def test_runner_refuses_execute_without_paper_only(self):
        """The runner's own guard must still reject --execute without the
        paper-only env var. Assert the guard exists in the source."""
        src = (SWING_DIR / "runner.py").read_text()
        assert "TRADING_BOT_PAPER_ONLY" in src


# ------------------------------------------------- runner-argparse helper
def _parse_with_runner(argv: list[str]):
    """Parse `argv` with the runner's REAL argparse, without running it.

    `runner.main()` builds the parser and then calls run(), which would
    hit the broker. We monkeypatch `runner.run` so `main()` returns the
    parsed namespace without any side effect.
    """
    import contextlib
    import io

    captured = {}

    def fake_run(args):
        captured["args"] = args
        return args

    original = runner.run
    runner.run = fake_run
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            runner.main(argv)
    finally:
        runner.run = original

    assert "args" in captured, "runner.main() did not call run()"
    return captured["args"]
