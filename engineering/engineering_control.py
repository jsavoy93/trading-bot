from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from engineering.engineering_events import EventType, build_event
from engineering.event_store import EngineeringEventStore


@dataclass(frozen=True)
class ControlResult:
    paused: bool
    revision: int
    changed: bool
    updated_at: str


class EngineeringControlService:
    """Narrow manager-dispatch pause control; it cannot control processes or trading."""

    def __init__(
        self,
        event_store: EngineeringEventStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.event_store = event_store
        self.clock = clock

    def pause(self, *, actor: str = "josh") -> ControlResult:
        return self._set(True, actor=actor)

    def resume(self, *, actor: str = "josh") -> ControlResult:
        return self._set(False, actor=actor)

    def _set(self, paused: bool, *, actor: str) -> ControlResult:
        if actor != "josh":
            raise ValueError("Engineering control actor is not authorized")
        before = self.event_store.pause_state()
        if before["paused"] is paused:
            return ControlResult(
                paused=paused,
                revision=int(before["revision"]),
                changed=False,
                updated_at=str(before["updated_at"]),
            )
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("Engineering control clock must be timezone-aware")
        revision = int(before["revision"]) + 1
        action = "pause" if paused else "resume"
        event = build_event(
            EventType.MANAGER_PAUSED if paused else EventType.MANAGER_RESUMED,
            occurred_at=now.astimezone(UTC).isoformat(),
            task_id="OPS-015",
            identity=f"telegram-{action}-revision-{revision}",
            payload={
                "paused": paused,
                "actor": actor,
                "reason": f"Telegram /{action}",
                "revision": revision,
            },
        )
        state = self.event_store.set_paused_with_event(
            paused,
            expected_revision=int(before["revision"]),
            actor=actor,
            reason=f"Telegram /{action}",
            event=event,
        )
        return ControlResult(
            paused=bool(state["paused"]),
            revision=int(state["revision"]),
            changed=True,
            updated_at=str(state["updated_at"]),
        )
