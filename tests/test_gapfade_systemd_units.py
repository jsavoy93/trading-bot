"""GAPFADE-MIDCAP-PAPER — systemd unit TEMPLATE contract (not installed).

The committed units are templates only. These tests pin the two things a
future operator relies on:

- `ExecStart` runs from the checkout's own venv
  (`/root/strat-gapfade/.venv/bin/python`), not an ambient python3 that may
  lack the runner's dependencies;
- the paper guard env and the hard-locked paper URL contract stay in place,
  and nothing in the repo installs or enables a unit.
"""

from pathlib import Path

UNIT_DIR = Path(__file__).resolve().parents[1] / "experiments" / "gapfade_midcap_paper" / "systemd"
VENV_PYTHON = "/root/strat-gapfade/.venv/bin/python"

SERVICES = {
    "gapfade-midcap-morning.service": "morning",
    "gapfade-midcap-after-close.service": "after_close",
}


def _exec_start(name):
    for line in (UNIT_DIR / name).read_text().splitlines():
        if line.startswith("ExecStart="):
            return line[len("ExecStart="):].strip()
    raise AssertionError(f"{name}: no ExecStart")


def test_exec_start_uses_checkout_venv_python():
    for name, pass_name in SERVICES.items():
        cmd = _exec_start(name)
        parts = cmd.split()
        assert parts[0] == VENV_PYTHON, (name, cmd)
        assert parts[1:4] == ["-m", "experiments.gapfade_midcap_paper.runner", "--pass"]
        assert parts[4] == pass_name
        assert parts[5] == "--execute"


def test_exec_start_does_not_rely_on_env_python3():
    for name in SERVICES:
        assert "/usr/bin/env python3" not in _exec_start(name), name


def test_working_directory_and_paper_env_present():
    for name in SERVICES:
        txt = (UNIT_DIR / name).read_text()
        assert "WorkingDirectory=/root/strat-gapfade" in txt, name
        assert "Environment=TRADING_BOT_PAPER_ONLY=1" in txt, name
        assert "[Install]" not in txt, f"{name}: services must stay un-enableable"


def test_timers_target_their_service_and_nothing_installs_units():
    for timer, service in (
        ("gapfade-midcap-morning.timer", "gapfade-midcap-morning.service"),
        ("gapfade-midcap-after-close.timer", "gapfade-midcap-after-close.service"),
    ):
        txt = (UNIT_DIR / timer).read_text()
        assert f"Unit={service}" in txt, timer
        assert "Persistent=false" in txt, timer
    exp_dir = UNIT_DIR.parent
    assert not (exp_dir / "enable.sh").exists()
