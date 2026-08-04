from __future__ import annotations

from datetime import UTC, datetime
import io
import json
import os
from pathlib import Path
import signal

import pytest

from engineering.engineering_control import EngineeringControlService
from engineering.event_store import EngineeringEventStore
from engineering.telegram_adapter import AdapterBounds, PollResult
from engineering.telegram_service import (
    DEFAULT_MAX_POLLS,
    DEFAULT_MAX_SECONDS,
    EXIT_COMPETING_POLLER,
    EXIT_CONFIG,
    EXIT_PERMANENT_TELEGRAM,
    EXIT_RUNTIME,
    EXIT_SIGINT,
    EXIT_SIGTERM,
    EXIT_SUCCESS,
    NORMAL_EVENT_STORE,
    SMOKE_EVENT_STORE,
    ConfigurationError,
    SmokeDeadline,
    SmokeSequenceTracker,
    SmokeSignal,
    StructuredRuntimeLogger,
    load_secret_file,
    main,
    run_smoke,
    validate_smoke_path,
)
from engineering.telegram_transport import TelegramTransportError


NOW = datetime(2026, 8, 4, 5, 0, tzinfo=UTC)


class FakeAdapter:
    def __init__(self, actions=(), *, lease_acquired=True, event_sink=None) -> None:
        self.actions = list(actions)
        self.lease_acquired = lease_acquired
        self.runtime_event_sink = event_sink or (lambda event, fields: None)
        self.bounds = AdapterBounds(poll_timeout_seconds=30, consumer_lease_seconds=60)
        self.polls = 0
        self.timeouts = []
        self.shutdowns = 0

    def poll_once(self, *, poll_timeout_seconds=None):
        self.polls += 1
        self.timeouts.append(poll_timeout_seconds)
        action = self.actions.pop(0) if self.actions else None
        if isinstance(action, BaseException):
            raise action
        if callable(action):
            action()
        elif isinstance(action, list):
            for event, fields in action:
                self.runtime_event_sink(event, fields)
        return PollResult(0, 0, 0, self.lease_acquired)

    def shutdown(self):
        self.shutdowns += 1


class FakeTime:
    def __init__(self, values=None) -> None:
        self.values = iter(values or ())
        self.current = 0.0
        self.sleeps = []

    def monotonic(self):
        try:
            self.current = next(self.values)
        except StopIteration:
            pass
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += seconds


def runtime(tmp_path: Path, actions=(), *, lease_acquired=True, time=None):
    events = EngineeringEventStore(tmp_path / "events.sqlite3", clock=lambda: NOW)
    controls = EngineeringControlService(events, clock=lambda: NOW)
    output = io.StringIO()
    logger = StructuredRuntimeLogger(output, clock=lambda: NOW, worker_id="smoke-test")
    tracker = SmokeSequenceTracker(logger)
    adapter = FakeAdapter(actions, lease_acquired=lease_acquired, event_sink=tracker.observe)
    fake_time = time or FakeTime()
    return adapter, controls, tracker, logger, output, fake_time


def complete_sequence():
    return [
        ("authorized_command", {"command": "/status"}),
        ("authorized_command", {"command": "/current"}),
        ("authorized_command", {"command": "/report"}),
        ("authorized_command", {"command": "/pause"}),
        ("authorized_command", {"command": "/pause"}),
        ("authorized_command", {"command": "/resume"}),
        ("access_denied", {}),
    ]


def write_secret(path: Path, text: str, mode=0o600) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def test_secret_file_accepts_exact_two_keys_without_exporting_values(tmp_path: Path) -> None:
    secret = tmp_path / "ops-015.env"
    write_secret(
        secret,
        "ENGINEERING_TELEGRAM_BOT_TOKEN=123:test-token\n"
        "ENGINEERING_TELEGRAM_JOSH_CHAT_ID=42\n",
    )
    before = dict(os.environ)
    credentials = load_secret_file(secret, expected_uid=os.geteuid())
    assert credentials.token == "123:test-token" and credentials.chat_id == 42
    assert dict(os.environ) == before


