import subprocess
from pathlib import Path

import pytest

from engineering.git_service import GitService
from engineering.models import RepositoryState


def run_git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )


def create_repository(repo_root: Path) -> GitService:
    run_git(repo_root, "init", "-b", "source-branch")
    run_git(repo_root, "config", "user.name", "Engineering Test")
    run_git(repo_root, "config", "user.email", "engineering@example.com")

    tracked_file = repo_root / "README.md"
    tracked_file.write_text("test repository\n", encoding="utf-8")

    run_git(repo_root, "add", "README.md")
    run_git(repo_root, "commit", "-m", "Initial commit")

    return GitService(repo_root)


def test_repository_state_matches_temporary_repo(tmp_path: Path) -> None:
    git = create_repository(tmp_path)

    state = git.repository_state()

    assert isinstance(state, RepositoryState)
    assert state.root == tmp_path
    assert state.branch == "source-branch"
    assert state.is_clean is True


def test_branch_exists_detects_local_branch(tmp_path: Path) -> None:
    git = create_repository(tmp_path)

    assert git.branch_exists("source-branch") is True
    assert git.branch_exists("missing-branch") is False


def test_creates_and_checks_out_feature_branch(tmp_path: Path) -> None:
    git = create_repository(tmp_path)

    state = git.create_and_checkout_branch(
        branch="agent/test-001",
        expected_source_branch="source-branch",
    )

    assert state.branch == "agent/test-001"
    assert state.is_clean is True
    assert git.branch_exists("agent/test-001") is True


def test_refuses_to_create_branch_when_repository_is_dirty(
    tmp_path: Path,
) -> None:
    git = create_repository(tmp_path)
    (tmp_path / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="repository is not clean",
    ):
        git.create_and_checkout_branch(
            branch="agent/test-001",
            expected_source_branch="source-branch",
        )

    assert git.current_branch() == "source-branch"


def test_refuses_when_current_branch_is_not_expected(
    tmp_path: Path,
) -> None:
    git = create_repository(tmp_path)

    with pytest.raises(
        RuntimeError,
        match="expected 'different-branch'",
    ):
        git.create_and_checkout_branch(
            branch="agent/test-001",
            expected_source_branch="different-branch",
        )

    assert git.current_branch() == "source-branch"


def test_refuses_when_feature_branch_already_exists(
    tmp_path: Path,
) -> None:
    git = create_repository(tmp_path)
    run_git(tmp_path, "branch", "agent/test-001")

    with pytest.raises(
        RuntimeError,
        match="already exists",
    ):
        git.create_and_checkout_branch(
            branch="agent/test-001",
            expected_source_branch="source-branch",
        )

    assert git.current_branch() == "source-branch"


def test_prepares_new_feature_branch(tmp_path: Path) -> None:
    git = create_repository(tmp_path)

    state = git.prepare_feature_branch("agent/test-001", "source-branch")

    assert state.branch == "agent/test-001"
    assert state.is_clean is True


def test_resumes_existing_feature_branch_from_source(tmp_path: Path) -> None:
    git = create_repository(tmp_path)
    run_git(tmp_path, "branch", "agent/test-001")

    state = git.prepare_feature_branch("agent/test-001", "source-branch")

    assert state.branch == "agent/test-001"
    assert state.is_clean is True


def test_resumes_current_feature_branch(tmp_path: Path) -> None:
    git = create_repository(tmp_path)
    run_git(tmp_path, "switch", "-c", "agent/test-001")

    state = git.prepare_feature_branch("agent/test-001", "source-branch")

    assert state.branch == "agent/test-001"
    assert state.is_clean is True


def test_prepare_refuses_dirty_repository(tmp_path: Path) -> None:
    git = create_repository(tmp_path)
    (tmp_path / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="repository is not clean"):
        git.prepare_feature_branch("agent/test-001", "source-branch")


def test_prepare_refuses_unexpected_current_branch(tmp_path: Path) -> None:
    git = create_repository(tmp_path)
    run_git(tmp_path, "switch", "-c", "unrelated-branch")

    with pytest.raises(RuntimeError, match="current branch is 'unrelated-branch'"):
        git.prepare_feature_branch("agent/test-001", "source-branch")


def test_prepare_refuses_missing_expected_source_branch(tmp_path: Path) -> None:
    git = create_repository(tmp_path)

    with pytest.raises(RuntimeError, match="expected source branch 'main' does not exist"):
        git.prepare_feature_branch("agent/test-001", "main")


def test_prepare_refuses_feature_branch_not_based_on_source(tmp_path: Path) -> None:
    git = create_repository(tmp_path)
    run_git(tmp_path, "switch", "--orphan", "agent/test-001")
    (tmp_path / "README.md").write_text("unrelated history\n", encoding="utf-8")
    run_git(tmp_path, "add", "README.md")
    run_git(tmp_path, "commit", "-m", "Unrelated feature history")
    run_git(tmp_path, "switch", "source-branch")

    with pytest.raises(RuntimeError, match="is not based on 'source-branch'"):
        git.prepare_feature_branch("agent/test-001", "source-branch")

    assert git.current_branch() == "source-branch"


def test_prepare_refuses_non_repository(tmp_path: Path) -> None:
    git = GitService(tmp_path)

    with pytest.raises(RuntimeError, match="Repository does not exist"):
        git.prepare_feature_branch("agent/test-001", "source-branch")
