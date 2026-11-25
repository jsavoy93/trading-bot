"""GAPFADE-MIDCAP-PAPER — paper-only guards (guards.py) + CLI gating."""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP = PROJECT_ROOT / "experiments" / "gapfade_midcap_paper"
sys.path.insert(0, str(PROJECT_ROOT))
# Import ONLY via the package path: never put the experiment dir itself on
# sys.path, or a bare `rules`/`guards`/`runner` module would shadow the
# other experiments' same-named modules (e.g. experiments/swing_paper/rules.py).
from experiments.gapfade_midcap_paper import rules  # noqa: E402

from experiments.gapfade_midcap_paper import guards  # noqa: E402
from experiments.gapfade_midcap_paper import runner  # noqa: E402

RUNNER = EXP / "runner.py"
PAPER = "https://paper-api.alpaca.markets"


@pytest.fixture()
def kill_file(tmp_path):
    return tmp_path / "KILL_GAPFADE_MIDCAP_PAPER"


def test_hard_locked_url_is_paper_endpoint():
    assert guards.PAPER_API_URL == PAPER
    assert guards.trading_base_url() == PAPER


def test_guard_requires_paper_only_env(kill_file):
    with pytest.raises(guards.GuardError, match="TRADING_BOT_PAPER_ONLY"):
        guards.check_paper_only(env={}, kill_file=kill_file)
    with pytest.raises(guards.GuardError):
        guards.check_paper_only(env={"TRADING_BOT_PAPER_ONLY": "0"},
                                kill_file=kill_file)


@pytest.mark.parametrize("url", [
    "https://api.alpaca.markets",
    "https://api.alpaca.markets/",
    "https://paper-api.alpaca.markets/",   # trailing slash is NOT exact
    "http://paper-api.alpaca.markets",
    "https://paper-api.alpaca.markets.evil.example",
    "",
])
def test_guard_refuses_any_non_exact_url(kill_file, url):
    with pytest.raises(guards.GuardError, match="must be exactly"):
        guards.check_paper_only(env={"TRADING_BOT_PAPER_ONLY": "1"},
                                base_url=url, kill_file=kill_file)


def test_guard_passes_with_env_url_and_no_kill_file(kill_file):
    guards.check_paper_only(env={"TRADING_BOT_PAPER_ONLY": "1"},
                            kill_file=kill_file)


def test_kill_switch_blocks_all_orders(kill_file):
    kill_file.write_text("stop")
    with pytest.raises(guards.GuardError, match="KILL SWITCH"):
        guards.check_paper_only(env={"TRADING_BOT_PAPER_ONLY": "1"},
                                kill_file=kill_file)


def test_kill_file_env_override_is_honoured(tmp_path, monkeypatch):
    kill = tmp_path / "alt-kill"
    kill.write_text("stop")
    monkeypatch.setenv(guards.KILL_FILE_ENV, str(kill))
    assert guards.kill_switch_path() == kill
    with pytest.raises(guards.GuardError, match="KILL SWITCH"):
        guards.check_paper_only(env={"TRADING_BOT_PAPER_ONLY": "1"})


# --- CLI: exit code 2 without the paper env ------------------------------
def _cli(args, env_extra=None, unset_paper_env=True):
    import os
    env = dict(os.environ)
    if unset_paper_env:
        env.pop("TRADING_BOT_PAPER_ONLY", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-B", str(RUNNER)] + args,
                          cwd=str(PROJECT_ROOT), env=env,
                          capture_output=True, text=True, timeout=120)


def test_cli_execute_without_paper_env_exits_2(tmp_path):
    res = _cli(["--execute", "--inputs", str(tmp_path / "nope.json")])
    assert res.returncode == 2, res.stderr
    assert "TRADING_BOT_PAPER_ONLY" in res.stderr


def test_cli_execute_with_kill_switch_exits_2(tmp_path):
    kill = tmp_path / "kill"
    kill.write_text("x")
    res = _cli(["--execute", "--inputs", str(tmp_path / "nope.json")],
               {"TRADING_BOT_PAPER_ONLY": "1",
                guards.KILL_FILE_ENV: str(kill)})
    assert res.returncode == 2, res.stderr
    assert "KILL SWITCH" in res.stderr


def test_cli_execute_and_dry_run_conflict_exits_2():
    res = _cli(["--execute", "--dry-run"])
    assert res.returncode == 2


def test_run_cli_returns_2_when_guard_fails(monkeypatch, tmp_path):
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    monkeypatch.setenv(guards.KILL_FILE_ENV, str(tmp_path / "kill"))
    called = []

    def fake_main(execute, argv):
        called.append(execute)
        return 0

    assert guards.run_cli(fake_main, ["--execute"]) == 2
    assert called == []                     # never reached the main path
