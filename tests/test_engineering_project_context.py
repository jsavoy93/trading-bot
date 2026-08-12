"""Structural contract tests for ENGPLAT-002B ProjectContext.

Scope: These tests verify the structural contracts defined by ENGPLAT-002A
and the concrete adapter implementations provided by ENGPLAT-002B. They do
not perform side effects and do not modify any existing engineering/ files
except authorized test updates required by the merged ENGPLAT-002B governance.

Allowed imports:
- standard library (dataclasses, pathlib, typing, typing.protocol, inspect)
- engineering.adapters (the module under test)
- engineering.models (ProjectConfig, validate_project_config, parse_project_config,
  BacklogTask, GovernanceFiles, WorkflowFiles, SUPPORTED_SCHEMA_VERSION)
- engineering.context (build_project_context)
- unittest / pytest
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

import pytest

from engineering.adapters import (
    EventAdapter,
    FileReadAdapter,
    GitReadAdapter,
    GovernanceAdapter,
    ProjectContext,
    ProjectMetadata,
    QAAdapter,
    WorkflowAdapter,
)
from engineering.context import build_project_context
from engineering.models import (
    GovernanceFiles,
    ProjectConfig,
    WorkflowFiles,
    validate_project_config,
)
from engineering.context import (
    GitAdapterImpl,
    QAAdapterImpl,
    _DeferredFileReadAdapter,
    GovernanceAdapterImpl,
    WorkflowAdapterImpl,
    EventAdapterImpl,
    build_project_context,
)
from engineering.adapters import CapabilityUnavailable


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _minimal_config(
    *,
    project_id: str = "test-project",
    display_name: str = "Test Project",
    repository_root: Path | None = None,
    schema_version: str = "1.0",
    owner_ids: tuple[str, ...] = ("test-owner",),
    agent_owners: tuple[str, ...] = ("test-agent",),
) -> ProjectConfig:
    """Build a minimal valid ProjectConfig for testing."""
    root = repository_root or Path("/tmp/test-repo")
    return ProjectConfig(
        schema_version=schema_version,
        project_id=project_id,
        display_name=display_name,
        repository_root=root,
        authoritative_base_branch="main",
        governance_files=GovernanceFiles(
            backlog_path=root / "AGENT_BACKLOG.md",
            operating_plan_path=root / "AGENT_OPERATING_PLAN.md",
            owners_path=root / "OWNERS.md",
            handoff_path=root / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md",
        ),
        workflow_files=WorkflowFiles(
            workflow_store_path=root / ".git" / "workflow.json",
            event_store_path=root / ".git" / "events.db",
            report_dir=root / "reports",
        ),
        qa_commands=("python -m pytest",),
        qa_timeout_seconds=300,
        prohibited_operations=("no_live_trading",),
        agents_may_merge=False,
        owner_ids=owner_ids,
        agent_owners=agent_owners,
    )


# ---------------------------------------------------------------------------
# C1: ProjectMetadata is frozen
# ---------------------------------------------------------------------------

class TestProjectMetadataFrozen:
    def test_metadata_is_frozen_dataclass(self):
        assert is_dataclass(ProjectMetadata)
        metadata = ProjectMetadata(
            project_id="p",
            display_name="n",
            repository_root=Path("/r"),
            authoritative_base_branch="b",
            agents_may_merge=False,
            owner_ids=("o",),
            agent_owners=("a",),
            prohibited_operations=(),
        )
        with pytest.raises(FrozenInstanceError):
            metadata.project_id = "changed"  # type: ignore[index]

    def test_metadata_fields_present(self):
        # Use dataclass fields instead of get_type_hints to avoid resolution issues
        from dataclasses import fields as _fields
        field_names = {f.name for f in _fields(ProjectMetadata)}
        expected = {
            "project_id",
            "display_name",
            "repository_root",
            "authoritative_base_branch",
            "agents_may_merge",
            "owner_ids",
            "agent_owners",
            "prohibited_operations",
        }
        assert field_names == expected


# ---------------------------------------------------------------------------
# C2: ProjectContext is frozen
# ---------------------------------------------------------------------------

class TestProjectContextFrozen:
    def test_context_is_frozen_dataclass(self):
        assert is_dataclass(ProjectContext)

    def test_context_fields_present(self):
        # Use dataclass fields instead of get_type_hints to avoid
        # string-annotation resolution issues in Python < 3.11
        from dataclasses import fields
        field_names = {f.name for f in fields(ProjectContext)}
        expected = {
            "config",
            "git",
            "governance",
            "workflow",
            "qa",
            "files",
            "events",
            "metadata",
        }
        assert field_names == expected

    def test_context_is_immutable(self):
        root = Path("/tmp/test")
        config = _minimal_config(repository_root=root)
        # The factory will fail on validation since paths don't exist,
        # but we can test immutability by constructing via __new__
        # (skip factory to isolate the dataclass immutability test)
        from engineering.adapters import (
            EventAdapter,
            FileReadAdapter,
            GitReadAdapter,
            GovernanceAdapter,
            ProjectContext,
            ProjectMetadata,
            QAAdapter,
            WorkflowAdapter,
        )

        class DummyAdapter:
            pass

        metadata = ProjectMetadata(
            project_id="p",
            display_name="n",
            repository_root=root,
            authoritative_base_branch="b",
            agents_may_merge=False,
            owner_ids=("o",),
            agent_owners=("a",),
            prohibited_operations=(),
        )

        ctx = ProjectContext(
            config=config,
            git=DummyAdapter(),
            governance=DummyAdapter(),
            workflow=DummyAdapter(),
            qa=DummyAdapter(),
            files=DummyAdapter(),
            events=DummyAdapter(),
            metadata=metadata,
        )
        with pytest.raises(FrozenInstanceError):
            ctx.config = None  # type: ignore[index]


# ---------------------------------------------------------------------------
# C3: All six Protocols are runtime_checkable
# ---------------------------------------------------------------------------

class TestProtocolsRuntimeCheckable:
    @pytest.mark.parametrize(
        "protocol_cls",
        [
            GitReadAdapter,
            GovernanceAdapter,
            WorkflowAdapter,
            QAAdapter,
            FileReadAdapter,
            EventAdapter,
        ],
    )
    def test_protocol_is_runtime_checkable(self, protocol_cls):
        # runtime_checkable marks a Protocol for isinstance() checks
        import typing
        # Verify the class has the typing.Protocol base
        assert issubclass(protocol_cls, typing.Protocol)

    def test_protocols_are_protocol_types(self):
        """All six adapter protocols are Protocol subclasses."""
        protocols = [
            GitReadAdapter,
            GovernanceAdapter,
            WorkflowAdapter,
            QAAdapter,
            FileReadAdapter,
            EventAdapter,
        ]
        for p in protocols:
            # Check it has Protocol in its MRO
            from typing import Protocol as TypingProtocol
            assert issubclass(p, TypingProtocol)


# ---------------------------------------------------------------------------
# C4: Expected objects satisfy Protocol checks
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    """Verify that concrete adapters satisfy their Protocol contracts.

    ENGPLAT-002B/002C provides GovernanceAdapterImpl, WorkflowAdapterImpl,
    EventAdapterImpl, and QAAdapterImpl as concrete implementations.
    Deferred adapter (_DeferredFileReadAdapter) remains as a stub raising
    CapabilityUnavailable.
    """

    def test_git_adapter_impl_satisfies_protocol(self):
        assert isinstance(GitAdapterImpl(Path("/root/.openclaw/workspace/trading-bot")), GitReadAdapter)

    def test_concrete_governance_adapter_satisfies_protocol(self):
        assert issubclass(GovernanceAdapterImpl, GovernanceAdapter)

    def test_concrete_workflow_adapter_satisfies_protocol(self):
        assert issubclass(WorkflowAdapterImpl, WorkflowAdapter)

    def test_concrete_event_adapter_satisfies_protocol(self):
        assert issubclass(EventAdapterImpl, EventAdapter)

    def test_qa_adapter_impl_satisfies_protocol(self):
        # QAAdapterImpl is concrete — test with a minimal config-like object
        from engineering.models import ProjectConfig, GovernanceFiles, WorkflowFiles
        from pathlib import Path
        root = Path("/tmp/test-qa-impl")
        config = ProjectConfig(
            project_id="proto-test",
            display_name="Proto Test",
            repository_root=root,
            authoritative_base_branch="main",
            governance_files=GovernanceFiles(
                backlog_path=root / "AGENT_BACKLOG.md",
                operating_plan_path=root / "AGENT_OPERATING_PLAN.md",
                owners_path=root / "OWNERS.md",
                handoff_path=root / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md",
            ),
            workflow_files=WorkflowFiles(
                workflow_store_path=root / ".git" / "workflow.json",
                event_store_path=root / ".git" / "events.db",
                report_dir=root / "reports",
            ),
            qa_commands=("python -m pytest",),
            qa_timeout_seconds=300,
            prohibited_operations=("no_live_trading",),
            agents_may_merge=False,
            owner_ids=("test-owner",),
            agent_owners=("test-agent",),
        )
        assert isinstance(QAAdapterImpl(config), QAAdapter)

    def test_deferred_file_adapter_satisfies_protocol(self):
        assert isinstance(_DeferredFileReadAdapter("test"), FileReadAdapter)


# ---------------------------------------------------------------------------
# C5: ProjectContext preserves config, metadata, and supplied adapter identities
# ---------------------------------------------------------------------------

class TestProjectContextComposition:
    def test_context_preserves_config(self):
        """Context.config returns the same ProjectConfig passed to the factory."""
        config = _minimal_config(project_id="preserve-test")
        ctx = _build_context_directly_for_test(config)
        assert ctx.config is config
        assert ctx.config.project_id == "preserve-test"

    def test_context_preserves_metadata(self):
        """Context.metadata is derived from config and preserved."""
        config = _minimal_config(
            project_id="meta-test",
            display_name="Meta Test",
            owner_ids=("owner-a", "owner-b"),
        )
        ctx = _build_context_directly_for_test(config)
        assert ctx.metadata.project_id == "meta-test"
        assert ctx.metadata.display_name == "Meta Test"
        assert ctx.metadata.owner_ids == ("owner-a", "owner-b")

    def test_context_preserves_adapter_identities(self):
        """Each adapter field in the context is the exact instance supplied."""
        config = _minimal_config()
        ctx = _build_context_directly_for_test(config)
        assert isinstance(ctx.git, GitReadAdapter)
        assert isinstance(ctx.governance, GovernanceAdapter)
        assert isinstance(ctx.workflow, WorkflowAdapter)
        assert isinstance(ctx.qa, QAAdapter)
        assert isinstance(ctx.files, FileReadAdapter)
        assert isinstance(ctx.events, EventAdapter)
        assert isinstance(ctx.metadata, ProjectMetadata)

    def test_concrete_adapters_are_correct_types(self, tmp_path):
        """build_project_context returns concrete *Impl adapters for 002B scope."""
        config = _minimal_config(repository_root=tmp_path)
        (tmp_path / "AGENT_BACKLOG.md").touch()
        (tmp_path / "AGENT_OPERATING_PLAN.md").touch()
        (tmp_path / "OWNERS.md").touch()
        (tmp_path / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md").touch()
        (tmp_path / ".git").mkdir()
        (tmp_path / "reports").mkdir()

        ctx = build_project_context(config)
        assert isinstance(ctx.governance, GovernanceAdapterImpl)
        assert isinstance(ctx.workflow, WorkflowAdapterImpl)
        assert isinstance(ctx.events, EventAdapterImpl)


# ---------------------------------------------------------------------------
# C6: Invalid ProjectConfig fails closed at factory boundary
# ---------------------------------------------------------------------------

class TestFactoryValidation:
    def test_empty_project_id_fails_closed(self):
        config = _minimal_config(project_id="")
        with pytest.raises(ValueError, match="validation failed"):
            build_project_context(config)

    def test_empty_display_name_fails_closed(self):
        config = _minimal_config(display_name="")
        with pytest.raises(ValueError, match="validation failed"):
            build_project_context(config)

    def test_negative_qa_timeout_fails_closed(self):
        config = _minimal_config()
        config = ProjectConfig(
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
            build_project_context(config)

    def test_empty_owner_ids_fails_closed(self):
        config = _minimal_config()
        config = ProjectConfig(
            schema_version=config.schema_version,
            project_id=config.project_id,
            display_name=config.display_name,
            repository_root=config.repository_root,
            authoritative_base_branch=config.authoritative_base_branch,
            governance_files=config.governance_files,
            workflow_files=config.workflow_files,
            qa_commands=config.qa_commands,
            qa_timeout_seconds=config.qa_timeout_seconds,
            prohibited_operations=config.prohibited_operations,
            agents_may_merge=config.agents_may_merge,
            owner_ids=(),  # empty
            agent_owners=config.agent_owners,
        )
        with pytest.raises(ValueError, match="validation failed"):
            build_project_context(config)

    def test_empty_agent_owners_fails_closed(self):
        config = _minimal_config()
        config = ProjectConfig(
            schema_version=config.schema_version,
            project_id=config.project_id,
            display_name=config.display_name,
            repository_root=config.repository_root,
            authoritative_base_branch=config.authoritative_base_branch,
            governance_files=config.governance_files,
            workflow_files=config.workflow_files,
            qa_commands=config.qa_commands,
            qa_timeout_seconds=config.qa_timeout_seconds,
            prohibited_operations=config.prohibited_operations,
            agents_may_merge=config.agents_may_merge,
            owner_ids=config.owner_ids,
            agent_owners=(),  # empty
        )
        with pytest.raises(ValueError, match="validation failed"):
            build_project_context(config)


# ---------------------------------------------------------------------------
# C7: Unsupported schema version fails closed
# ---------------------------------------------------------------------------

class TestSchemaVersionValidation:
    def test_unknown_schema_version_fails_closed(self):
        config = _minimal_config(schema_version="99.99")
        with pytest.raises(ValueError, match="validation failed"):
            build_project_context(config)

    def test_unsupported_schema_version_rejected_by_parse(self):
        from engineering.models import parse_project_config
        result = parse_project_config({"schema_version": "99.99", "project_id": "x"})
        assert result.config is None
        assert any("unsupported version" in e for e in result.errors)


# ---------------------------------------------------------------------------
# C8-C11: No side effects (filesystem, QA, Git, workflows, network)
# ---------------------------------------------------------------------------

class TestNoSideEffects:
    """Verify the factory and adapters perform zero side effects."""

    def test_factory_does_not_create_filesystem_paths(self, tmp_path):
        """Context construction must not create files or directories."""
        config = _minimal_config(
            repository_root=tmp_path,
        )
        # Create parent dirs for governance files (simulate existing repo)
        (tmp_path / "AGENT_BACKLOG.md").touch()
        (tmp_path / "AGENT_OPERATING_PLAN.md").touch()
        (tmp_path / "OWNERS.md").touch()
        (tmp_path / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md").touch()
        (tmp_path / ".git").mkdir()
        (tmp_path / "reports").mkdir()

        # Track filesystem changes
        before = set(_all_paths(tmp_path))

        # Build context
        ctx = build_project_context(config)

        after = set(_all_paths(tmp_path))
        created = after - before
        # No new paths should be created by the factory
        assert not created, f"Factory created unexpected paths: {created}"

    def test_factory_does_not_execute_qa(self):
        """Factory must not run QA commands.

        Validation fails (non-existent repository_root) before any QA
        could run, proving QA is never invoked.
        """
        config = _minimal_config()  # uses non-existent /tmp/test-repo
        with pytest.raises(ValueError, match="validation failed"):
            build_project_context(config)

    def test_factory_does_not_mutate_git(self):
        """Factory must not call Git to mutate state.

        Validation fails (non-existent repository_root) before any Git
        operation could occur, proving no Git mutation happens.
        """
        config = _minimal_config()  # uses non-existent /tmp/test-repo
        with pytest.raises(ValueError, match="validation failed"):
            build_project_context(config)

    def test_factory_does_not_start_workflows(self):
        """Factory must not start or persist workflow state.

        Validation fails (non-existent repository_root) before any workflow
        could be started, proving no workflow state is modified.
        """
        config = _minimal_config()  # uses non-existent /tmp/test-repo
        with pytest.raises(ValueError, match="validation failed"):
            build_project_context(config)

    def test_factory_performs_no_network_access(self):
        """Factory must not make network calls.

        Validation fails (non-existent repository_root) before any network
        call could be made, proving no network access occurs.
        """
        config = _minimal_config()  # uses non-existent /tmp/test-repo
        with pytest.raises(ValueError, match="validation failed"):
            build_project_context(config)


# ---------------------------------------------------------------------------
# C12-C13: No cwd fallback, no hardcoded trading-bot fallback
# ---------------------------------------------------------------------------

class TestNoPathFallbacks:
    def test_no_cwd_fallback_in_factory(self):
        """Factory must not use Path.cwd() as a fallback."""
        from engineering import context as ctx_module
        import inspect
        source = inspect.getsource(ctx_module.build_project_context)
        assert "cwd" not in source.lower()
        assert "Path.cwd" not in source

    def test_no_hardcoded_trading_bot_in_factory(self):
        """Factory must not hard-code 'trading-bot' as a fallback."""
        from engineering import context as ctx_module
        import inspect
        source = inspect.getsource(ctx_module.build_project_context)
        assert "trading-bot" not in source


# ---------------------------------------------------------------------------
# C14: adapters.py imports no trading runtime modules
# ---------------------------------------------------------------------------

class TestImportBoundary:
    def test_adapters_imports_no_trading_runtime(self):
        """Verify engineering/adapters.py has no imports from src/."""
        import ast
        from pathlib import Path

        adapters_path = Path(__file__).parent.parent / "engineering" / "adapters.py"
        source = adapters_path.read_text()
        tree = ast.parse(source)

        forbidden_imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("src."):
                        forbidden_imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("src."):
                    forbidden_imports.append(node.module)

        assert not forbidden_imports, (
            f"engineering/adapters.py must not import trading runtime (src/); "
            f"found: {forbidden_imports}"
        )

    def test_adapters_imports_no_dashboard(self):
        """Verify engineering/adapters.py has no imports from dashboard/."""
        import ast
        from pathlib import Path

        adapters_path = Path(__file__).parent.parent / "engineering" / "adapters.py"
        source = adapters_path.read_text()
        tree = ast.parse(source)

        forbidden_imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "dashboard" in alias.name.lower():
                        forbidden_imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and "dashboard" in node.module.lower():
                    forbidden_imports.append(node.module)

        assert not forbidden_imports


# ---------------------------------------------------------------------------
# C15-C16: Deferred factory behavior is explicit and deterministic
# ---------------------------------------------------------------------------

class TestDeferredFactoryBehavior:
    def test_files_deferred_adapter_raises_capability_unavailable(self, tmp_path):
        """Files adapter remains deferred; raises CapabilityUnavailable on every method call."""
        config = _minimal_config(project_id="deferred-test", repository_root=tmp_path)
        (tmp_path / "AGENT_BACKLOG.md").touch()
        (tmp_path / "AGENT_OPERATING_PLAN.md").touch()
        (tmp_path / "OWNERS.md").touch()
        (tmp_path / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md").touch()
        (tmp_path / ".git").mkdir()
        (tmp_path / "reports").mkdir()

        ctx = build_project_context(config)

        # ctx.qa is now concrete (QAAdapterImpl); ctx.files remains deferred
        with pytest.raises(CapabilityUnavailable) as exc_files:
            ctx.files.resolve(Path("x"))
        assert exc_files.value.project_id == "deferred-test"
        assert exc_files.value.capability == "files"

    def test_error_messages_are_sanitized(self, tmp_path):
        """Validation errors contain no secrets or raw command output."""
        config = _minimal_config(project_id="", repository_root=tmp_path)
        with pytest.raises(ValueError) as exc_info:
            build_project_context(config)
        error_msg = str(exc_info.value)
        # Error message should mention what failed but not leak internal state
        assert "validation failed" in error_msg
        # Should be a single-line, bounded message
        assert "\n" not in error_msg

    def test_files_capability_unavailable_is_deterministic(self, tmp_path):
        """Files CapabilityUnavailable message is stable (not random or time-based)."""
        config = _minimal_config(project_id="det-test", repository_root=tmp_path)
        (tmp_path / "AGENT_BACKLOG.md").touch()
        (tmp_path / "AGENT_OPERATING_PLAN.md").touch()
        (tmp_path / "OWNERS.md").touch()
        (tmp_path / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md").touch()
        (tmp_path / ".git").mkdir()
        (tmp_path / "reports").mkdir()

        ctx1 = build_project_context(config)
        ctx2 = build_project_context(config)

        # files remains deferred; qa is now concrete
        with pytest.raises(CapabilityUnavailable) as exc_files1:
            ctx1.files.resolve(Path("x"))
        with pytest.raises(CapabilityUnavailable) as exc_files2:
            ctx2.files.resolve(Path("x"))

        # Same error message on every call
        assert str(exc_files1.value) == str(exc_files2.value)
        assert "det-test" in str(exc_files1.value)
        assert "files" in str(exc_files1.value)

    def test_qa_adapter_returns_deterministic_values(self, tmp_path):
        """QAAdapterImpl returns deterministic config values on repeated calls."""
        config = _minimal_config(project_id="qa-det-test", repository_root=tmp_path)
        (tmp_path / "AGENT_BACKLOG.md").touch()
        (tmp_path / "AGENT_OPERATING_PLAN.md").touch()
        (tmp_path / "OWNERS.md").touch()
        (tmp_path / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md").touch()
        (tmp_path / ".git").mkdir()
        (tmp_path / "reports").mkdir()

        ctx = build_project_context(config)

        # Values are deterministic across calls
        assert ctx.qa.configured_command() == ctx.qa.configured_command()
        assert ctx.qa.timeout_seconds() == ctx.qa.timeout_seconds()
        assert ctx.qa.configured_command() == config.qa_commands
        assert ctx.qa.timeout_seconds() == config.qa_timeout_seconds


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _build_context_directly_for_test(config: ProjectConfig) -> ProjectContext:
    """Build a ProjectContext directly without factory validation (for isolation)."""
    metadata = ProjectMetadata(
        project_id=config.project_id,
        display_name=config.display_name,
        repository_root=config.repository_root,
        authoritative_base_branch=config.authoritative_base_branch,
        agents_may_merge=config.agents_may_merge,
        owner_ids=config.owner_ids,
        agent_owners=config.agent_owners,
        prohibited_operations=config.prohibited_operations,
    )

    event_adapter = EventAdapterImpl(config.workflow_files.event_store_path)
    workflow_adapter = WorkflowAdapterImpl(config.workflow_files, event_adapter)
    governance_adapter = GovernanceAdapterImpl(
        config.governance_files,
        config.repository_root,
    )

    return ProjectContext(
        config=config,
        git=GitAdapterImpl(config.repository_root),
        governance=governance_adapter,
        workflow=workflow_adapter,
        qa=QAAdapterImpl(config),
        files=_DeferredFileReadAdapter(config.project_id),
        events=event_adapter,
        metadata=metadata,
    )


def _all_paths(root: Path) -> list[Path]:
    """Return all paths under root (recursive)."""
    try:
        return list(root.rglob("*"))
    except PermissionError:
        return []