@pytest.mark.parametrize(
    "content",
    (
        "",
        "ENGINEERING_TELEGRAM_BOT_TOKEN=token\n",
        "ENGINEERING_TELEGRAM_BOT_TOKEN=token\nENGINEERING_TELEGRAM_JOSH_CHAT_ID=42\nEXTRA=x\n",
        "export ENGINEERING_TELEGRAM_BOT_TOKEN=token\nENGINEERING_TELEGRAM_JOSH_CHAT_ID=42\n",
        "ENGINEERING_TELEGRAM_BOT_TOKEN=$(id)\nENGINEERING_TELEGRAM_JOSH_CHAT_ID=42\n",
        "ENGINEERING_TELEGRAM_BOT_TOKEN=token\nENGINEERING_TELEGRAM_BOT_TOKEN=again\n",
        "ENGINEERING_TELEGRAM_BOT_TOKEN=token value\nENGINEERING_TELEGRAM_JOSH_CHAT_ID=42\n",
        "ENGINEERING_TELEGRAM_BOT_TOKEN=token\nENGINEERING_TELEGRAM_JOSH_CHAT_ID=-1\n",
    ),
)
def test_secret_file_rejects_malformed_or_extra_content_without_echo(tmp_path: Path, content) -> None:
    secret = tmp_path / "ops-015.env"
    write_secret(secret, content)
    with pytest.raises(ConfigurationError) as caught:
        load_secret_file(secret, expected_uid=os.geteuid())
    assert "token" not in str(caught.value) and "$(id)" not in str(caught.value)


