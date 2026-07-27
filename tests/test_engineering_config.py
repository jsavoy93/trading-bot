from pathlib import Path

from engineering.config import (
    REQUIRED_REPOSITORY_PATHS,
    missing_required_paths,
)


def test_current_repository_has_required_paths() -> None:
    assert missing_required_paths(Path.cwd()) == ()


def test_missing_required_paths_reports_missing_items(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").touch()

    missing = missing_required_paths(tmp_path)

    assert missing == (
        "AGENT_BACKLOG.md",
        "AGENT_OPERATING_PLAN.md",
    )


def test_required_repository_paths_are_stable() -> None:
    assert REQUIRED_REPOSITORY_PATHS == (
        ".git",
        "AGENTS.md",
        "AGENT_BACKLOG.md",
        "AGENT_OPERATING_PLAN.md",
    )
