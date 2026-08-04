from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import signal
import stat
import sys
import time
from typing import Callable, Mapping, TextIO

from engineering.engineering_control import EngineeringControlService
from engineering.event_store import EngineeringEventStore
from engineering.query_service import EngineeringQueryService
from engineering.telegram_adapter import AdapterBounds, TelegramEngineeringAdapter
from engineering.telegram_transport import (
    TelegramHTTPTransport,
    TelegramTransportError,
    telegram_credentials_from_env,
)
from engineering.workflow_store import WorkflowStore


SECRET_FILE = Path("/etc/trading-bot/ops-015.env")
SMOKE_EVENT_STORE = Path(".agent-state/telegram-smoke-events.sqlite3")
NORMAL_EVENT_STORE = Path(".agent-state/engineering-events.sqlite3")
DEFAULT_MAX_POLLS = 20
DEFAULT_MAX_SECONDS = 300
MAX_SECRET_FILE_BYTES = 1_024

EXIT_SUCCESS = 0
EXIT_CONFIG = 2
EXIT_COMPETING_POLLER = 3
EXIT_PERMANENT_TELEGRAM = 4
EXIT_RUNTIME = 5
EXIT_SIGINT = 130
EXIT_SIGTERM = 143

LOG_SCHEMA_VERSION = 1
_LOG_FIELDS = frozenset(
    {
        "schema_version",
        "timestamp",
        "severity",
        "component",
        "event",
        "worker_id",
        "poll_count",
        "delivery_status",
        "exit_code",
        "reason_code",
        "command",
    }
)
_SAFE_COMMANDS = frozenset(
    {"/status", "/current", "/report", "/pause", "/resume"}
)
_REQUIRED_SEQUENCE = (
    ("authorized_command", "/status"),
    ("authorized_command", "/current"),
    ("authorized_command", "/report"),
    ("authorized_command", "/pause"),
    ("authorized_command", "/pause"),
    ("authorized_command", "/resume"),
    ("access_denied", ""),
)


class ConfigurationError(RuntimeError):
    pass


class SmokeSignal(RuntimeError):
    def __init__(self, signum: int) -> None:
        super().__init__("smoke interrupted")
        self.signum = signum


class SmokeDeadline(RuntimeError):
    pass


@dataclass(frozen=True)
class SmokeCredentials:
    token: str
    chat_id: int


@dataclass(frozen=True)
class SmokeOutcome:
    exit_code: int
    reason_code: str
    polls: int


class StructuredRuntimeLogger:
    def __init__(
        self,
        stream: TextIO,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        worker_id: str,
    ) -> None:
        self.stream = stream
        self.clock = clock
        self.worker_id = worker_id

    def emit(self, event: str, *, severity: str = "INFO", **fields: object) -> None:
        if set(fields) - _LOG_FIELDS:
            raise RuntimeError("Structured log fields are not allowlisted")
        timestamp = self.clock()
        if timestamp.tzinfo is None:
            raise RuntimeError("Structured log clock must be timezone-aware")
        record: dict[str, object] = {
            "schema_version": LOG_SCHEMA_VERSION,
            "timestamp": timestamp.astimezone(UTC).isoformat(),
            "severity": severity,
            "component": "telegram_smoke",
            "event": _safe_log_value(event, 80),
            "worker_id": _safe_log_value(self.worker_id, 80),
        }
        for key, value in fields.items():
            if key == "command" and value not in _SAFE_COMMANDS:
                raise RuntimeError("Structured log command is not allowlisted")
            record[key] = (
                value
                if isinstance(value, (bool, int, float))
                else _safe_log_value(value, 120)
            )
        if set(record) - _LOG_FIELDS:
            raise RuntimeError("Structured log record is not allowlisted")
        try:
            self.stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            self.stream.flush()
        except OSError as exc:
            raise RuntimeError("Structured runtime logging failed") from exc


def _safe_log_value(value: object, limit: int) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").replace("\x00", "")
    return " ".join(text.split())[:limit]


class SmokeSequenceTracker:
    def __init__(self, logger: StructuredRuntimeLogger) -> None:
        self.logger = logger
        self.position = 0

    @property
    def complete(self) -> bool:
        return self.position == len(_REQUIRED_SEQUENCE)

    def observe(self, event: str, fields: Mapping[str, object]) -> None:
        safe_fields: dict[str, object] = {}
        for key in ("command", "event_type", "delivery_status", "reason_code"):
            if key in fields:
                mapped = "delivery_status" if key == "event_type" else key
                safe_fields[mapped] = fields[key]
        self.logger.emit(event, **safe_fields)
        if self.complete:
            return
        expected_event, expected_command = _REQUIRED_SEQUENCE[self.position]
        command = fields.get("command", "")
        if event == expected_event and command == expected_command:
            self.position += 1


