"""Tests for scripts/dispatch_agent.sh (ENG-02).

Proves a message containing pipes, single+double quotes, $, backticks, and
newlines round-trips UNCHANGED through the --dry-run/echo path, and that
missing/empty message files are rejected. NEVER dispatches a real agent.
"""

import os
import re
import subprocess
import textwrap

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "dispatch_agent.sh")

BEGIN = "-----BEGIN MESSAGE-----"
END = "-----END MESSAGE-----"


def run_script(*args):
    return subprocess.run(
        ["bash", SCRIPT, *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def make_message_file(tmp_path, content):
    p = tmp_path / "message.txt"
    p.write_text(content, encoding="utf-8")
    return str(p)


HOSTILE_MESSAGE = textwrap.dedent(
    """\
    Task: analyze `backtest.py` output | grep -v HOLD
    Quote types: 'single' and "double" and '$HOME' and "$(whoami)"
    Backtick: `echo hi` and dollar: $HOME ${VAR} $(date)
    Line1 with pipe | ; Line2
    Done.
    """
)


def extract_message(stdout):
    """Return the raw bytes between the markers.

    The helper emits the message verbatim and adds a single separator newline
    only when the message does not already end with one, so the END marker
    starts on its own line.
    """
    assert BEGIN in stdout and END in stdout, stdout
    body = stdout.split(BEGIN, 1)[1].split(END, 1)[0]
    assert body.startswith("\n")
    return body[1:]


def expected_body(message):
    return message if message.endswith("\n") else message + "\n"


def message_sha(stdout):
    import hashlib
    m = re.search(r"message sha256: ([0-9a-f]{64})", stdout)
    assert m, stdout
    return m.group(1)


def test_dry_run_preserves_hostile_message_unchanged(tmp_path):
    msg_file = make_message_file(tmp_path, HOSTILE_MESSAGE)
    r = run_script("--dry-run", "trading-exec", "ENG-02-TEST", "zai/glm-5.1", msg_file)
    assert r.returncode == 0, r.stderr
    received = extract_message(r.stdout)
    # exact byte-for-byte comparison, including the trailing newline
    assert received == expected_body(HOSTILE_MESSAGE)
    import hashlib
    assert message_sha(r.stdout) == hashlib.sha256(
        HOSTILE_MESSAGE.encode("utf-8")
    ).hexdigest()


def test_echo_mode_matches_dry_run(tmp_path):
    msg_file = make_message_file(tmp_path, HOSTILE_MESSAGE)
    a = run_script("--echo", "a1", "T1", "m1", msg_file)
    assert a.returncode == 0, a.stderr
    assert extract_message(a.stdout) == expected_body(HOSTILE_MESSAGE)


def test_dry_run_prints_exact_command_and_fresh_session_key(tmp_path):
    msg_file = make_message_file(tmp_path, "hello")
    r = run_script("--dry-run", "trading-exec", "ENG-02", "zai/glm-5.1", msg_file)
    assert r.returncode == 0, r.stderr
    assert re.search(r"agent:trading-exec:ENG-02-\d{10,}", r.stdout), r.stdout
    assert "nohup openclaw agent" in r.stdout
    assert "--session-key" in r.stdout
    assert "--model" in r.stdout
    assert "--json" in r.stdout
    assert "--timeout 3600" in r.stdout
    assert "_run.log" in r.stdout


def test_session_keys_differ_between_invocations(tmp_path):
    msg_file = make_message_file(tmp_path, "hello")
    r1 = run_script("--dry-run", "ag", "TASKX", "m", msg_file)
    r2 = run_script("--dry-run", "ag", "TASKX", "m", msg_file)
    assert r1.returncode == 0 and r2.returncode == 0
    k1 = re.search(r"session key: (\S+)", r1.stdout).group(1)
    k2 = re.search(r"session key: (\S+)", r2.stdout).group(1)
    assert k1 != k2


def test_missing_file_rejected():
    r = run_script("--dry-run", "ag", "TASK", "m", "/nonexistent/nope.txt")
    assert r.returncode != 0
    assert "not found" in r.stderr


def test_empty_file_rejected(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("")
    r = run_script("--dry-run", "ag", "TASK", "m", str(p))
    assert r.returncode != 0
    assert "empty" in r.stderr


def test_directory_rejected(tmp_path):
    r = run_script("--dry-run", "ag", "TASK", "m", str(tmp_path))
    assert r.returncode != 0


def test_message_without_trailing_newline_is_preserved(tmp_path):
    msg = "no trailing newline | 'q' \"d\" $X `cmd`"
    msg_file = make_message_file(tmp_path, msg)
    r = run_script("--dry-run", "ag", "TASK", "m", msg_file)
    assert r.returncode == 0, r.stderr
    assert extract_message(r.stdout) == expected_body(msg)
    import hashlib
    assert message_sha(r.stdout) == hashlib.sha256(msg.encode("utf-8")).hexdigest()


def test_wrong_arg_count_rejected():
    r = run_script("--dry-run", "only-one-arg")
    assert r.returncode == 2


def test_usage_documents_engineering_manager_as_code_work_default():
    r = run_script()
    assert r.returncode == 2, r.stderr
    combined = r.stdout + r.stderr
    assert "engineering-manager" in combined
    assert "default target for CODE work is engineering-manager" in combined
    canonical = (
        "scripts/dispatch_agent.sh engineering-manager <TASKID> "
        "minimax-portal/MiniMax-M3 /tmp/<taskid>-brief.txt"
    )
    assert canonical in combined


def test_usage_documents_trading_manager_as_research_alternative():
    r = run_script()
    assert r.returncode == 2, r.stderr
    combined = r.stdout + r.stderr
    assert "trading-manager" in combined
    assert "RESEARCH work" in combined
    canonical = (
        "scripts/dispatch_agent.sh trading-manager <TASKID> "
        "minimax-portal/MiniMax-M3 /tmp/<taskid>-brief.txt"
    )
    assert canonical in combined


def test_agent_id_argument_is_still_required_no_default_substituted(tmp_path):
    msg_file = make_message_file(tmp_path, "hello")
    r = run_script("--dry-run", "TASK", "m", msg_file)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "dispatched agent=" not in r.stdout
    assert "engineering-manager" not in r.stdout


def test_explicit_trading_manager_target_dry_run_command(tmp_path):
    msg_file = make_message_file(tmp_path, "research brief")
    r = run_script(
        "--dry-run", "trading-manager", "ENG-11-TEST", "minimax-portal/MiniMax-M3", msg_file
    )
    assert r.returncode == 0, r.stderr
    assert re.search(
        r"session key: agent:trading-manager:ENG-11-TEST-\d{10,}", r.stdout
    ), r.stdout
    assert "--agent trading-manager" in r.stdout
    assert "--session-key agent:trading-manager:ENG-11-TEST-" in r.stdout
    assert "dispatched agent=" not in r.stdout
    assert "DRY-RUN" in r.stdout


def test_dry_run_never_dispatches(tmp_path):
    msg_file = make_message_file(tmp_path, "hello")
    r = run_script("--dry-run", "ag", "TASK", "m", msg_file)
    assert r.returncode == 0
    assert "dispatched agent=" not in r.stdout
    assert "DRY-RUN" in r.stdout
