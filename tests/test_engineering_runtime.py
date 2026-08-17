"""Tests for engineering.runtime runtime initialization."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from engineering.models import ProjectConfig, WorkflowFiles, GovernanceFiles
from engineering.runtime import (
    RuntimeInitError,
    RuntimeInitializationResult,
    ensure_project_runtime_dirs,
)


def _fantasy_config(root: Path) -> ProjectConfig:
    """Return a fantasy-style ProjectConfig for testing."""
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
        prohibited_operations=("no_production_database",),
        agents_may_merge=False,
        owner_ids=("josh",),
        agent_owners=("fantasy-manager",),
    )


def _trading_bot_config(root: Path) -> ProjectConfig:
    """Return a trading-bot-style ProjectConfig for testing."""
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


class TestEnsureProjectRuntimeDirs:
    """Tests for ensure_project_runtime_dirs()."""

    def test_creates_missing_engineering_directory(self, tmp_path: Path) -> None:
        """Creates .engineering/ when it does not exist."""
        config = _fantasy_config(tmp_path)
        result = ensure_project_runtime_dirs(config)

        assert (tmp_path / ".engineering").is_dir()
        assert (tmp_path / ".engineering" / "reports").is_dir()
        assert isinstance(result, RuntimeInitializationResult)
        created = set(result.created_paths)
        assert tmp_path / ".engineering" in created
        assert tmp_path / ".engineering" / "reports" in created

    def test_creates_reports_subdirectory(self, tmp_path: Path) -> None:
        """Creates .engineering/reports/ when report_dir doesn't exist."""
        config = _fantasy_config(tmp_path)
        result = ensure_project_runtime_dirs(config)

        assert (tmp_path / ".engineering" / "reports").is_dir()

    def test_duplicate_shared_parent_created_only_once(
        self, tmp_path: Path
    ) -> None:
        """Duplicate parent directories are created only once.

        For fantasy, workflow_store_path.parent and event_store_path.parent
        are the same (.engineering/). Only one mkdir call should occur.
        """
        config = _fantasy_config(tmp_path)
        result = ensure_project_runtime_dirs(config)

        # Both workflow_store_path.parent and event_store_path.parent
        # resolve to .engineering/. Unique set should de-duplicate.
        # .engineering/reports/ is also created.
        # So we expect 2 unique directories created.
        assert len(result.created_paths) == 2
        created_set = set(result.created_paths)
        assert tmp_path / ".engineering" in created_set
        assert tmp_path / ".engineering" / "reports" in created_set

    def test_second_call_creates_nothing(self, tmp_path: Path) -> None:
        """Second call with same config does not raise and creates nothing."""
        config = _fantasy_config(tmp_path)

        result1 = ensure_project_runtime_dirs(config)
        assert len(result1.created_paths) == 2  # .engineering/ and .engineering/reports/

        result2 = ensure_project_runtime_dirs(config)
        assert result2.created_paths == ()
        assert result1.created_paths != result2.created_paths

    def test_created_paths_deterministic(self, tmp_path: Path) -> None:
        """created_paths is deterministic regardless of call order."""
        config = _fantasy_config(tmp_path)

        result1 = ensure_project_runtime_dirs(config)
        eng_dir, reports_dir = sorted(result1.created_paths, key=str)

        assert eng_dir == tmp_path / ".engineering"
        assert reports_dir == tmp_path / ".engineering" / "reports"

    def test_trading_bot_config_respects_different_report_dir_layout(
        self, tmp_path: Path
    ) -> None:
        """Trading-bot config creates engineering/ and reports/ at repo root."""
        config = _trading_bot_config(tmp_path)
        result = ensure_project_runtime_dirs(config)

        # Trading-bot workflow_store_path.parent = engineering/
        # event_store_path.parent = engineering/
        # report_dir = reports/ (parent = repo_root/, always exists)
        created_set = set(result.created_paths)
        assert tmp_path / "engineering" in created_set
        # reports/ parent is repo_root/ which always exists,
        # so report_dir itself (reports/) should be created
        assert tmp_path / "reports" in created_set

    def test_fantasy_config_creates_only_fantasy_dirs(
        self, tmp_path: Path
    ) -> None:
        """Fantasy config creates only fantasy .engineering/, not trading-bot paths."""
        config = _fantasy_config(tmp_path)
        result = ensure_project_runtime_dirs(config)

        created_str = [str(p) for p in result.created_paths]
        for path_str in created_str:
            assert ".engineering" in path_str
            assert "trading-bot" not in path_str
            assert "engineering/" not in path_str.replace(".engineering/", "")

    def test_trading_bot_config_creates_only_trading_bot_dirs(
        self, tmp_path: Path
    ) -> None:
        """Trading-bot config creates only trading-bot paths, not fantasy paths."""
        config = _trading_bot_config(tmp_path)
        result = ensure_project_runtime_dirs(config)

        created_str = [str(p) for p in result.created_paths]
        for path_str in created_str:
            assert ".engineering" not in path_str
            assert "fantasy" not in path_str

    def test_path_escape_rejected_before_writes(self, tmp_path: Path) -> None:
        """Config with path escaping repo_root raises RuntimeInitError before writes."""
        # Create a workflow path that escapes via ".." traversal
        evil_config = ProjectConfig(
            schema_version="1.0",
            project_id="evil",
            display_name="Evil",
            repository_root=tmp_path,
            authoritative_base_branch="main",
            governance_files=GovernanceFiles(
                backlog_path=tmp_path / "AGENT_BACKLOG.md",
                operating_plan_path=tmp_path / "AGENT_OPERATING_PLAN.md",
                owners_path=tmp_path / "OWNERS.md",
                handoff_path=tmp_path / "AUTONOMOUS_ENGINEERING_HANDOFF.md",
            ),
            workflow_files=WorkflowFiles(
                workflow_store_path=tmp_path / ".." / "outside" / "workflow_store.json",
                event_store_path=tmp_path / "engineering" / "event_store.db",
                report_dir=tmp_path / "reports",
            ),
            qa_commands=("python -m pytest",),
            qa_timeout_seconds=300,
            prohibited_operations=(),
            agents_may_merge=False,
            owner_ids=("josh",),
            agent_owners=("test-agent",),
        )

        with pytest.raises(RuntimeInitError, match="escapes repository_root"):
            ensure_project_runtime_dirs(evil_config)

        # Verify nothing was created in tmp_path
        assert list(tmp_path.iterdir()) == []

    def test_symlink_escape_rejected_before_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Symlink pointing outside repo_root raises RuntimeInitError before writes."""
        # Create a directory that contains a symlink to outside
        real_dir = tmp_path / "real_project"
        real_dir.mkdir()
        # Create a symlink inside real_project that points outside
        outside_target = tmp_path.parent / "outside_target"
        outside_target.mkdir(exist_ok=True)
        symlink_path = real_dir / "escape_link"
        symlink_path.symlink_to(outside_target)

        # Config uses real_project as root but workflow path goes through symlink
        evil_config = ProjectConfig(
            schema_version="1.0",
            project_id="escape-test",
            display_name="Escape Test",
            repository_root=real_dir,
            authoritative_base_branch="main",
            governance_files=GovernanceFiles(
                backlog_path=real_dir / "AGENT_BACKLOG.md",
                operating_plan_path=real_dir / "AGENT_OPERATING_PLAN.md",
                owners_path=real_dir / "OWNERS.md",
                handoff_path=real_dir / "AUTONOMOUS_ENGINEERING_HANDOFF.md",
            ),
            workflow_files=WorkflowFiles(
                # Path resolves to outside_target via symlink
                workflow_store_path=symlink_path / "workflow_store.json",
                event_store_path=real_dir / "engineering" / "event_store.db",
                report_dir=real_dir / "reports",
            ),
            qa_commands=("python -m pytest",),
            qa_timeout_seconds=300,
            prohibited_operations=(),
            agents_may_merge=False,
            owner_ids=("josh",),
            agent_owners=("test-agent",),
        )

        with pytest.raises(RuntimeInitError, match="escapes repository_root"):
            ensure_project_runtime_dirs(evil_config)

        # Verify nothing was created
        assert not (real_dir / "engineering").exists()
        assert not (real_dir / "reports").exists()

    def test_regular_file_conflict_rejected_before_writes(
        self, tmp_path: Path
    ) -> None:
        """Regular file at .engineering/ path raises RuntimeInitError before writes."""
        config = _fantasy_config(tmp_path)
        engineering_blocker = tmp_path / ".engineering"
        engineering_blocker.write_text("I am a file, not a directory", encoding="utf-8")

        with pytest.raises(RuntimeInitError, match="non-directory"):
            ensure_project_runtime_dirs(config)

    def test_conflicting_report_dir_rejected(self, tmp_path: Path) -> None:
        """report_dir that is a regular file raises RuntimeInitError."""
        config = _fantasy_config(tmp_path)
        # Create .engineering/ first (so first preflight passes)
        (tmp_path / ".engineering").mkdir()
        # But make reports a file
        reports_blocker = tmp_path / ".engineering" / "reports"
        reports_blocker.write_text("I am a file", encoding="utf-8")

        with pytest.raises(RuntimeInitError, match="non-directory"):
            ensure_project_runtime_dirs(config)

    def test_registry_loading_creates_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_registry() with skip_workflow_files=True creates zero directories.

        This test verifies that the registry module does not call runtime init.
        Registry loading requires governance files to exist (skip_workflow_files=True
        only bypasses workflow parent-dir checks, not governance existence checks).
        After creating minimal governance files, registry loading should NOT create
        .engineering/. Calling ensure_project_runtime_dirs separately does.
        """
        import json

        # Create minimal governance files (required by _validate_without_workflow_dirs)
        (tmp_path / "AGENT_BACKLOG.md").write_text("# Backlog", encoding="utf-8")
        (tmp_path / "AGENT_OPERATING_PLAN.md").write_text("# Plan", encoding="utf-8")
        (tmp_path / "OWNERS.md").write_text("# Owners", encoding="utf-8")
        (tmp_path / "AUTONOMOUS_ENGINEERING_HANDOFF.md").write_text(
            "# Handoff", encoding="utf-8"
        )

        registry_path = tmp_path / "registry.json"
        fantasy_entry = {
            "schema_version": "1.0",
            "project_id": "fantasy-draft-command-center",
            "display_name": "Fantasy",
            "repository_root": str(tmp_path),
            "authoritative_base_branch": "main",
            "governance_files": {
                "backlog_path": str(tmp_path / "AGENT_BACKLOG.md"),
                "operating_plan_path": str(tmp_path / "AGENT_OPERATING_PLAN.md"),
                "owners_path": str(tmp_path / "OWNERS.md"),
                "handoff_path": str(tmp_path / "AUTONOMOUS_ENGINEERING_HANDOFF.md"),
            },
            "workflow_files": {
                "workflow_store_path": str(tmp_path / ".engineering" / "workflow_store.json"),
                "event_store_path": str(tmp_path / ".engineering" / "event_store.db"),
                "report_dir": str(tmp_path / ".engineering" / "reports"),
            },
            "qa_commands": ["npm test"],
            "qa_timeout_seconds": 300,
            "prohibited_operations": [],
            "agents_may_merge": False,
            "owner_ids": ["josh"],
            "agent_owners": ["fantasy-agent"],
        }
        registry_path.write_text(
            json.dumps({"registry_version": "1", "projects": [fantasy_entry]}),
            encoding="utf-8",
        )

        # Before loading registry, .engineering/ does not exist
        assert not (tmp_path / ".engineering").exists()

        # Load registry (skip_workflow_files=True bypasses the .engineering/ parent-dir check)
        from engineering.registry import load_registry

        registry = load_registry(registry_path, skip_workflow_files=True)

        # Registry loading should NOT create .engineering/
        assert not (tmp_path / ".engineering").exists()

        # Now calling ensure_project_runtime_dirs explicitly creates them
        cfg = registry.projects["fantasy-draft-command-center"]
        ensure_project_runtime_dirs(cfg)

        assert (tmp_path / ".engineering").is_dir()

    def test_no_workflow_event_files_created(self, tmp_path: Path) -> None:
        """ensure_project_runtime_dirs does NOT create workflow_store.json or event_store.db."""
        config = _fantasy_config(tmp_path)
        ensure_project_runtime_dirs(config)

        assert not (tmp_path / ".engineering" / "workflow_store.json").exists()
        assert not (tmp_path / ".engineering" / "event_store.db").exists()

    def test_no_governance_files_changed(self, tmp_path: Path) -> None:
        """Governance files are not modified by runtime initialization."""
        # Create minimal governance files
        (tmp_path / "AGENT_BACKLOG.md").write_text("# Backlog", encoding="utf-8")
        (tmp_path / "AGENT_OPERATING_PLAN.md").write_text("# Plan", encoding="utf-8")
        (tmp_path / "OWNERS.md").write_text("# Owners", encoding="utf-8")
        (tmp_path / "AUTONOMOUS_ENGINEERING_HANDOFF.md").write_text(
            "# Handoff", encoding="utf-8"
        )

        original_hashes = {
            "AGENT_BACKLOG.md": (
                tmp_path / "AGENT_BACKLOG.md"
            ).stat().st_mtime,
            "AGENT_OPERATING_PLAN.md": (
                tmp_path / "AGENT_OPERATING_PLAN.md"
            ).stat().st_mtime,
            "OWNERS.md": (tmp_path / "OWNERS.md").stat().st_mtime,
            "AUTONOMOUS_ENGINEERING_HANDOFF.md": (
                tmp_path / "AUTONOMOUS_ENGINEERING_HANDOFF.md"
            ).stat().st_mtime,
        }

        config = _fantasy_config(tmp_path)
        ensure_project_runtime_dirs(config)

        current_hashes = {
            "AGENT_BACKLOG.md": (
                tmp_path / "AGENT_BACKLOG.md"
            ).stat().st_mtime,
            "AGENT_OPERATING_PLAN.md": (
                tmp_path / "AGENT_OPERATING_PLAN.md"
            ).stat().st_mtime,
            "OWNERS.md": (tmp_path / "OWNERS.md").stat().st_mtime,
            "AUTONOMOUS_ENGINEERING_HANDOFF.md": (
                tmp_path / "AUTONOMOUS_ENGINEERING_HANDOFF.md"
            ).stat().st_mtime,
        }

        assert original_hashes == current_hashes

    def test_no_git_mutation(self, tmp_path: Path) -> None:
        """ensure_project_runtime_dirs does not run git commands."""
        config = _fantasy_config(tmp_path)

        # Initialize a git repo in tmp_path to have something to check
        os.system(f"git init {tmp_path} > /dev/null 2>&1")

        # Get HEAD before
        import subprocess

        before = (
            subprocess.run(
                ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if (tmp_path / ".git").exists()
            else "no-git"
        )

        ensure_project_runtime_dirs(config)

        after = (
            subprocess.run(
                ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if (tmp_path / ".git").exists()
            else "no-git"
        )

        assert before == after

    def test_partial_layout_handled_idempotently(self, tmp_path: Path) -> None:
        """Partial layout already present is handled idempotently."""
        config = _fantasy_config(tmp_path)

        # Create .engineering/ but not .engineering/reports/
        (tmp_path / ".engineering").mkdir()

        result = ensure_project_runtime_dirs(config)

        assert (tmp_path / ".engineering" / "reports").is_dir()
        created_paths_set = set(result.created_paths)
        # .engineering/ already existed; only .engineering/reports/ should be created
        assert tmp_path / ".engineering" not in created_paths_set
        assert tmp_path / ".engineering" / "reports" in created_paths_set


class TestRuntimeInitializationResult:
    """Tests for RuntimeInitializationResult dataclass."""

    def test_frozen_dataclass(self, tmp_path: Path) -> None:
        """Result is immutable (frozen=True)."""
        config = _fantasy_config(tmp_path)
        result = ensure_project_runtime_dirs(config)

        with pytest.raises(AttributeError):
            result.created_paths = ()  # type: ignore[misc]

    def test_warning_field_present(self, tmp_path: Path) -> None:
        """warnings field is present and may be empty."""
        config = _fantasy_config(tmp_path)
        result = ensure_project_runtime_dirs(config)

        assert isinstance(result.warnings, tuple)
        # With clean inputs, no warnings expected
        assert result.warnings == ()

    def test_created_paths_is_tuple(self, tmp_path: Path) -> None:
        """created_paths is a tuple (not list)."""
        config = _fantasy_config(tmp_path)
        result = ensure_project_runtime_dirs(config)

        assert isinstance(result.created_paths, tuple)


class TestRuntimeInitError:
    """Tests for RuntimeInitError exception."""

    def test_escape_error_message(self, tmp_path: Path) -> None:
        """Error message identifies the escaping path field."""
        evil_config = ProjectConfig(
            schema_version="1.0",
            project_id="evil",
            display_name="Evil",
            repository_root=tmp_path,
            authoritative_base_branch="main",
            governance_files=GovernanceFiles(
                backlog_path=tmp_path / "AGENT_BACKLOG.md",
                operating_plan_path=tmp_path / "AGENT_OPERATING_PLAN.md",
                owners_path=tmp_path / "OWNERS.md",
                handoff_path=tmp_path / "AUTONOMOUS_ENGINEERING_HANDOFF.md",
            ),
            workflow_files=WorkflowFiles(
                workflow_store_path=tmp_path / ".." / "evil.json",
                event_store_path=tmp_path / "engineering" / "event_store.db",
                report_dir=tmp_path / "reports",
            ),
            qa_commands=("python -m pytest",),
            qa_timeout_seconds=300,
            prohibited_operations=(),
            agents_may_merge=False,
            owner_ids=("josh",),
            agent_owners=("test-agent",),
        )

        with pytest.raises(RuntimeInitError, match="escapes"):
            ensure_project_runtime_dirs(evil_config)

    def test_non_directory_error_message(self, tmp_path: Path) -> None:
        """Error message identifies the conflicting non-directory path."""
        config = _fantasy_config(tmp_path)
        (tmp_path / ".engineering").write_text("blocker", encoding="utf-8")

        with pytest.raises(RuntimeInitError, match="non-directory"):
            ensure_project_runtime_dirs(config)
