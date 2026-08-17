"""Tests for engineering.manager project selection."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from engineering.manager import _cli_main, _parser, _resolve_project, _announce_project
from engineering.models import ProjectConfig, ProjectRegistry
from engineering.registry import get_project, load_registry
from engineering.runtime import ensure_project_runtime_dirs


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _fantasy_config(root: Path) -> ProjectConfig:
    """Return a fantasy-style ProjectConfig for testing."""
    from engineering.models import GovernanceFiles, WorkflowFiles

    return ProjectConfig(
        schema_version="1.0",
        project_id="fantasy-draft-command-center",
        display_name="Fantasy Draft Command Center",
        repository_root=root,
        authoritative_base_branch="main",
        governance_files=GovernanceFiles(
            backlog_path=root / "AGENT_BACKLOG.md",
            operating_plan_path=root / "AGENT_OPERATING_PLAN.md",
            owners_path=root / "OWNERS.md",
            handoff_path=root / "AUTONOMOUS_ENGINEERING_HANDOFF.md",
        ),
        workflow_files=WorkflowFiles(
            workflow_store_path=root / ".engineering" / "workflow_store.json",
            event_store_path=root / ".engineering" / "event_store.db",
            report_dir=root / ".engineering" / "reports",
        ),
        qa_commands=("npm test",),
        qa_timeout_seconds=300,
        prohibited_operations=(),
        agents_may_merge=False,
        owner_ids=("josh",),
        agent_owners=("fantasy-agent",),
    )


def _trading_bot_config(root: Path) -> ProjectConfig:
    """Return a trading-bot-style ProjectConfig for testing."""
    from engineering.models import GovernanceFiles, WorkflowFiles

    return ProjectConfig(
        schema_version="1.0",
        project_id="trading-bot",
        display_name="Trading Bot",
        repository_root=root,
        authoritative_base_branch="main",
        governance_files=GovernanceFiles(
            backlog_path=root / "AGENT_BACKLOG.md",
            operating_plan_path=root / "AGENT_OPERATING_PLAN.md",
            owners_path=root / "OWNERS.md",
            handoff_path=root / "AUTONOMOUS_ENGINEERING_HANDOFF.md",
        ),
        workflow_files=WorkflowFiles(
            workflow_store_path=root / "engineering" / "workflow_store.json",
            event_store_path=root / "engineering" / "event_store.db",
            report_dir=root / "reports",
        ),
        qa_commands=("python -m pytest",),
        qa_timeout_seconds=300,
        prohibited_operations=("no_live_trading",),
        agents_may_merge=False,
        owner_ids=("josh",),
        agent_owners=("trading-manager",),
    )


def _make_gov_files(root: Path) -> None:
    """Create minimal governance files for a test project."""
    (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (root / "AGENT_BACKLOG.md").write_text("# Backlog\n", encoding="utf-8")
    (root / "AGENT_OPERATING_PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (root / "OWNERS.md").write_text("# Owners\n", encoding="utf-8")
    (root / "AUTONOMOUS_ENGINEERING_HANDOFF.md").write_text("# Handoff\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# _parser tests
# ---------------------------------------------------------------------------


class TestParserProjectId:
    def test_project_id_flag_exists(self) -> None:
        args = _parser().parse_args(["--project-id", "my-project"])
        assert args.project_id == "my-project"

    def test_project_id_default_none(self) -> None:
        args = _parser().parse_args([])
        assert args.project_id is None

    def test_drive_still_works(self) -> None:
        args = _parser().parse_args(["--project-id", "trading-bot", "--drive"])
        assert args.drive is True
        assert args.project_id == "trading-bot"


# ---------------------------------------------------------------------------
# _resolve_project tests — using mock to isolate from real registry
# ---------------------------------------------------------------------------


class TestResolveProjectTradingBot:
    def test_resolves_trading_bot_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--project-id trading-bot resolves trading-bot ProjectConfig."""
        trading_root = tmp_path / "trading"
        trading_root.mkdir()
        _make_gov_files(trading_root)
        config = _trading_bot_config(trading_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        # Clear ENGINEERING_PROJECT_ID
        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args(["--project-id", "trading-bot"])
            result = _resolve_project(args)

        assert result is not None
        assert result.project_id == "trading-bot"
        assert result.repository_root == trading_root


class TestResolveProjectFantasy:
    def test_resolves_fantasy_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--project-id fantasy-draft-command-center resolves fantasy ProjectConfig."""
        fantasy_root = tmp_path / "fantasy"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args(["--project-id", "fantasy-draft-command-center"])
            result = _resolve_project(args)

        assert result is not None
        assert result.project_id == "fantasy-draft-command-center"
        assert result.repository_root == fantasy_root


class TestEnvVarFallback:
    def test_env_var_resolves_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ENGINEERING_PROJECT_ID resolves project when CLI --project-id absent."""
        fantasy_root = tmp_path / "fantasy-env"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.setenv("ENGINEERING_PROJECT_ID", "fantasy-draft-command-center")

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args([])  # No --project-id
            result = _resolve_project(args)

        assert result is not None
        assert result.project_id == "fantasy-draft-command-center"


class TestCLIPrecedenceOverEnvVar:
    def test_cli_overrides_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI --project-id takes precedence over ENGINEERING_PROJECT_ID."""
        fantasy_root = tmp_path / "fantasy-cli"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        fantasy_config = _fantasy_config(fantasy_root)
        trading_root = tmp_path / "trading-cli"
        trading_root.mkdir()
        _make_gov_files(trading_root)
        trading_config = _trading_bot_config(trading_root)
        registry = ProjectRegistry.from_projects([fantasy_config, trading_config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        # Env says fantasy
        monkeypatch.setenv("ENGINEERING_PROJECT_ID", "fantasy-draft-command-center")

        with patch("engineering.registry.load_registry", mock_load_registry):
            # CLI says trading-bot → should win
            args = _parser().parse_args(["--project-id", "trading-bot"])
            result = _resolve_project(args)

        assert result is not None
        assert result.project_id == "trading-bot"


class TestMissingSelectionFailsClosed:
    def test_missing_project_id_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Neither --project-id nor ENGINEERING_PROJECT_ID → exit 1."""
        fantasy_root = tmp_path / "fantasy-missing"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args([])
            result = _resolve_project(args)

        assert result is None


class TestUnknownProjectFailsClosed:
    def test_unknown_project_id_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unknown --project-id → fail closed (not silent trading-bot fallback)."""
        fantasy_root = tmp_path / "fantasy-unknown"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args(["--project-id", "non-existent-project"])
            result = _resolve_project(args)

        assert result is None


class TestMissingRegistryFailsClosed:
    def test_missing_registry_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing registry file → exit 1."""
        from engineering.registry import MalformedRegistryError

        def mock_load_registry_raises(*, skip_workflow_files=False):
            raise MalformedRegistryError("registry file does not exist")

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry_raises):
            args = _parser().parse_args(["--project-id", "any-project"])
            result = _resolve_project(args)

        assert result is None


