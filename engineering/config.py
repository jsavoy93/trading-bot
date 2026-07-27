from __future__ import annotations

from pathlib import Path


REQUIRED_REPOSITORY_PATHS: tuple[str, ...] = (
    ".git",
    "AGENTS.md",
    "AGENT_BACKLOG.md",
    "AGENT_OPERATING_PLAN.md",
)


def missing_required_paths(repo_root: Path) -> tuple[str, ...]:
    return tuple(
        required_path
        for required_path in REQUIRED_REPOSITORY_PATHS
        if not (repo_root / required_path).exists()
    )