def load_secret_file(
    path: Path,
    *,
    expected_uid: int | None = None,
) -> SmokeCredentials:
    expected_uid = os.geteuid() if expected_uid is None else expected_uid
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigurationError("Telegram secret file is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigurationError("Telegram secret path is not a regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != expected_uid:
            raise ConfigurationError("Telegram secret file ownership or mode is unsafe")
        raw = os.read(descriptor, MAX_SECRET_FILE_BYTES + 1)
        if len(raw) > MAX_SECRET_FILE_BYTES:
            raise ConfigurationError("Telegram secret file exceeds the bounded size")
    finally:
        os.close(descriptor)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError("Telegram secret file is not valid UTF-8") from exc
    values: dict[str, str] = {}
    allowed = {
        "ENGINEERING_TELEGRAM_BOT_TOKEN",
        "ENGINEERING_TELEGRAM_JOSH_CHAT_ID",
    }
    lines = text.splitlines()
    if len(lines) != 2:
        raise ConfigurationError("Telegram secret file must contain exactly two keys")
    for line in lines:
        if not line or line.startswith(("#", "export ")) or line.count("=") != 1:
            raise ConfigurationError("Telegram secret file syntax is invalid")
        key, value = line.split("=", 1)
        if key not in allowed or key in values or not value:
            raise ConfigurationError("Telegram secret file keys are invalid")
        if any(token in value for token in ("$", "`", "$(", "${")):
            raise ConfigurationError("Telegram secret file syntax is invalid")
        values[key] = value
    if set(values) != allowed:
        raise ConfigurationError("Telegram secret file keys are incomplete")
    try:
        token, chat_id = telegram_credentials_from_env(values)
    except ValueError as exc:
        raise ConfigurationError("Telegram credentials are invalid") from exc
    return SmokeCredentials(token, chat_id)


def validate_smoke_path(repo_root: Path, candidate: Path) -> Path:
    if candidate.as_posix() != SMOKE_EVENT_STORE.as_posix():
        raise ConfigurationError("Smoke event-store path must use the isolated location")
    resolved = (repo_root / candidate).resolve(strict=False)
    expected = (repo_root / SMOKE_EVENT_STORE).resolve(strict=False)
    normal = (repo_root / NORMAL_EVENT_STORE).resolve(strict=False)
    if resolved != expected or resolved == normal:
        raise ConfigurationError("Smoke event-store path is unsafe")
    return resolved


def _bound_outcome(tracker: SmokeSequenceTracker, polls: int, reason: str) -> SmokeOutcome:
    if tracker.complete:
        return SmokeOutcome(EXIT_SUCCESS, reason, polls)
    return SmokeOutcome(EXIT_RUNTIME, "required_sequence_incomplete", polls)


def run_smoke(
    adapter: TelegramEngineeringAdapter,
    control_service: EngineeringControlService,
    tracker: SmokeSequenceTracker,
    logger: StructuredRuntimeLogger,
    *,
    max_polls: int = DEFAULT_MAX_POLLS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> SmokeOutcome:
    if max_polls != DEFAULT_MAX_POLLS or max_seconds != DEFAULT_MAX_SECONDS:
        raise ConfigurationError("Smoke bounds must be exactly 20 polls and 300 seconds")
    started = monotonic()
    polls = 0
    failures = 0
    outcome = SmokeOutcome(EXIT_RUNTIME, "runtime_failure", 0)
    cleanup_failed = False
    initial_pause: bool | None = None
    try:
        initial_pause = bool(control_service.event_store.pause_state()["paused"])
        logger.emit("startup", poll_count=0, reason_code="foreground_smoke")
        while True:
            elapsed = max(0.0, monotonic() - started)
            if polls >= max_polls:
                outcome = _bound_outcome(tracker, polls, "max_polls_reached")
                break
            if elapsed >= max_seconds:
                outcome = _bound_outcome(tracker, polls, "max_seconds_reached")
                break
            remaining = max_seconds - elapsed
            poll_timeout = min(
                adapter.bounds.poll_timeout_seconds,
                max(1, int(math.floor(remaining))),
            )
            logger.emit("poll_started", poll_count=polls + 1)
            try:
                result = adapter.poll_once(poll_timeout_seconds=poll_timeout)
            except SmokeDeadline:
                outcome = _bound_outcome(tracker, polls, "max_seconds_reached")
                break
            except SmokeSignal as exc:
                code = EXIT_SIGINT if exc.signum == signal.SIGINT else EXIT_SIGTERM
                outcome = SmokeOutcome(code, "signal_received", polls)
                break
            except TelegramTransportError as exc:
                polls += 1
                if not exc.transient:
                    outcome = SmokeOutcome(
                        EXIT_PERMANENT_TELEGRAM, "permanent_telegram_error", polls
                    )
                    break
                failures += 1
                delay = exc.retry_after or float(2 ** min(failures - 1, 8))
                remaining = max(0.0, max_seconds - (monotonic() - started))
                sleeper(min(300.0, max(0.0, delay), remaining))
                logger.emit(
                    "poll_retry",
                    severity="WARNING",
                    poll_count=polls,
                    reason_code="transient_telegram_error",
                )
                continue
            polls += 1
            failures = 0
            logger.emit("poll_completed", poll_count=polls)
            if not result.lease_acquired:
                outcome = SmokeOutcome(
                    EXIT_COMPETING_POLLER, "competing_poller", polls
                )
                break
            if tracker.complete:
                outcome = SmokeOutcome(EXIT_SUCCESS, "required_sequence_complete", polls)
                break
    except SmokeSignal as exc:
        code = EXIT_SIGINT if exc.signum == signal.SIGINT else EXIT_SIGTERM
        outcome = SmokeOutcome(code, "signal_received", polls)
    except Exception:
        outcome = SmokeOutcome(EXIT_RUNTIME, "runtime_failure", polls)
    finally:
        try:
            adapter.shutdown()
            if initial_pause is not None:
                current_pause = bool(control_service.event_store.pause_state()["paused"])
                if current_pause != initial_pause:
                    if initial_pause:
                        control_service.pause()
                    else:
                        control_service.resume()
                restored = bool(control_service.event_store.pause_state()["paused"])
                if restored != initial_pause:
                    raise RuntimeError("Pre-smoke pause state was not restored")
            logger.emit("cleanup_complete", poll_count=polls)
        except Exception:
            cleanup_failed = True
    if cleanup_failed:
        outcome = SmokeOutcome(EXIT_RUNTIME, "cleanup_failed", polls)
    try:
        logger.emit(
            "shutdown",
            severity="INFO" if outcome.exit_code == 0 else "ERROR",
            poll_count=polls,
            exit_code=outcome.exit_code,
            reason_code=outcome.reason_code,
        )
    except Exception:
        outcome = SmokeOutcome(EXIT_RUNTIME, "logging_failed", polls)
    return outcome


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded OPS-015 Telegram smoke launcher")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true", required=True)
    parser.add_argument("--event-store", type=Path, required=True)
    parser.add_argument("--max-polls", type=int, required=True)
    parser.add_argument("--max-seconds", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.env_file != SECRET_FILE or not args.smoke:
            raise ConfigurationError("Smoke launcher arguments are invalid")
        if args.max_polls != DEFAULT_MAX_POLLS or args.max_seconds != DEFAULT_MAX_SECONDS:
            raise ConfigurationError("Smoke launcher bounds are invalid")
        repo_root = Path.cwd().resolve()
        if not (repo_root / "AGENT_BACKLOG.md").is_file():
            raise ConfigurationError("Smoke launcher must run from the repository root")
        event_path = validate_smoke_path(repo_root, args.event_store)
        credentials = load_secret_file(args.env_file)
    except (ConfigurationError, SystemExit):
        return EXIT_CONFIG

    worker_id = f"smoke-{os.getpid()}"
    logger = StructuredRuntimeLogger(sys.stderr, worker_id=worker_id)
    tracker = SmokeSequenceTracker(logger)
    try:
        event_store = EngineeringEventStore(event_path)
        workflow_store = WorkflowStore(
            repo_root / ".git" / "engineering-workflow.json",
            event_store=event_store,
        )
        query_service = EngineeringQueryService(
            event_store=event_store,
            workflow_store=workflow_store,
            backlog_path=repo_root / "AGENT_BACKLOG.md",
        )
        control_service = EngineeringControlService(event_store)
        transport = TelegramHTTPTransport(credentials.token)
        adapter = TelegramEngineeringAdapter(
            authorized_chat_id=credentials.chat_id,
            transport=transport,
            query_service=query_service,
            control_service=control_service,
            event_store=event_store,
            worker_id=worker_id,
            bounds=AdapterBounds(
                poll_timeout_seconds=30,
                consumer_lease_seconds=60,
                max_run_polls=DEFAULT_MAX_POLLS,
            ),
            runtime_event_sink=tracker.observe,
        )
    except Exception:
        logger.emit(
            "shutdown", severity="ERROR", poll_count=0,
            exit_code=EXIT_RUNTIME, reason_code="startup_failure",
        )
        return EXIT_RUNTIME

    previous_handlers = {
        signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    previous_alarm = signal.getsignal(signal.SIGALRM)

    def interrupt(signum, frame):
        raise SmokeSignal(signum)

    def deadline(signum, frame):
        raise SmokeDeadline()

    try:
        signal.signal(signal.SIGINT, interrupt)
        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, DEFAULT_MAX_SECONDS)
        return run_smoke(adapter, control_service, tracker, logger).exit_code
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        signal.signal(signal.SIGALRM, previous_alarm)


if __name__ == "__main__":
    raise SystemExit(main())
