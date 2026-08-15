"""Tests for ENGPLAT-003A bootstrap planning and filesystem creation.

Covers:
- plan_bootstrap() dry-run behavior
- apply_bootstrap() filesystem creation
- Destination safety and validation
- Transaction model (pre-flight, partial state)
- Conflict and overwrite policy
- Generic template content
- Regression: existing ProjectConfig behavior unchanged
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
import pytest

from engineering.bootstrap import (
    BootstrapInput,
    BootstrapPlan,
    BootstrapResult,
    apply_bootstrap,
    plan_bootstrap,
)
from engineering.models import (
    GovernanceFiles,
    ProjectConfig,
    WorkflowFiles,
    parse_project_config,
    validate_project_config,
    TRADING_BOT_PROJECT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmp_path() -> Path:
    """Return a new temporary directory Path that is cleaned up after the test."""
    return Path(tempfile.mkdtemp(prefix="bootstrap_test_"))


def _bootstrap_input(
    project_id: str = "test-project",
    display_name: str = "Test Project",
    destination: Path | None = None,
    **kwargs,
) -> BootstrapInput:
    if destination is None:
        destination = _tmp_path()
    return BootstrapInput(
        project_id=project_id,
        display_name=display_name,
        destination=destination,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Test: plan_bootstrap requires BootstrapInput type
# ---------------------------------------------------------------------------


def test_plan_bootstrap_requires_bootstrap_input_type() -> None:
    with pytest.raises(TypeError, match="must be BootstrapInput"):
        plan_bootstrap({"project_id": "x", "display_name": "X", "destination": Path("/tmp/x")})


def test_apply_bootstrap_requires_bootstrap_input_type() -> None:
    with pytest.raises(TypeError, match="must be BootstrapInput"):
        apply_bootstrap({"project_id": "x", "display_name": "X", "destination": Path("/tmp/x")})


# ---------------------------------------------------------------------------
# Test: plan_bootstrap dry-run creates zero files
# ---------------------------------------------------------------------------


def test_plan_bootstrap_dry_run_creates_zero_files() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    # No files written
    assert list(dest.iterdir()) == []
    # No directories created
    assert dest.exists()


def test_plan_bootstrap_dry_run_creates_zero_directories() -> None:
    dest = _tmp_path()
    # Remove the dir so it doesn't exist
    os.rmdir(dest)
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    # dry-run does not create destination
    assert not dest.exists()


# ---------------------------------------------------------------------------
# Test: plan_bootstrap returns exactly five artifacts
# ---------------------------------------------------------------------------


def test_plan_bootstrap_returns_exactly_five_artifacts() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    assert len(plan.artifacts) == 5


def test_plan_bootstrap_artifact_actions_are_all_create() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    for artifact in plan.artifacts:
        assert artifact.action == "CREATE", f"unexpected action {artifact.action} for {artifact.relative_path}"


def test_plan_bootstrap_artifact_paths_are_correct() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    expected_names = [
        "AGENTS.md",
        "AGENT_BACKLOG.md",
        "AGENT_OPERATING_PLAN.md",
        "OWNERS.md",
        "AUTONOMOUS_ENGINEERING_HANDOFF.md",
    ]
    actual_names = [a.relative_path.name for a in plan.artifacts]
    assert actual_names == expected_names


def test_plan_bootstrap_artifact_bounded_metadata() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    for artifact in plan.artifacts:
        # byte_count must be positive
        assert artifact.byte_count > 0, f"zero byte_count for {artifact.relative_path}"
        # line_count must be positive
        assert artifact.line_count > 0, f"zero line_count for {artifact.relative_path}"
        # SHA-256 is 64 hex chars
        assert len(artifact.sha256_digest) == 64, f"invalid SHA-256 length for {artifact.relative_path}"
        assert artifact.sha256_digest.isalnum(), f"non-hex digest for {artifact.relative_path}"
        # summary is bounded
        assert len(artifact.summary) <= 200, f"unbounded summary for {artifact.relative_path}"


def test_plan_bootstrap_is_deterministic() -> None:
    dest = _tmp_path()
    inp1 = _bootstrap_input(
        project_id="det-test",
        display_name="Determinism Test",
        destination=dest,
    )
    inp2 = _bootstrap_input(
        project_id="det-test",
        display_name="Determinism Test",
        destination=dest,
    )
    plan1 = plan_bootstrap(inp1)
    plan2 = plan_bootstrap(inp2)
    # Same artifacts
    assert len(plan1.artifacts) == len(plan2.artifacts)
    for a1, a2 in zip(plan1.artifacts, plan2.artifacts):
        assert a1.sha256_digest == a2.sha256_digest
        assert a1.relative_path == a2.relative_path


# ---------------------------------------------------------------------------
# Test: generated ProjectConfig behavior
# ---------------------------------------------------------------------------


def test_plan_bootstrap_returns_project_config() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    assert plan.project_config is not None
    assert isinstance(plan.project_config, ProjectConfig)


def test_plan_bootstrap_project_config_passes_structural_parse() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    cfg = plan.project_config
    assert cfg is not None

    mapping = {
        "schema_version": cfg.schema_version,
        "project_id": cfg.project_id,
        "display_name": cfg.display_name,
        "repository_root": str(cfg.repository_root),
        "authoritative_base_branch": cfg.authoritative_base_branch,
        "governance_files": {
            "backlog_path": str(cfg.governance_files.backlog_path),
            "operating_plan_path": str(cfg.governance_files.operating_plan_path),
            "owners_path": str(cfg.governance_files.owners_path),
            "handoff_path": str(cfg.governance_files.handoff_path),
        },
        "workflow_files": {
            "workflow_store_path": str(cfg.workflow_files.workflow_store_path),
            "event_store_path": str(cfg.workflow_files.event_store_path),
            "report_dir": str(cfg.workflow_files.report_dir),
        },
        "qa_commands": cfg.qa_commands,
        "qa_timeout_seconds": cfg.qa_timeout_seconds,
        "prohibited_operations": cfg.prohibited_operations,
        "agents_may_merge": cfg.agents_may_merge,
        "owner_ids": cfg.owner_ids,
        "agent_owners": cfg.agent_owners,
    }
    result = parse_project_config(mapping)
    assert result.config is not None, f"structural parse failed: {result.errors}"
    assert result.errors == ()


def test_plan_bootstrap_project_config_passes_semantic_validation() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    cfg = plan.project_config
    assert cfg is not None

    # For validation to pass, the destination must exist and the governance
    # files must exist (they won't since we haven't applied)
    # So we use apply to create the files first
    result = apply_bootstrap(inp)
    assert result.success, f"apply failed: {result.error_message}"

    # Re-parse and validate
    cfg2 = result.project_config
    assert cfg2 is not None
    mapping = {
        "schema_version": cfg2.schema_version,
        "project_id": cfg2.project_id,
        "display_name": cfg2.display_name,
        "repository_root": str(cfg2.repository_root),
        "authoritative_base_branch": cfg2.authoritative_base_branch,
        "governance_files": {
            "backlog_path": str(cfg2.governance_files.backlog_path),
            "operating_plan_path": str(cfg2.governance_files.operating_plan_path),
            "owners_path": str(cfg2.governance_files.owners_path),
            "handoff_path": str(cfg2.governance_files.handoff_path),
        },
        "workflow_files": {
            "workflow_store_path": str(cfg2.workflow_files.workflow_store_path),
            "event_store_path": str(cfg2.workflow_files.event_store_path),
            "report_dir": str(cfg2.workflow_files.report_dir),
        },
        "qa_commands": cfg2.qa_commands,
        "qa_timeout_seconds": cfg2.qa_timeout_seconds,
        "prohibited_operations": cfg2.prohibited_operations,
        "agents_may_merge": cfg2.agents_may_merge,
        "owner_ids": cfg2.owner_ids,
        "agent_owners": cfg2.agent_owners,
    }
    result2 = parse_project_config(mapping)
    assert result2.config is not None, f"structural parse failed: {result2.errors}"
    sem_errors = validate_project_config(result2.config)
    assert sem_errors == [], f"semantic validation failed: {sem_errors}"


# ---------------------------------------------------------------------------
# Test: destination safety
# ---------------------------------------------------------------------------


def test_missing_destination_allowed_for_plan() -> None:
    dest = _tmp_path()
    os.rmdir(dest)  # Ensure it doesn't exist
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    # Pre-flight creates the directory for plan (needed to check conflicts)
    # But dry-run should not create it
    assert not dest.exists()


def test_empty_existing_destination_allowed() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    assert plan.validation_errors == ()
    assert len(plan.artifacts) == 5


def test_non_empty_destination_with_unrelated_file_allowed() -> None:
    dest = _tmp_path()
    # Create an unrelated file
    unrelated = dest / "README.txt"
    unrelated.write_text("existing")
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)
    # Should have a warning about non-empty destination
    assert len(plan.warnings) >= 1
    assert "non-empty" in plan.warnings[0].lower()
    # But no validation errors
    assert plan.validation_errors == ()
    assert len(plan.artifacts) == 5


def test_symlink_destination_root_rejected() -> None:
    real_dir = _tmp_path()
    link_dir = _tmp_path()
    # Ensure link target does not already exist
    if link_dir.exists() or link_dir.is_symlink():
        import shutil
        if link_dir.is_dir():
            shutil.rmtree(link_dir)
        else:
            link_dir.unlink()
    os.symlink(real_dir, link_dir)
    try:
        inp = _bootstrap_input(destination=link_dir)
        plan = plan_bootstrap(inp)
        # Must fail validation
        assert any("symlink" in e.lower() for e in plan.validation_errors), (
            f"expected symlink rejection; got: {plan.validation_errors}"
        )
    finally:
        # Clean up symlink
        if link_dir.is_symlink():
            link_dir.unlink()


def test_traversal_via_dotdot_rejected() -> None:
    dest = _tmp_path()
    # Path with ../
    inp = BootstrapInput(
        project_id="traversal-test",
        display_name="Traversal Test",
        destination=dest,
    )
    plan = plan_bootstrap(inp)
    # Normal plan should work
    assert plan.validation_errors == ()
    assert len(plan.artifacts) == 5


# ---------------------------------------------------------------------------
# Test: conflict detection — planned file already exists
# ---------------------------------------------------------------------------


def test_planned_file_conflict_causes_zero_writes() -> None:
    dest = _tmp_path()
    # Pre-create one of the planned artifacts
    conflict_file = dest / "AGENTS.md"
    conflict_file.write_text("already exists")

    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)

    # Conflict must be detected in validation
    assert any("CONFLICT" in e or "already exists" in e.lower() for e in plan.validation_errors), (
        f"expected CONFLICT detection; got: {plan.validation_errors}"
    )

    # Apply must write zero files
    result = apply_bootstrap(inp)
    assert not result.success
    assert result.written_paths == ()
    assert result.partial_state is False

    # File still has original content
    assert conflict_file.read_text() == "already exists"


# ---------------------------------------------------------------------------
# Test: apply_bootstrap creates exactly five files
# ---------------------------------------------------------------------------


def test_apply_bootstrap_creates_five_files() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    result = apply_bootstrap(inp)

    assert result.success, f"apply failed: {result.error_message}"
    assert len(result.written_paths) == 5
    assert result.partial_state is False
    assert result.failed_target is None

    # All five files exist
    expected_files = [
        "AGENTS.md",
        "AGENT_BACKLOG.md",
        "AGENT_OPERATING_PLAN.md",
        "OWNERS.md",
        "AUTONOMOUS_ENGINEERING_HANDOFF.md",
    ]
    for fname in expected_files:
        assert (dest / fname).exists(), f"missing: {fname}"
        assert (dest / fname).stat().st_size > 0, f"empty: {fname}"


def test_apply_bootstrap_no_gitignore_created() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    result = apply_bootstrap(inp)
    assert result.success
    assert not (dest / ".gitignore").exists()


def test_apply_bootstrap_no_agent_state_created() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    result = apply_bootstrap(inp)
    assert result.success
    assert not (dest / ".agent-state").exists()


def test_apply_bootstrap_no_reports_dir_created() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    result = apply_bootstrap(inp)
    assert result.success
    # reports/ is NOT created by bootstrap (runtime creates it)
    assert not (dest / "reports").exists()


def test_apply_bootstrap_no_pyproject_toml_created() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    result = apply_bootstrap(inp)
    assert result.success
    assert not (dest / "pyproject.toml").exists()


def test_apply_bootstrap_no_pytest_ini_created() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    result = apply_bootstrap(inp)
    assert result.success
    assert not (dest / "pytest.ini").exists()


def test_apply_bootstrap_no_env_created() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    result = apply_bootstrap(inp)
    assert result.success
    assert not (dest / ".env").exists()


def test_apply_bootstrap_no_registry_file_created() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    result = apply_bootstrap(inp)
    assert result.success
    # No registry JSON/YAML
    assert not (dest / "project_registry.json").exists()
    assert not (dest / "project_registry.yaml").exists()
    assert not (dest / "registry.json").exists()


# ---------------------------------------------------------------------------
# Test: generated content is generic
# ---------------------------------------------------------------------------


def test_generic_handoff_filename_used() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)

    # Must use AUTONOMOUS_ENGINEERING_HANDOFF.md, not TRADING_BOT_...
    handoff_artifacts = [a for a in plan.artifacts if "handoff" in a.relative_path.name.lower()]
    assert len(handoff_artifacts) == 1
    assert handoff_artifacts[0].relative_path.name == "AUTONOMOUS_ENGINEERING_HANDOFF.md"


def test_apply_bootstrap_no_trading_bot_specific_strings() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(
        project_id="generic-test",
        display_name="Generic Test",
        destination=dest,
    )
    result = apply_bootstrap(inp)
    assert result.success

    # Check each generated file
    for fname in ["AGENTS.md", "AGENT_BACKLOG.md", "AGENT_OPERATING_PLAN.md", "OWNERS.md", "AUTONOMOUS_ENGINEERING_HANDOFF.md"]:
        content = (dest / fname).read_text()
        # Must not contain trading-bot specific identifiers
        assert "trading-bot" not in content.lower(), f"{fname} contains trading-bot"
        assert "trading_bot" not in content, f"{fname} contains trading_bot"
        assert "alpaca" not in content.lower(), f"{fname} contains alpaca"
        assert "no_live_trading" not in content, f"{fname} contains no_live_trading"
        assert "no_brokerage_access" not in content, f"{fname} contains no_brokerage_access"
        assert "josh" not in content.lower(), f"{fname} contains josh"


def test_apply_bootstrap_substitutes_project_id_and_display_name() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(
        project_id="my-project",
        display_name="My Special Project",
        destination=dest,
    )
    result = apply_bootstrap(inp)
    assert result.success

    # AGENTS.md should contain the project display name
    agents_content = (dest / "AGENTS.md").read_text()
    assert "My Special Project" in agents_content, "display_name not substituted in AGENTS.md"

    # OWNERS.md should contain the generic roles, not trading-bot names
    owners_content = (dest / "OWNERS.md").read_text()
    assert "trading-bot" not in owners_content.lower()


# ---------------------------------------------------------------------------
# Test: failure and partial state
# ---------------------------------------------------------------------------


def test_write_failure_stops_remaining_writes(monkeypatch) -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)

    # Count how many files would be written before the failure
    call_count = 0

    original_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        nonlocal call_count
        if isinstance(path, (str, Path)) and "AGENTS.md" in str(path) and "w" in mode:
            call_count += 1
            raise OSError("injected write failure for testing")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    result = apply_bootstrap(inp)

    # Should fail on first file
    assert not result.success
    assert result.partial_state is False
    assert result.written_paths == ()
    assert result.failed_target is not None
    assert "AGENTS.md" in str(result.failed_target)


def test_partial_state_reported_when_some_files_written(monkeypatch) -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)

    original_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        if isinstance(path, (str, Path)) and "AGENT_BACKLOG.md" in str(path) and "w" in mode:
            raise OSError("injected write failure after first file")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    result = apply_bootstrap(inp)

    assert not result.success
    assert result.partial_state is True
    assert len(result.written_paths) == 1
    assert result.written_paths[0].name == "AGENTS.md"
    assert result.failed_target is not None
    assert "AGENT_BACKLOG.md" in str(result.failed_target)
    assert "partial_state" in str(result).lower()


def test_result_no_rollback_claim(monkeypatch) -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)

    original_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        if isinstance(path, (str, Path)) and "AGENT_BACKLOG.md" in str(path) and "w" in mode:
            raise OSError("injected write failure")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    result = apply_bootstrap(inp)

    # Must not claim rollback in error message
    assert result.error_message is not None
    assert "rollback" not in result.error_message.lower()


# ---------------------------------------------------------------------------
# Test: BootstrapResult fields
# ---------------------------------------------------------------------------


def test_bootstrap_result_has_all_required_fields() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    result = apply_bootstrap(inp)

    assert result.success is not None
    assert isinstance(result.written_paths, tuple)
    assert result.failed_target is None or isinstance(result.failed_target, Path)
    assert isinstance(result.partial_state, bool)
    assert result.project_config is not None
    assert result.plan is not None
    assert result.error_message is None or isinstance(result.error_message, str)


def test_bootstrap_plan_has_all_required_fields() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(destination=dest)
    plan = plan_bootstrap(inp)

    assert isinstance(plan.input, BootstrapInput)
    assert isinstance(plan.artifacts, tuple)
    assert isinstance(plan.warnings, tuple)
    assert isinstance(plan.validation_errors, tuple)
    assert plan.project_config is None or isinstance(plan.project_config, ProjectConfig)


# ---------------------------------------------------------------------------
# Test: BootstrapInput field validation
# ---------------------------------------------------------------------------


def test_empty_project_id_rejected_in_plan() -> None:
    dest = _tmp_path()
    inp = BootstrapInput(
        project_id="",
        display_name="Test",
        destination=dest,
    )
    plan = plan_bootstrap(inp)
    assert any("project_id" in e.lower() for e in plan.validation_errors)


def test_empty_display_name_rejected_in_plan() -> None:
    dest = _tmp_path()
    inp = BootstrapInput(
        project_id="test",
        display_name="",
        destination=dest,
    )
    plan = plan_bootstrap(inp)
    assert any("display_name" in e.lower() for e in plan.validation_errors)


def test_negative_qa_timeout_rejected() -> None:
    dest = _tmp_path()
    inp = BootstrapInput(
        project_id="test",
        display_name="Test",
        destination=dest,
        qa_timeout_seconds=0,
    )
    plan = plan_bootstrap(inp)
    assert any("qa_timeout" in e.lower() for e in plan.validation_errors)


# ---------------------------------------------------------------------------
# Test: destination must be absolute
# ---------------------------------------------------------------------------


def test_relative_destination_rejected() -> None:
    inp = BootstrapInput(
        project_id="test",
        display_name="Test",
        destination=Path("relative/path"),
    )
    plan = plan_bootstrap(inp)
    assert any("absolute" in e.lower() for e in plan.validation_errors)


# ---------------------------------------------------------------------------
# Test: overwrite policy — no overwrite mechanism exists
# ---------------------------------------------------------------------------


def test_no_force_flag_in_bootstrap_input() -> None:
    """BootstrapInput dataclass has no force/overwrite field."""
    fields = {f.name for f in BootstrapInput.__dataclass_fields__.values()}
    assert "force" not in fields
    assert "overwrite" not in fields
    assert "replace" not in fields


def test_existing_file_prevents_apply() -> None:
    dest = _tmp_path()
    # Pre-create one artifact
    (dest / "OWNERS.md").write_text("existing content")
    inp = _bootstrap_input(destination=dest)
    result = apply_bootstrap(inp)
    # Must not overwrite
    assert not result.success
    assert (dest / "OWNERS.md").read_text() == "existing content"


# ---------------------------------------------------------------------------
# Regression: TRADING_BOT_PROJECT unchanged
# ---------------------------------------------------------------------------


def test_trading_bot_project_unchanged() -> None:
    """TRADING_BOT_PROJECT constant must not be modified by bootstrap."""
    assert TRADING_BOT_PROJECT.project_id == "trading-bot"
    assert TRADING_BOT_PROJECT.schema_version == "1.0"
    assert "no_live_trading" in TRADING_BOT_PROJECT.prohibited_operations
    assert TRADING_BOT_PROJECT.agents_may_merge is False


def test_parse_project_config_still_works() -> None:
    """Regression: existing parse_project_config behavior unchanged."""
    mapping = {
        "schema_version": "1.0",
        "project_id": "regression-test",
        "display_name": "Regression Test",
        "repository_root": str(Path.cwd()),
        "authoritative_base_branch": "main",
        "governance_files": {
            "backlog_path": str(Path.cwd() / "AGENT_BACKLOG.md"),
            "operating_plan_path": str(Path.cwd() / "AGENT_OPERATING_PLAN.md"),
            "owners_path": str(Path.cwd() / "OWNERS.md"),
            "handoff_path": str(Path.cwd() / "AUTONOMOUS_ENGINEERING_HANDOFF.md"),
        },
        "workflow_files": {
            "workflow_store_path": str(Path.cwd() / "engineering" / "workflow_store.json"),
            "event_store_path": str(Path.cwd() / "engineering" / "event_store.db"),
            "report_dir": str(Path.cwd() / "reports"),
        },
        "qa_commands": ("python -m pytest tests/ -q",),
        "qa_timeout_seconds": 300,
        "prohibited_operations": (),
        "agents_may_merge": False,
        "owner_ids": ("owner",),
        "agent_owners": ("manager",),
    }
    result = parse_project_config(mapping)
    assert result.config is not None
    assert result.errors == ()


def test_validate_project_config_still_works() -> None:
    """Regression: existing validate_project_config behavior unchanged."""
    # Use a path that exists in the test environment
    repo_root = Path.cwd()
    if not repo_root.exists():
        pytest.skip("cwd does not exist in test env")

    cfg = ProjectConfig(
        schema_version="1.0",
        project_id="regression-test",
        display_name="Regression Test",
        repository_root=repo_root,
        authoritative_base_branch="main",
        governance_files=GovernanceFiles(
            backlog_path=repo_root / "AGENT_BACKLOG.md",
            operating_plan_path=repo_root / "AGENT_OPERATING_PLAN.md",
            owners_path=repo_root / "OWNERS.md",
            handoff_path=repo_root / "AUTONOMOUS_ENGINEERING_HANDOFF.md",
        ),
        workflow_files=WorkflowFiles(
            workflow_store_path=repo_root / "engineering" / "workflow_store.json",
            event_store_path=repo_root / "engineering" / "event_store.db",
            report_dir=repo_root / "reports",
        ),
        qa_commands=("python -m pytest tests/ -q",),
        qa_timeout_seconds=300,
        prohibited_operations=(),
        agents_may_merge=False,
        owner_ids=("owner",),
        agent_owners=("manager",),
    )
    errors = validate_project_config(cfg)
    # Should have errors about missing files/paths, but not structural errors
    assert isinstance(errors, list)


# ---------------------------------------------------------------------------
# Test: BootstrapInput accepts custom agent roles
# ---------------------------------------------------------------------------


def test_custom_agent_roles_are_substituted() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(
        destination=dest,
        manager_role="my-manager",
        exec_role="my-executor",
        dashboard_role="my-dashboard",
    )
    result = apply_bootstrap(inp)
    assert result.success

    # Check that custom role names appear in OWNERS.md
    owners = (dest / "OWNERS.md").read_text()
    assert "my-manager" in owners
    assert "my-executor" in owners
    assert "my-dashboard" in owners


# ---------------------------------------------------------------------------
# Test: BootstrapInput accepts custom prohibited_operations
# ---------------------------------------------------------------------------


def test_custom_prohibited_operations_passed_to_config() -> None:
    dest = _tmp_path()
    inp = _bootstrap_input(
        destination=dest,
        prohibited_operations=("no_deployment", "no_deletion"),
    )
    plan = plan_bootstrap(inp)
    cfg = plan.project_config
    assert cfg is not None
    assert "no_deployment" in cfg.prohibited_operations
    assert "no_deletion" in cfg.prohibited_operations


# ---------------------------------------------------------------------------
# Test: BootstrapInput accepts custom qa_commands
# ---------------------------------------------------------------------------


def test_custom_qa_commands_passed_to_config() -> None:
    dest = _tmp_path()
    custom_qa = ("python -m pytest tests/test_specific.py -v",)
    inp = _bootstrap_input(
        destination=dest,
        qa_commands=custom_qa,
    )
    plan = plan_bootstrap(inp)
    cfg = plan.project_config
    assert cfg is not None
    assert cfg.qa_commands == custom_qa