class TestMalformedRegistryFailsClosed:
    def test_malformed_registry_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Malformed registry JSON → exit 1."""
        from engineering.registry import MalformedRegistryError

        def mock_load_registry_raises(*, skip_workflow_files=False):
            raise MalformedRegistryError("registry is not valid JSON")

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry_raises):
            args = _parser().parse_args(["--project-id", "any-project"])
            result = _resolve_project(args)

        assert result is None


# ---------------------------------------------------------------------------
# Runtime directory isolation tests
# ---------------------------------------------------------------------------


class TestFantasyRuntimeIsolation:
    def test_fantasy_creates_only_fantasy_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fantasy activation creates only fantasy .engineering/, not trading-bot paths."""
        fantasy_root = tmp_path / "fantasy-iso"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args(["--project-id", "fantasy-draft-command-center"])
            result = _resolve_project(args)

        assert result is not None
        # .engineering/ and .engineering/reports/ should have been created
        assert (fantasy_root / ".engineering").is_dir()
        assert (fantasy_root / ".engineering" / "reports").is_dir()
        # No trading-bot engineering/ path at repo root
        assert not (fantasy_root / "engineering").exists()
        # No trading-bot reports/ at repo root
        assert not (fantasy_root / "reports").exists()


class TestTradingBotRuntimeIsolation:
    def test_trading_bot_creates_only_trading_bot_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Trading-bot activation creates only trading-bot engineering/ and reports/."""
        trading_root = tmp_path / "trading-iso"
        trading_root.mkdir()
        _make_gov_files(trading_root)
        config = _trading_bot_config(trading_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args(["--project-id", "trading-bot"])
            result = _resolve_project(args)

        assert result is not None
        # engineering/ created
        assert (trading_root / "engineering").is_dir()
        # reports/ created at repo root
        assert (trading_root / "reports").is_dir()
        # No fantasy .engineering/
        assert not (trading_root / ".engineering").exists()


# ---------------------------------------------------------------------------
# Announcement tests
# ---------------------------------------------------------------------------


class TestAnnounceProject:
    def test_announcement_contains_project_identity(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """_announce_project prints project_id, display_name, repo_root, base_branch."""
        fantasy_root = tmp_path / "fantasy-ann"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)

        _announce_project(config)
        output = capsys.readouterr().out

        assert "fantasy-draft-command-center" in output
        assert "Fantasy Draft Command Center" in output
        assert str(fantasy_root) in output
        assert "main" in output  # authoritative_base_branch


# ---------------------------------------------------------------------------
# _cli_main integration tests (calling _cli_main directly, not subprocess)
# ---------------------------------------------------------------------------


class TestCLIMainIntegration:
    def test_cli_main_unknown_project_exit_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Unknown --project-id returns exit code 1."""
        fantasy_root = tmp_path / "fantasy-exit1"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args(["--project-id", "non-existent-project"])
            exit_code = _cli_main(args)

        assert exit_code == 1
        output = capsys.readouterr().out
        assert "Unknown project" in output

    def test_cli_main_missing_selection_exit_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Missing --project-id and ENGINEERING_PROJECT_ID returns exit code 1."""
        fantasy_root = tmp_path / "fantasy-no-select"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args([])
            exit_code = _cli_main(args)

        assert exit_code == 1
        output = capsys.readouterr().out
        assert "required" in output.lower()

    def test_cli_main_trading_bot_announces_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Trading-bot selection announces project identity."""
        trading_root = tmp_path / "trading-ann"
        trading_root.mkdir()
        _make_gov_files(trading_root)
        config = _trading_bot_config(trading_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args(["--project-id", "trading-bot"])
            exit_code = _cli_main(args)

        # Will fail on missing required repo paths (AGENTS.md etc already created),
        # but the announcement should appear before that failure.
        output = capsys.readouterr().out
        assert "trading-bot" in output.lower()
        assert "Trading Bot" in output

    def test_cli_main_fantasy_announces_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Fantasy selection announces project identity."""
        fantasy_root = tmp_path / "fantasy-announce"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args(["--project-id", "fantasy-draft-command-center"])
            exit_code = _cli_main(args)

        # Will fail because .engineering/ is created but AGENT_BACKLOG etc don't
        # match what build_project_context expects — but announcement should appear.
        output = capsys.readouterr().out
        assert "fantasy-draft-command-center" in output


# ---------------------------------------------------------------------------
# No implicit fallback
# ---------------------------------------------------------------------------


class TestNoImplicitFallback:
    def test_no_trading_bot_fallback_without_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No --project-id and no ENGINEERING_PROJECT_ID → fail, not trading-bot."""
        fantasy_root = tmp_path / "fantasy-no-fb"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args([])
            result = _resolve_project(args)

        # Should return None (fail closed), NOT fall back to TRADING_BOT_PROJECT
        assert result is None


# ---------------------------------------------------------------------------
# Path.cwd() absence
# ---------------------------------------------------------------------------


class TestNoPathCwdSelection:
    def test_no_cwd_in_resolve_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Project resolution uses registry config, not Path.cwd()."""
        fantasy_root = tmp_path / "cwd-test"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        # Change cwd to somewhere completely different during resolution
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        original_cwd = os.getcwd()
        try:
            os.chdir(other_dir)
            with patch("engineering.registry.load_registry", mock_load_registry):
                args = _parser().parse_args(["--project-id", "fantasy-draft-command-center"])
                result = _resolve_project(args)
        finally:
            os.chdir(original_cwd)

        # Should still find the project from registry (not cwd)
        assert result is not None
        assert result.project_id == "fantasy-draft-command-center"


# ---------------------------------------------------------------------------
# Drive flag still functional
# ---------------------------------------------------------------------------


class TestDriveFlagStillWorks:
    def test_drive_flag_parsed_after_project_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--drive flag still works alongside --project-id."""
        fantasy_root = tmp_path / "fantasy-drive"
        fantasy_root.mkdir()
        _make_gov_files(fantasy_root)
        config = _fantasy_config(fantasy_root)
        registry = ProjectRegistry.from_projects([config])

        def mock_load_registry(*, skip_workflow_files=False):
            return registry

        monkeypatch.delenv("ENGINEERING_PROJECT_ID", raising=False)

        with patch("engineering.registry.load_registry", mock_load_registry):
            args = _parser().parse_args([
                "--project-id", "fantasy-draft-command-center",
                "--drive",
                "--max-steps", "3",
            ])
            result = _resolve_project(args)

        assert result is not None
        assert args.drive is True
        assert args.max_steps == 3
