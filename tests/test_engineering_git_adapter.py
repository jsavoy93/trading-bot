"""Tests for GitAdapterImpl — ENGPLAT-002C1.

Behavioral tests verifying GitAdapterImpl satisfies GitReadAdapter
and delegates correctly to GitService.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from engineering.adapters import CapabilityUnavailable, GitReadAdapter
from engineering.context import GitAdapterImpl, build_project_context
from engineering.git_service import GitService
from engineering.models import GovernanceFiles, ProjectConfig, WorkflowFiles


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

TRADING_BOT_ROOT = Path("/root/.openclaw/workspace/trading-bot")


def _create_temp_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository in tmp_path and return the repo root."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    readme = tmp_path / "README.md"
    readme.write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


def _minimal_config(repo_root: Path) -> ProjectConfig:
    """Build a minimal valid ProjectConfig pointing at repo_root."""
    return ProjectConfig(
        schema_version="1.0",
        project_id="test-project",
        display_name="Test Project",
        repository_root=repo_root,
        authoritative_base_branch="main",
        governance_files=GovernanceFiles(
            backlog_path=repo_root / "AGENT_BACKLOG.md",
            operating_plan_path=repo_root / "AGENT_OPERATING_PLAN.md",
            owners_path=repo_root / "OWNERS.md",
            handoff_path=repo_root / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md",
        ),
        workflow_files=WorkflowFiles(
            workflow_store_path=repo_root / ".git" / "workflow.json",
            event_store_path=repo_root / ".git" / "events.db",
            report_dir=repo_root / "reports",
        ),
        qa_commands=("python -m pytest",),
        qa_timeout_seconds=300,
        prohibited_operations=("no_live_trading",),
        agents_may_merge=False,
        owner_ids=("test-owner",),
        agent_owners=("test-agent",),
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestGitAdapterImplSatisfiesProtocol:
    """GitAdapterImpl must satisfy GitReadAdapter."""

    def test_isinstance_git_read_adapter(self, tmp_path: Path) -> None:
        repo_root = _create_temp_git_repo(tmp_path)
        adapter = GitAdapterImpl(repo_root)
        assert isinstance(adapter, GitReadAdapter)


# ---------------------------------------------------------------------------
# Method delegation — round-trip against GitService
# ---------------------------------------------------------------------------

class TestGitAdapterImplDelegation:
    """GitAdapterImpl must delegate all methods to GitService with identical output."""

    def test_current_branch_matches_git_service(self, tmp_path: Path) -> None:
        repo_root = _create_temp_git_repo(tmp_path)
        adapter = GitAdapterImpl(repo_root)
        git_service = GitService(repo_root)
        assert adapter.current_branch() == git_service.current_branch()

    def test_is_clean_matches_git_service(self, tmp_path: Path) -> None:
        repo_root = _create_temp_git_repo(tmp_path)
        adapter = GitAdapterImpl(repo_root)
        git_service = GitService(repo_root)
        assert adapter.is_clean() == git_service.is_clean()

    def test_repository_state_matches_git_service(self, tmp_path: Path) -> None:
        repo_root = _create_temp_git_repo(tmp_path)
        adapter = GitAdapterImpl(repo_root)
        git_service = GitService(repo_root)
        state = adapter.repository_state()
        svc_state = git_service.repository_state()
        assert state.root == svc_state.root
        assert state.branch == svc_state.branch
        assert state.is_clean == svc_state.is_clean

    def test_branch_exists_true_for_existing_branch(self, tmp_path: Path) -> None:
        repo_root = _create_temp_git_repo(tmp_path)
        adapter = GitAdapterImpl(repo_root)
        git_service = GitService(repo_root)
        # main branch was created by _create_temp_git_repo
        assert adapter.branch_exists("main") is git_service.branch_exists("main") is True

    def test_branch_exists_false_for_nonexistent_branch(self, tmp_path: Path) -> None:
        repo_root = _create_temp_git_repo(tmp_path)
        adapter = GitAdapterImpl(repo_root)
        git_service = GitService(repo_root)
        assert adapter.branch_exists("nonexistent-xyz") == (
            git_service.branch_exists("nonexistent-xyz")
        ) == False

    def test_is_ancestor_same_commit_is_true(self, tmp_path: Path) -> None:
        repo_root = _create_temp_git_repo(tmp_path)
        adapter = GitAdapterImpl(repo_root)
        git_service = GitService(repo_root)
        head = git_service.run("rev-parse", "HEAD")
        assert adapter.is_ancestor(head, head) is True


# ---------------------------------------------------------------------------
# build_project_context wiring
# ---------------------------------------------------------------------------

class TestBuildProjectContext:
    """build_project_context must return GitAdapterImpl in ctx.git."""

    def test_ctx_git_is_git_adapter_impl(self) -> None:
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        assert isinstance(ctx.git, GitReadAdapter)
        # Must NOT be the deferred stub
        assert not type(ctx.git).__name__.startswith("_Deferred")

    def test_ctx_git_matches_direct_git_service(self) -> None:
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        direct_git = GitService(TRADING_BOT_ROOT)
        assert ctx.git.current_branch() == direct_git.current_branch()
        assert ctx.git.is_clean() == direct_git.is_clean()

    def test_ctx_qa_still_raises_capability_unavailable(self) -> None:
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        with pytest.raises(CapabilityUnavailable) as exc_info:
            ctx.qa.configured_command()
        assert exc_info.value.capability == "qa"

    def test_ctx_files_still_raises_capability_unavailable(self) -> None:
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        with pytest.raises(CapabilityUnavailable) as exc_info:
            ctx.files.exists(Path("."))
        assert exc_info.value.capability == "files"


# ---------------------------------------------------------------------------
# Side-effect safety
# ---------------------------------------------------------------------------

class TestGitAdapterImplNoSideEffects:
    """GitAdapterImpl construction must not mutate repository state."""

    def test_construction_does_not_change_branch(self, tmp_path: Path) -> None:
        repo_root = _create_temp_git_repo(tmp_path)
        git_service = GitService(repo_root)
        original_branch = git_service.current_branch()
        GitAdapterImpl(repo_root)
        assert git_service.current_branch() == original_branch

    def test_construction_does_not_create_commits(self, tmp_path: Path) -> None:
        repo_root = _create_temp_git_repo(tmp_path)
        git_service = GitService(repo_root)
        original_log = git_service.run("log", "--oneline")
        GitAdapterImpl(repo_root)
        assert git_service.run("log", "--oneline") == original_log
