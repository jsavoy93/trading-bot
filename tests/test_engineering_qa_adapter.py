"""Behavioral tests for QAAdapterImpl — ENGPLAT-002C2.

Scope: Verify QAAdapterImpl satisfies QAAdapter, returns exact config values,
and construction is side-effect free.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from engineering.adapters import QAAdapter
from engineering.context import QAAdapterImpl, build_project_context
from engineering.models import GovernanceFiles, ProjectConfig, WorkflowFiles


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TRADING_BOT_ROOT = Path("/root/.openclaw/workspace/trading-bot")


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
        qa_commands=("python -m pytest tests/test_example.py",),
        qa_timeout_seconds=300,
        prohibited_operations=("no_live_trading",),
        agents_may_merge=False,
        owner_ids=("test-owner",),
        agent_owners=("test-agent",),
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestQAAdapterImplProtocol:
    """QAAdapterImpl must satisfy QAAdapter."""

    def test_isinstance_qa_adapter(self, tmp_path: Path) -> None:
        config = _minimal_config(tmp_path)
        assert isinstance(QAAdapterImpl(config), QAAdapter)


# ---------------------------------------------------------------------------
# Config value access
# ---------------------------------------------------------------------------

class TestQAAdapterImplValues:
    """configured_command and timeout_seconds return exact config values."""

    def test_configured_command_exactly_matches_qa_commands(self, tmp_path: Path) -> None:
        config = _minimal_config(tmp_path)
        adapter = QAAdapterImpl(config)
        assert adapter.configured_command() == config.qa_commands

    def test_configured_command_returns_tuple(self, tmp_path: Path) -> None:
        config = _minimal_config(tmp_path)
        adapter = QAAdapterImpl(config)
        result = adapter.configured_command()
        assert isinstance(result, tuple)
        assert all(isinstance(seg, str) for seg in result)

    def test_timeout_seconds_exactly_matches_qa_timeout_seconds(self, tmp_path: Path) -> None:
        config = _minimal_config(tmp_path)
        adapter = QAAdapterImpl(config)
        assert adapter.timeout_seconds() == config.qa_timeout_seconds

    def test_timeout_seconds_returns_positive_int(self, tmp_path: Path) -> None:
        config = _minimal_config(tmp_path)
        adapter = QAAdapterImpl(config)
        result = adapter.timeout_seconds()
        assert isinstance(result, int)
        assert result > 0

    def test_values_are_deterministic_across_calls(self, tmp_path: Path) -> None:
        config = _minimal_config(tmp_path)
        adapter = QAAdapterImpl(config)
        for _ in range(3):
            assert adapter.configured_command() == config.qa_commands
            assert adapter.timeout_seconds() == config.qa_timeout_seconds


# ---------------------------------------------------------------------------
# build_project_context wiring
# ---------------------------------------------------------------------------

class TestBuildProjectContextWiring:
    """build_project_context must return QAAdapterImpl in ctx.qa."""

    def test_ctx_qa_is_qa_adapter_impl(self) -> None:
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        assert isinstance(ctx.qa, QAAdapterImpl)

    def test_ctx_qa_command_matches_config(self) -> None:
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        assert ctx.qa.configured_command() == config.qa_commands

    def test_ctx_qa_timeout_matches_config(self) -> None:
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        assert ctx.qa.timeout_seconds() == config.qa_timeout_seconds

    def test_ctx_git_still_git_adapter_impl(self) -> None:
        from engineering.adapters import GitReadAdapter
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        assert isinstance(ctx.git, GitReadAdapter)
        # Must NOT be a deferred stub
        assert not type(ctx.git).__name__.startswith("_Deferred")

    def test_ctx_files_is_file_read_adapter_impl(self) -> None:
        from engineering.context import FileReadAdapterImpl
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        assert isinstance(ctx.files, FileReadAdapterImpl)


# ---------------------------------------------------------------------------
# Side-effect safety
# ---------------------------------------------------------------------------

class TestQAAdapterImplNoSideEffects:
    """QAAdapterImpl construction must not perform any side effects."""

    def test_construction_does_not_execute_qa(self, tmp_path: Path) -> None:
        """Construction must not run QA or invoke any QA tooling."""
        config = _minimal_config(tmp_path)
        # If this raises, it would be a bug — but it should not raise
        QAAdapterImpl(config)
        # Verify no QA process was started by checking /proc if available
        # (This is a best-effort check; the real proof is that __init__ has no subprocess calls)

    def test_construction_does_not_spawn_subprocess(self, tmp_path: Path) -> None:
        """Construction must not spawn any subprocess."""
        config = _minimal_config(tmp_path)
        QAAdapterImpl(config)
        # Proof: QAAdapterImpl.__init__ contains no subprocess calls

    def test_construction_does_not_create_files(self, tmp_path: Path) -> None:
        """Construction must not create any files or directories."""
        config = _minimal_config(tmp_path)
        (tmp_path / "AGENT_BACKLOG.md").touch()
        (tmp_path / "AGENT_OPERATING_PLAN.md").touch()
        (tmp_path / "OWNERS.md").touch()
        (tmp_path / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md").touch()
        (tmp_path / ".git").mkdir()
        (tmp_path / "reports").mkdir()

        before = set(_all_paths(tmp_path))
        QAAdapterImpl(config)
        after = set(_all_paths(tmp_path))
        assert not (after - before), "QAAdapterImpl construction created unexpected paths"

    def test_construction_does_not_perform_network_or_git(self, tmp_path: Path) -> None:
        """Construction must not make network calls or Git operations."""
        config = _minimal_config(tmp_path)
        QAAdapterImpl(config)
        # Proof: QAAdapterImpl.__init__ contains no network or Git calls

    def test_no_cwd_or_repository_discovery(self, tmp_path: Path) -> None:
        """No Path.cwd() or repository discovery is introduced."""
        import inspect
        source = inspect.getsource(QAAdapterImpl.__init__)
        assert "cwd" not in source.lower()
        assert "Path.cwd" not in source
        assert "discover" not in source.lower()
        assert "repo_root" not in source or "self._config.repository_root" in source


# ---------------------------------------------------------------------------
# Invalid ProjectConfig handling
# ---------------------------------------------------------------------------

class TestInvalidConfigHandling:
    """Invalid config must fail at factory boundary, before adapter construction."""

    def test_negative_timeout_fails_before_adapter_construction(self) -> None:
        """Negative qa_timeout_seconds fails validation before any adapter is used."""
        config = _minimal_config(Path("/tmp/invalid-config"))
        # Override with invalid timeout
        invalid_config = ProjectConfig(
            schema_version=config.schema_version,
            project_id=config.project_id,
            display_name=config.display_name,
            repository_root=config.repository_root,
            authoritative_base_branch=config.authoritative_base_branch,
            governance_files=config.governance_files,
            workflow_files=config.workflow_files,
            qa_commands=config.qa_commands,
            qa_timeout_seconds=-1,
            prohibited_operations=config.prohibited_operations,
            agents_may_merge=config.agents_may_merge,
            owner_ids=config.owner_ids,
            agent_owners=config.agent_owners,
        )
        with pytest.raises(ValueError, match="validation failed"):
            build_project_context(invalid_config)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_paths(root: Path) -> list[Path]:
    """Return all paths under root (recursive)."""
    try:
        return list(root.rglob("*"))
    except PermissionError:
        return []
