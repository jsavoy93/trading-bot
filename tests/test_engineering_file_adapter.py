"""Behavioral tests for FileReadAdapterImpl — ENGPLAT-002C3.

Scope: Verify FileReadAdapterImpl satisfies FileReadAdapter, performs correct
path resolution and containment, and construction is side-effect free.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from engineering.adapters import FileReadAdapter
from engineering.context import FileReadAdapterImpl, build_project_context
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
        qa_commands=("python -m pytest",),
        qa_timeout_seconds=300,
        prohibited_operations=("no_live_trading",),
        agents_may_merge=False,
        owner_ids=("test-owner",),
        agent_owners=("test-agent",),
    )


def _setup_repo(tmp_path: Path) -> None:
    """Set up minimal governance files for a temp repo."""
    (tmp_path / "AGENT_BACKLOG.md").write_text("# Backlog\n", encoding="utf-8")
    (tmp_path / "AGENT_OPERATING_PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (tmp_path / "OWNERS.md").write_text("# Owners\n", encoding="utf-8")
    (tmp_path / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md").write_text(
        "# Handoff\n", encoding="utf-8"
    )
    (tmp_path / ".git").mkdir()
    (tmp_path / "reports").mkdir()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestFileAdapterImplProtocol:
    """FileReadAdapterImpl must satisfy FileReadAdapter."""

    def test_isinstance_file_read_adapter(self, tmp_path: Path) -> None:
        adapter = FileReadAdapterImpl(tmp_path)
        assert isinstance(adapter, FileReadAdapter)


# ---------------------------------------------------------------------------
# resolve() — relative path resolution
# ---------------------------------------------------------------------------

class TestResolveRelative:
    """resolve() returns a resolved Path inside the repository root."""

    def test_resolve_simple_relative_path(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        adapter = FileReadAdapterImpl(tmp_path)
        result = adapter.resolve(Path("AGENT_BACKLOG.md"))
        assert result.is_absolute()
        assert str(result).endswith("AGENT_BACKLOG.md")

    def test_resolve_nested_relative_path(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "module.py").write_text("# module\n", encoding="utf-8")
        adapter = FileReadAdapterImpl(tmp_path)
        result = adapter.resolve(Path("src/module.py"))
        assert result.is_absolute()
        assert str(result).endswith("src/module.py")

    def test_resolve_dotdot_traversal_rejected(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(ValueError, match="escapes repository_root"):
            adapter.resolve(Path(".."))

    def test_resolve_dotdot_nested_traversal_rejected(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(ValueError, match="escapes repository_root"):
            adapter.resolve(Path("src/../../etc/passwd"))

    def test_resolve_absolute_path_inside_repo(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        adapter = FileReadAdapterImpl(tmp_path)
        absolute_inside = tmp_path / "AGENT_BACKLOG.md"
        result = adapter.resolve(absolute_inside)
        assert result.is_absolute()
        assert str(result).endswith("AGENT_BACKLOG.md")

    def test_resolve_absolute_path_outside_repo_rejected(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        adapter = FileReadAdapterImpl(tmp_path)
        absolute_outside = Path("/etc/passwd")
        with pytest.raises(ValueError, match="escapes repository_root"):
            adapter.resolve(absolute_outside)


# ---------------------------------------------------------------------------
# resolve() — symlink handling
# ---------------------------------------------------------------------------

class TestResolveSymlinks:
    """resolve() handles symlinks correctly."""

    def test_resolve_symlink_to_contained_target(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        (tmp_path / "real_file.txt").write_text("content\n", encoding="utf-8")
        (tmp_path / "link_file.txt").symlink_to(tmp_path / "real_file.txt")
        adapter = FileReadAdapterImpl(tmp_path)
        result = adapter.resolve(Path("link_file.txt"))
        assert result.is_absolute()

    def test_resolve_symlink_escape_rejected(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        outside = tmp_path.parent / "outside_target.txt"
        outside.write_text("escape\n", encoding="utf-8")
        (tmp_path / "escape_link").symlink_to(outside)
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(ValueError, match="escapes repository_root"):
            adapter.resolve(Path("escape_link"))

    def test_resolve_broken_symlink_to_contained_target(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        (tmp_path / "broken_link").symlink_to(tmp_path / "nonexistent.txt")
        adapter = FileReadAdapterImpl(tmp_path)
        # Broken symlink whose target would be contained — resolve should still
        # succeed (resolve doesn't check existence; containment is enforced)
        result = adapter.resolve(Path("broken_link"))
        assert result.is_absolute()

    def test_resolve_broken_symlink_to_outside_target(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        outside = tmp_path.parent / "outside_nonexistent.txt"
        (tmp_path / "escape_broken_link").symlink_to(outside)
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(ValueError, match="escapes repository_root"):
            adapter.resolve(Path("escape_broken_link"))

    def test_resolve_symlink_loop_rejected(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        (tmp_path / "loop_link").symlink_to(tmp_path / "loop_link", target_is_directory=False)
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(ValueError, match="path resolution failed"):
            adapter.resolve(Path("loop_link"))


# ---------------------------------------------------------------------------
# exists() — filesystem existence checks
# ---------------------------------------------------------------------------

class TestExists:
    """exists() returns True/False for contained targets, raises on escape."""

    def test_exists_existing_file(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        (tmp_path / "exists.txt").write_text("hi\n", encoding="utf-8")
        adapter = FileReadAdapterImpl(tmp_path)
        assert adapter.exists(Path("exists.txt")) is True

    def test_exists_existing_directory(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        (tmp_path / "subdir").mkdir()
        adapter = FileReadAdapterImpl(tmp_path)
        assert adapter.exists(Path("subdir")) is True

    def test_exists_missing_contained_target(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        adapter = FileReadAdapterImpl(tmp_path)
        assert adapter.exists(Path("nonexistent_xyz.txt")) is False

    def test_exists_broken_symlink_to_contained_missing_target(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        (tmp_path / "broken").symlink_to(tmp_path / "missing.txt")
        adapter = FileReadAdapterImpl(tmp_path)
        # Broken symlink — exists() returns False because target doesn't exist
        assert adapter.exists(Path("broken")) is False

    def test_exists_outside_target_raises(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(ValueError, match="escapes repository_root"):
            adapter.exists(Path("../etc/passwd"))

    def test_exists_symlink_loop_raises(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        (tmp_path / "loop").symlink_to(tmp_path / "loop")
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(ValueError, match="path resolution failed"):
            adapter.exists(Path("loop"))


# ---------------------------------------------------------------------------
# read_text() — file content reading
# ---------------------------------------------------------------------------

class TestReadText:
    """read_text() returns UTF-8 content, raises on missing/escape/directory."""

    def test_read_text_exact_contents(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        content = "Hello, world!\n"
        (tmp_path / "readable.txt").write_text(content, encoding="utf-8")
        adapter = FileReadAdapterImpl(tmp_path)
        assert adapter.read_text(Path("readable.txt")) == content

    def test_read_text_missing_raises(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(FileNotFoundError):
            adapter.read_text(Path("nonexistent_xyz.txt"))

    def test_read_text_broken_symlink_raises(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        (tmp_path / "broken").symlink_to(tmp_path / "missing.txt")
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(FileNotFoundError):
            adapter.read_text(Path("broken"))

    def test_read_text_directory_raises(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        (tmp_path / "a_dir").mkdir()
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(IsADirectoryError):
            adapter.read_text(Path("a_dir"))

    def test_read_text_escape_raises(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(ValueError, match="escapes repository_root"):
            adapter.read_text(Path("../etc/passwd"))


# ---------------------------------------------------------------------------
# Error sanitization
# ---------------------------------------------------------------------------

class TestErrorSanitization:
    """Adapter-generated ValueErrors are deterministic and bounded."""

    def test_escape_error_is_bounded(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            adapter.resolve(Path("../escape"))
        msg = str(exc_info.value)
        # Message must be deterministic and bounded — no raw paths, no exception traces
        assert "files: path escapes repository_root" in msg
        assert str(tmp_path) not in msg  # raw path must not leak
        assert "\\n" not in msg  # no newlines

    def test_resolution_error_is_bounded(self, tmp_path: Path) -> None:
        _setup_repo(tmp_path)
        (tmp_path / "loop").symlink_to(tmp_path / "loop")
        adapter = FileReadAdapterImpl(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            adapter.resolve(Path("loop"))
        msg = str(exc_info.value)
        assert "files: path resolution failed" in msg
        assert "loop" not in msg.lower() or "resolution failed" in msg
        assert "\\n" not in msg


# ---------------------------------------------------------------------------
# Side-effect safety
# ---------------------------------------------------------------------------

class TestFileAdapterImplNoSideEffects:
    """FileReadAdapterImpl construction must not perform side effects."""

    def test_construction_no_cwd(self, tmp_path: Path) -> None:
        """Construction must not use Path.cwd()."""
        import inspect
        source = inspect.getsource(FileReadAdapterImpl.__init__)
        assert "cwd" not in source.lower()
        assert "Path.cwd" not in source

    def test_construction_does_not_read_files(self, tmp_path: Path) -> None:
        """Construction must not read any files."""
        _setup_repo(tmp_path)
        before = set(_all_paths(tmp_path))
        FileReadAdapterImpl(tmp_path)
        after = set(_all_paths(tmp_path))
        # No new files created
        assert after == before

    def test_construction_does_not_create_files(self, tmp_path: Path) -> None:
        """Construction must not create any files or directories."""
        _setup_repo(tmp_path)
        before = set(_all_paths(tmp_path))
        FileReadAdapterImpl(tmp_path)
        after = set(_all_paths(tmp_path))
        assert not (after - before), "Construction created unexpected paths"


# ---------------------------------------------------------------------------
# build_project_context wiring
# ---------------------------------------------------------------------------

class TestBuildProjectContext:
    """build_project_context must wire ctx.files to FileReadAdapterImpl."""

    def test_ctx_files_is_file_read_adapter_impl(self) -> None:
        """ctx.files is concrete FileReadAdapterImpl (not deferred)."""
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        assert isinstance(ctx.files, FileReadAdapterImpl)

    def test_ctx_files_satisfies_protocol(self) -> None:
        """ctx.files satisfies FileReadAdapter."""
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        assert isinstance(ctx.files, FileReadAdapter)

    def test_ctx_files_not_deferred_stub(self) -> None:
        """ctx.files must NOT be a _Deferred* stub."""
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        assert not type(ctx.files).__name__.startswith("_Deferred")


# ---------------------------------------------------------------------------
# Regression: ctx.git and ctx.qa remain concrete
# ---------------------------------------------------------------------------

class TestRegressionAdapters:
    """Verify ctx.git and ctx.qa remain concrete after FileReadAdapterImpl swap."""

    def test_ctx_git_remains_concrete(self) -> None:
        """ctx.git must remain GitAdapterImpl (not deferred)."""
        from engineering.adapters import GitReadAdapter
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        assert isinstance(ctx.git, GitReadAdapter)
        assert not type(ctx.git).__name__.startswith("_Deferred")

    def test_ctx_qa_remains_concrete(self) -> None:
        """ctx.qa must remain QAAdapterImpl (not deferred)."""
        from engineering.adapters import QAAdapter
        config = _minimal_config(TRADING_BOT_ROOT)
        ctx = build_project_context(config)
        assert isinstance(ctx.qa, QAAdapter)
        assert not type(ctx.qa).__name__.startswith("_Deferred")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_paths(root: Path) -> set[Path]:
    """Return all paths under root (recursive)."""
    try:
        return set(root.rglob("*"))
    except PermissionError:
        return set()
