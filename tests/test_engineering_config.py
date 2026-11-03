from pathlib import Path

from engineering.config import (
    REQUIRED_REPOSITORY_PATHS,
    missing_required_paths,
)


def test_current_repository_has_required_paths() -> None:
    # ENG-04c: derive the repo root from this test file instead of Path.cwd(),
    # so the test is correct regardless of the directory pytest is invoked
    # from (e.g. cwd=/tmp).
    repo_root = Path(__file__).resolve().parents[1]
    missing = missing_required_paths(repo_root)
    if not (repo_root / ".git").exists():
        # Exported tree (e.g. git-archive on a CI runner) has no .git; that
        # item is an artifact of the packaging, not a governance defect.
        missing = tuple(item for item in missing if item != ".git")
    assert missing == ()


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