def test_secret_file_rejects_symlink_broad_mode_and_wrong_owner(tmp_path: Path) -> None:
    target = tmp_path / "target"
    write_secret(target, "ENGINEERING_TELEGRAM_BOT_TOKEN=token\nENGINEERING_TELEGRAM_JOSH_CHAT_ID=42\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ConfigurationError):
        load_secret_file(link)
    target.chmod(0o640)
    with pytest.raises(ConfigurationError, match="ownership or mode"):
        load_secret_file(target)
    target.chmod(0o600)
    with pytest.raises(ConfigurationError, match="ownership or mode"):
        load_secret_file(target, expected_uid=os.geteuid() + 1)


def test_smoke_path_is_exact_isolated_and_normal_path_is_rejected(tmp_path: Path) -> None:
    assert validate_smoke_path(tmp_path, SMOKE_EVENT_STORE) == (
        tmp_path / SMOKE_EVENT_STORE
    ).resolve()
    for path in (NORMAL_EVENT_STORE, Path("./.agent-state/../.agent-state/telegram-smoke-events.sqlite3")):
        with pytest.raises(ConfigurationError):
            validate_smoke_path(tmp_path, path)


def test_complete_required_sequence_exits_success_and_logs_only_safe_fields(tmp_path: Path) -> None:
    adapter, controls, tracker, logger, output, fake_time = runtime(
        tmp_path, [complete_sequence()]
    )
    outcome = run_smoke(
        adapter, controls, tracker, logger,
        monotonic=fake_time.monotonic, sleeper=fake_time.sleep,
    )
    assert outcome.exit_code == EXIT_SUCCESS and outcome.polls == 1
    assert tracker.complete and adapter.shutdowns == 1
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert records[-1]["event"] == "shutdown" and records[-1]["exit_code"] == 0
    assert [r.get("command") for r in records if "command" in r] == [
        "/status", "/current", "/report", "/pause", "/pause", "/resume"
    ]
    forbidden = ("chat_id", "sender_id", "message", "token", "url", "payload")
    assert all(not any(key in record for key in forbidden) for record in records)


def test_max_polls_stops_before_poll_21_without_restart_and_restores_pause(tmp_path: Path) -> None:
    adapter, controls, tracker, logger, _, fake_time = runtime(tmp_path)
    controls.pause()
    initial = controls.event_store.pause_state()["paused"]
    outcome = run_smoke(
        adapter, controls, tracker, logger,
        monotonic=fake_time.monotonic, sleeper=fake_time.sleep,
    )
    assert outcome.exit_code == EXIT_RUNTIME
    assert outcome.reason_code == "required_sequence_incomplete"
    assert adapter.polls == DEFAULT_MAX_POLLS and adapter.shutdowns == 1
    assert controls.event_store.pause_state()["paused"] is initial


def test_max_seconds_stops_when_time_bound_arrives_first_and_restores_pause(tmp_path: Path) -> None:
    fake_time = FakeTime([0, 0, 100, 200, 300])
    adapter, controls, tracker, logger, _, _ = runtime(tmp_path, time=fake_time)
    outcome = run_smoke(
        adapter, controls, tracker, logger,
        monotonic=fake_time.monotonic, sleeper=fake_time.sleep,
    )
    assert outcome.exit_code == EXIT_RUNTIME
    assert adapter.polls == 3 and adapter.shutdowns == 1
    assert controls.event_store.pause_state()["paused"] is False


@pytest.mark.parametrize(
    ("exception", "expected"),
    (
        (SmokeSignal(signal.SIGINT), EXIT_SIGINT),
        (SmokeSignal(signal.SIGTERM), EXIT_SIGTERM),
        (SmokeDeadline(), EXIT_RUNTIME),
        (TelegramTransportError("fixed", transient=False), EXIT_PERMANENT_TELEGRAM),
    ),
)
def test_terminal_paths_restore_pre_smoke_pause_state(tmp_path: Path, exception, expected) -> None:
    adapter, controls, tracker, logger, _, fake_time = runtime(tmp_path)
    controls.pause()
    adapter.actions = [lambda: controls.resume(), exception]
    outcome = run_smoke(
        adapter, controls, tracker, logger,
        monotonic=fake_time.monotonic, sleeper=fake_time.sleep,
    )
    assert outcome.exit_code == expected
    assert controls.event_store.pause_state()["paused"] is True
    assert adapter.shutdowns == 1


def test_competing_poller_exits_three_and_runs_cleanup(tmp_path: Path) -> None:
    adapter, controls, tracker, logger, _, fake_time = runtime(
        tmp_path, lease_acquired=False
    )
    outcome = run_smoke(
        adapter, controls, tracker, logger,
        monotonic=fake_time.monotonic, sleeper=fake_time.sleep,
    )
    assert outcome.exit_code == EXIT_COMPETING_POLLER
    assert outcome.reason_code == "competing_poller"
    assert adapter.polls == 1 and adapter.shutdowns == 1


def test_transient_error_retries_with_fake_sleep_and_remains_bounded(tmp_path: Path) -> None:
    adapter, controls, tracker, logger, _, fake_time = runtime(
        tmp_path,
        [TelegramTransportError("temporary", transient=True, retry_after=4), complete_sequence()],
    )
    outcome = run_smoke(
        adapter, controls, tracker, logger,
        monotonic=fake_time.monotonic, sleeper=fake_time.sleep,
    )
    assert outcome.exit_code == EXIT_SUCCESS and adapter.polls == 2
    assert fake_time.sleeps == [4]


def test_cleanup_failure_overrides_success(tmp_path: Path) -> None:
    adapter, controls, tracker, logger, _, fake_time = runtime(
        tmp_path, [complete_sequence()]
    )
    adapter.shutdown = lambda: (_ for _ in ()).throw(RuntimeError("failed"))
    outcome = run_smoke(
        adapter, controls, tracker, logger,
        monotonic=fake_time.monotonic, sleeper=fake_time.sleep,
    )
    assert outcome.exit_code == EXIT_RUNTIME and outcome.reason_code == "cleanup_failed"


def test_startup_logging_failure_still_runs_cleanup_and_returns_five(tmp_path: Path) -> None:
    adapter, controls, tracker, logger, _, fake_time = runtime(tmp_path)

    def failed_log(*args, **kwargs):
        raise RuntimeError("logger unavailable")

    logger.emit = failed_log
    outcome = run_smoke(
        adapter, controls, tracker, logger,
        monotonic=fake_time.monotonic, sleeper=fake_time.sleep,
    )
    assert outcome.exit_code == EXIT_RUNTIME
    assert adapter.polls == 0 and adapter.shutdowns == 1
    assert controls.event_store.pause_state()["paused"] is False


def test_isolated_store_does_not_open_or_modify_normal_database(tmp_path: Path) -> None:
    normal = tmp_path / NORMAL_EVENT_STORE
    normal.parent.mkdir(parents=True)
    normal.write_bytes(b"normal-database-canary")
    before = (normal.read_bytes(), normal.stat().st_mtime_ns)
    smoke = EngineeringEventStore(validate_smoke_path(tmp_path, SMOKE_EVENT_STORE), clock=lambda: NOW)
    controls = EngineeringControlService(smoke, clock=lambda: NOW)
    controls.pause()
    controls.resume()
    assert (normal.read_bytes(), normal.stat().st_mtime_ns) == before
    assert smoke.path.name == "telegram-smoke-events.sqlite3"


def test_cli_rejects_any_non_exact_setup_before_network_or_state(monkeypatch, tmp_path: Path) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("network or event store must not be constructed")

    monkeypatch.setattr("engineering.telegram_service.TelegramHTTPTransport", forbidden)
    monkeypatch.setattr("engineering.telegram_service.EngineeringEventStore", forbidden)
    assert main([
        "--env-file", str(tmp_path / "secret"), "--smoke",
        "--event-store", str(SMOKE_EVENT_STORE),
        "--max-polls", "20", "--max-seconds", "300",
    ]) == EXIT_CONFIG
    assert main([
        "--env-file", "/etc/trading-bot/ops-015.env", "--smoke",
        "--event-store", str(NORMAL_EVENT_STORE),
        "--max-polls", "20", "--max-seconds", "300",
    ]) == EXIT_CONFIG


def test_structured_logger_rejects_unknown_fields_and_unsafe_commands() -> None:
    logger = StructuredRuntimeLogger(io.StringIO(), clock=lambda: NOW, worker_id="worker")
    with pytest.raises(RuntimeError, match="allowlisted"):
        logger.emit("event", token="canary")
    with pytest.raises(RuntimeError, match="command"):
        logger.emit("event", command="/shell")
