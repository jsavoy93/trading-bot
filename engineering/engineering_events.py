from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
import json
from typing import Mapping


EVENT_SCHEMA_VERSION = 1
MAX_EVENT_STRING_CHARS = 2_000
MAX_EVENT_LIST_ITEMS = 100
MAX_EVENT_PAYLOAD_BYTES = 16_384


class EventType(str, Enum):
    WORKFLOW_TRANSITION = "workflow.transition"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    WORKFLOW_BLOCKED = "workflow.blocked"
    WORKFLOW_STALE = "workflow.stale"
    PR_READY = "pr.ready"
    APPROVAL_REQUIRED = "approval.required"
    DELEGATION_STATUS = "delegation.status"
    QA_RESULT = "qa.result"
    REPORT_GENERATED = "report.generated"
    MANAGER_PAUSED = "manager.paused"
    MANAGER_RESUMED = "manager.resumed"
    TELEGRAM_ACCESS_DENIED = "telegram.access_denied"
    SUPERVISOR_AUTO_DISPATCH_ATTEMPT = "supervisor.auto_dispatch_attempt"


class EventSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


NOTIFICATION_EVENT_TYPES = frozenset(
    {
        EventType.TASK_COMPLETED,
        EventType.TASK_FAILED,
        EventType.WORKFLOW_BLOCKED,
        EventType.WORKFLOW_STALE,
        EventType.PR_READY,
        EventType.APPROVAL_REQUIRED,
    }
)


_ALLOWED_PAYLOAD_FIELDS = frozenset(
    {
        "state",
        "previous_state",
        "feature_branch",
        "agent_name",
        "delegation_status",
        "exit_code",
        "failure_reason",
        "passed_count",
        "failed_count",
        "timed_out",
        "recommendation",
        "next_action",
        "stop_reason",
        "continuity",
        "report_generated_at",
        "pr_url",
        "paused",
        "actor",
        "reason",
        "revision",
    }
)


@dataclass(frozen=True)
class EngineeringEvent:
    event_id: str
    event_type: EventType
    severity: EventSeverity
    occurred_at: str
    task_id: str
    payload: dict[str, object]
    workflow_id: str = ""
    request_id: str = ""
    run_id: str = ""
    causation_id: str = ""
    correlation_id: str = ""
    schema_version: int = EVENT_SCHEMA_VERSION


def sanitize_payload(payload: Mapping[str, object]) -> dict[str, object]:
    unknown = set(payload) - _ALLOWED_PAYLOAD_FIELDS
    if unknown:
        raise ValueError(f"Event payload contains unsupported fields: {sorted(unknown)}")

    sanitized: dict[str, object] = {}
    for key, value in payload.items():
        if value is None or isinstance(value, (bool, int, float)):
            sanitized[key] = value
        elif isinstance(value, str):
            sanitized[key] = value[:MAX_EVENT_STRING_CHARS]
        elif isinstance(value, (tuple, list)):
            if len(value) > MAX_EVENT_LIST_ITEMS or not all(
                isinstance(item, str) for item in value
            ):
                raise ValueError(f"Event payload field {key!r} is not a bounded string list")
            sanitized[key] = [item[:MAX_EVENT_STRING_CHARS] for item in value]
        else:
            raise ValueError(f"Event payload field {key!r} has an unsupported type")

    encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_EVENT_PAYLOAD_BYTES:
        raise ValueError("Event payload exceeds the bounded size")
    return sanitized


def build_event(
    event_type: EventType,
    *,
    occurred_at: str,
    task_id: str,
    identity: str,
    payload: Mapping[str, object],
    severity: EventSeverity = EventSeverity.INFO,
    workflow_id: str = "",
    request_id: str = "",
    run_id: str = "",
    causation_id: str = "",
    correlation_id: str = "",
) -> EngineeringEvent:
    try:
        parsed_time = datetime.fromisoformat(occurred_at)
    except ValueError as exc:
        raise ValueError("Event occurrence time must be ISO-8601") from exc
    if parsed_time.tzinfo is None or parsed_time.astimezone(UTC).utcoffset() is None:
        raise ValueError("Event occurrence time must be timezone-aware")
    occurred_at = parsed_time.astimezone(UTC).isoformat()
    for label, value in (
        ("task ID", task_id),
        ("identity", identity),
        ("workflow ID", workflow_id),
        ("request ID", request_id),
        ("run ID", run_id),
    ):
        if len(value) > MAX_EVENT_STRING_CHARS:
            raise ValueError(f"Event {label} exceeds the bounded size")
    clean_payload = sanitize_payload(payload)
    digest_input = json.dumps(
        {
            "schema": EVENT_SCHEMA_VERSION,
            "type": event_type.value,
            "task": task_id,
            "workflow": workflow_id,
            "identity": identity,
            "payload": clean_payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    event_id = f"evt-{sha256(digest_input).hexdigest()[:32]}"
    return EngineeringEvent(
        event_id=event_id,
        event_type=event_type,
        severity=severity,
        occurred_at=occurred_at,
        task_id=task_id,
        payload=clean_payload,
        workflow_id=workflow_id,
        request_id=request_id,
        run_id=run_id,
        causation_id=causation_id,
        correlation_id=correlation_id or workflow_id,
    )
