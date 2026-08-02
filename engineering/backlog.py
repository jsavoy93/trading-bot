from __future__ import annotations

import re
from pathlib import Path

from engineering.models import BacklogTask, Priority, TaskStatus


TASK_HEADING_PATTERN = re.compile(
    r"^###\s+(?P<task_id>[A-Z]+-\d+)\s+—\s+(?P<title>.+)$"
)


class BacklogParseError(ValueError):
    pass


def load_backlog(backlog_path: Path) -> tuple[BacklogTask, ...]:
    return parse_backlog(backlog_path.read_text(encoding="utf-8"))


def parse_backlog(content: str) -> tuple[BacklogTask, ...]:
    lines = content.splitlines()
    tasks: list[BacklogTask] = []
    index = 0

    while index < len(lines):
        heading_match = TASK_HEADING_PATTERN.match(lines[index].strip())

        if heading_match is None:
            index += 1
            continue

        task_id = heading_match.group("task_id")
        title = heading_match.group("title").strip()
        index += 1

        fields: dict[str, str] = {}
        acceptance_criteria: list[str] = []
        allowed_areas: list[str] = []
        current_section: str | None = None

        while index < len(lines):
            line = lines[index].strip()

            if TASK_HEADING_PATTERN.match(line) or line.startswith("## Phase"):
                break

            if line == "Acceptance criteria:":
                current_section = "acceptance_criteria"
            elif line == "Allowed areas:":
                current_section = "allowed_areas"
            elif line.startswith("Status:"):
                fields["status"] = line.removeprefix("Status:").strip()
                current_section = None
            elif line.startswith("Owner:"):
                fields["owner"] = line.removeprefix("Owner:").strip()
                current_section = None
            elif line.startswith("Priority:"):
                fields["priority"] = line.removeprefix("Priority:").strip()
                current_section = None
            elif line.startswith("- "):
                value = line.removeprefix("- ").strip()

                if current_section == "acceptance_criteria":
                    acceptance_criteria.append(value)
                elif current_section == "allowed_areas":
                    allowed_areas.append(value)

            index += 1

        missing_fields = {
            required_field
            for required_field in ("status", "owner", "priority")
            if required_field not in fields
        }

        if missing_fields:
            missing = ", ".join(sorted(missing_fields))
            raise BacklogParseError(
                f"Task {task_id} is missing required fields: {missing}"
            )

        try:
            status = TaskStatus(fields["status"])
        except ValueError as exc:
            raise BacklogParseError(
                f"Task {task_id} has invalid status: {fields['status']}"
            ) from exc

        try:
            priority = Priority(fields["priority"])
        except ValueError as exc:
            raise BacklogParseError(
                f"Task {task_id} has invalid priority: {fields['priority']}"
            ) from exc

        tasks.append(
            BacklogTask(
                task_id=task_id,
                title=title,
                status=status,
                owner=fields["owner"],
                priority=priority,
                acceptance_criteria=tuple(acceptance_criteria),
                allowed_areas=tuple(allowed_areas),
            )
        )

    return tuple(tasks)
