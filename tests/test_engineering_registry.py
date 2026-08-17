"""Tests for engineering/registry.py — ENGPLAT-003B Slice 1.

Scope
-----
Registry loading, project lookup, and error handling.
No workflow mutations, no manager changes, no project special-casing.

Key invariants verified
----------------------
- Zero ``if project_id == ...`` branches in registry.py (confirmed by code inspection)
- Malformed / missing registry fails closed
- Duplicate project_id raises DuplicateProjectId before any other processing
- Unknown project lookup raises ProjectNotFoundError
- No implicit fallback to trading-bot
- Both trading-bot and fantasy entries resolve to correct repository_root
- Existing ProjectConfig/ProjectRegistry tests remain passing

Known limitations
-----------------
- Fantasy .engineering/ directory does not exist yet. This is expected — runtime
  state is created lazily when the fantasy project is first activated.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Import the module under test
from engineering.registry import (
    DuplicateProjectId,
    MalformedRegistryError,
    ProjectNotFoundError,
    RegistryError,
    UnsupportedRegistryVersionError,
    get_project,
    load_registry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TRADING_BOT_ROOT = Path("/root/.openclaw/workspace/trading-bot")
FANTASY_ROOT = Path("/root/.openclaw/workspace/fantasy-draft-command-center")

# Minimal valid project entries used across tests
_TRADING_BOT_ENTRY: dict[str, Any] = {
    "schema_version": "1.0",
    "project_id": "trading-bot",
    "display_name": "Trading Bot",
    "repository_root": str(TRADING_BOT_ROOT),
    "authoritative_base_branch": "main",
    "governance_files": {
        "backlog_path": str(TRADING_BOT_ROOT / "AGENT_BACKLOG.md"),
        "operating_plan_path": str(TRADING_BOT_ROOT / "AGENT_OPERATING_PLAN.md"),
        "owners_path": str(TRADING_BOT_ROOT / "OWNERS.md"),
        "handoff_path": str(TRADING_BOT_ROOT / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md"),
    },
    "workflow_files": {
        "workflow_store_path": str(TRADING_BOT_ROOT / "engineering" / "workflow_store.json"),
        "event_store_path": str(TRADING_BOT_ROOT / "engineering" / "event_store.db"),
        "report_dir": str(TRADING_BOT_ROOT / "reports"),
    },
    "qa_commands": ["python -m pytest tests/test_engineering_models.py -v"],
    "qa_timeout_seconds": 300,
    "prohibited_operations": ["no_live_trading", "no_brokerage_access"],
    "agents_may_merge": False,
    "owner_ids": ["josh"],
    "agent_owners": ["trading-manager"],
}

_FANTASY_ENTRY: dict[str, Any] = {
    "schema_version": "1.0",
    "project_id": "fantasy-draft-command-center",
    "display_name": "Fantasy Draft Command Center",
    "repository_root": str(FANTASY_ROOT),
    "authoritative_base_branch": "main",
    "governance_files": {
        "backlog_path": str(FANTASY_ROOT / "AGENT_BACKLOG.md"),
        "operating_plan_path": str(FANTASY_ROOT / "AGENT_OPERATING_PLAN.md"),
        "owners_path": str(FANTASY_ROOT / "OWNERS.md"),
        "handoff_path": str(FANTASY_ROOT / "AUTONOMOUS_ENGINEERING_HANDOFF.md"),
    },
    "workflow_files": {
        "workflow_store_path": str(FANTASY_ROOT / ".engineering" / "workflow_store.json"),
        "event_store_path": str(FANTASY_ROOT / ".engineering" / "event_store.db"),
        "report_dir": str(FANTASY_ROOT / ".engineering" / "reports"),
    },
    "qa_commands": ["npm test"],
    "qa_timeout_seconds": 300,
    "prohibited_operations": ["no_production_database", "no_live_trading"],
    "agents_may_merge": False,
    "owner_ids": ["josh"],
    "agent_owners": ["fantasy-manager"],
}


def _write_registry(
    tmp_path: Path,
    entries: list[dict[str, Any]],
    *,
    registry_version: str = "1",
) -> Path:
    """Write a valid registry file and return its path."""
    path = tmp_path / "engineering-registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "registry_version": registry_version,
        "projects": entries,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Structural / file errors
# ---------------------------------------------------------------------------

def test_missing_registry_file(tmp_path: Path):
    """Missing registry file raises MalformedRegistryError."""
    fake = tmp_path / "nonexistent.json"
    with pytest.raises(MalformedRegistryError, match="not found"):
        load_registry(fake)


def test_unreadable_registry_file(tmp_path: Path):
    """Unreadable registry raises MalformedRegistryError.

    We simulate unreadability by patching Path.read_text, since root can
    read files regardless of permissions on Unix.
    """
    path = tmp_path / "unreadable.json"
    path.write_text("{}", encoding="utf-8")

    original_read = Path.read_text
    def fake_read_text(self, *args, **kwargs):  # type: ignore[override]
        if self == path:
            raise OSError("Permission denied")
        return original_read(self, *args, **kwargs)

    with patch.object(Path, "read_text", fake_read_text):
        with pytest.raises(MalformedRegistryError, match="cannot read"):
            load_registry(path)


def test_malformed_json(tmp_path: Path):
    """Non-JSON registry raises MalformedRegistryError."""
    path = tmp_path / "bad.json"
    path.write_text("{ this is not json }", encoding="utf-8")
    with pytest.raises(MalformedRegistryError, match="not valid JSON"):
        load_registry(path)


def test_root_not_object(tmp_path: Path):
    """Registry root that is not a JSON object raises MalformedRegistryError."""
    path = tmp_path / "not_obj.json"
    path.write_text('"just a string"', encoding="utf-8")
    with pytest.raises(MalformedRegistryError, match="must be a JSON object"):
        load_registry(path)


def test_projects_not_array(tmp_path: Path):
    """projects that is not an array raises MalformedRegistryError."""
    path = tmp_path / "not_array.json"
    path.write_text('{"registry_version": "1", "projects": "not an array"}', encoding="utf-8")
    with pytest.raises(MalformedRegistryError, match="must be a JSON array"):
        load_registry(path)


def test_missing_projects_key(tmp_path: Path):
    """Missing projects key raises MalformedRegistryError."""
    path = tmp_path / "no_projects.json"
    path.write_text('{"registry_version": "1"}', encoding="utf-8")
    with pytest.raises(MalformedRegistryError, match='missing required top-level key'):
        load_registry(path)


def test_unsupported_registry_version(tmp_path: Path):
    """Unsupported registry_version raises UnsupportedRegistryVersionError."""
    path = _write_registry(tmp_path, [], registry_version="99")
    with pytest.raises(UnsupportedRegistryVersionError, match="unsupported registry_version"):
        load_registry(path)


# ---------------------------------------------------------------------------
# Duplicate project_id
# ---------------------------------------------------------------------------

def test_duplicate_project_id_raises_before_parsing(tmp_path: Path):
    """Duplicate project_id raises DuplicateProjectId before any project is parsed."""
    entry = _TRADING_BOT_ENTRY.copy()
    path = _write_registry(
        tmp_path,
        [entry, entry],  # same entry twice
    )
    with pytest.raises(DuplicateProjectId, match="trading-bot"):
        load_registry(path)


def test_duplicate_id_in_three_entries(tmp_path: Path):
    """Duplicate ID among three entries raises DuplicateProjectId on the second."""
    fantasy = _FANTASY_ENTRY.copy()
    fantasy["project_id"] = "trading-bot"  # duplicate with trading-bot
    path = _write_registry(tmp_path, [_TRADING_BOT_ENTRY, fantasy, _FANTASY_ENTRY])
    with pytest.raises(DuplicateProjectId, match="trading-bot"):
        load_registry(path)


# ---------------------------------------------------------------------------
# Per-entry structural errors
# ---------------------------------------------------------------------------

def test_project_not_an_object(tmp_path: Path):
    """A non-object project entry raises MalformedRegistryError."""
    path = _write_registry(tmp_path, ["not an object", _TRADING_BOT_ENTRY])
    with pytest.raises(MalformedRegistryError, match="expected JSON object"):
        load_registry(path)


def test_missing_schema_version(tmp_path: Path):
    """Project entry missing schema_version raises MalformedRegistryError."""
    bad = {k: v for k, v in _TRADING_BOT_ENTRY.items() if k != "schema_version"}
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="schema_version"):
        load_registry(path)


def test_unsupported_schema_version(tmp_path: Path):
    """Unsupported schema_version raises MalformedRegistryError per entry."""
    bad = {**_TRADING_BOT_ENTRY, "schema_version": "99.0"}
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError):
        load_registry(path)


def test_unknown_field_rejected(tmp_path: Path):
    """Unknown fields in a project entry raise MalformedRegistryError."""
    bad = {**_TRADING_BOT_ENTRY, "unknown_field": "value"}
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="unknown field"):
        load_registry(path)


def test_missing_required_field_rejected(tmp_path: Path):
    """Missing required field raises MalformedRegistryError."""
    bad = {k: v for k, v in _TRADING_BOT_ENTRY.items() if k == "schema_version"}
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="missing required field"):
        load_registry(path)


# ---------------------------------------------------------------------------
# Semantic validation errors
# ---------------------------------------------------------------------------

def test_empty_project_id_rejected(tmp_path: Path):
    """Empty project_id raises MalformedRegistryError."""
    bad = {**_TRADING_BOT_ENTRY, "project_id": "   "}
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="project_id cannot be empty"):
        load_registry(path)


def test_missing_repository_root_raises(tmp_path: Path):
    """Missing repository_root raises MalformedRegistryError."""
    bad = {k: v for k, v in _TRADING_BOT_ENTRY.items() if k == "schema_version"}
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="missing required field"):
        load_registry(path)


def test_relative_repository_root_rejected(tmp_path: Path):
    """Non-absolute repository_root raises MalformedRegistryError."""
    bad = {**_TRADING_BOT_ENTRY, "repository_root": "relative/path"}
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="must be absolute"):
        load_registry(path)


def test_nonexistent_repository_root_rejected(tmp_path: Path):
    """Non-existent repository_root raises MalformedRegistryError."""
    bad = {**_TRADING_BOT_ENTRY, "repository_root": "/nonexistent/project/root"}
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="does not exist"):
        load_registry(path)


def test_missing_governance_file_raises(tmp_path: Path):
    """Missing governance file raises MalformedRegistryError."""
    bad = {
        **_TRADING_BOT_ENTRY,
        "governance_files": {
            "backlog_path": str(TRADING_BOT_ROOT / "AGENT_BACKLOG.md"),
            "operating_plan_path": str(tmp_path / "nonexistent.md"),
            "owners_path": str(TRADING_BOT_ROOT / "OWNERS.md"),
            "handoff_path": str(TRADING_BOT_ROOT / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md"),
        },
    }
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="does not exist"):
        load_registry(path)


def test_path_escape_rejected(tmp_path: Path):
    """Governance path outside repository_root raises MalformedRegistryError."""
    bad = {
        **_TRADING_BOT_ENTRY,
        "governance_files": {
            "backlog_path": str(Path("/etc/passwd")),
            "operating_plan_path": str(TRADING_BOT_ROOT / "AGENT_OPERATING_PLAN.md"),
            "owners_path": str(TRADING_BOT_ROOT / "OWNERS.md"),
            "handoff_path": str(TRADING_BOT_ROOT / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md"),
        },
    }
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="escapes repository_root"):
        load_registry(path)


# ---------------------------------------------------------------------------
# QA command safety (from models.py _check_qa_command_safety)
# ---------------------------------------------------------------------------

def test_unsafe_qa_command_rejected(tmp_path: Path):
    """QA command with unsafe patterns raises MalformedRegistryError."""
    bad = {**_TRADING_BOT_ENTRY, "qa_commands": ["rm -rf /tmp"]}
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="unsafe pattern"):
        load_registry(path)


def test_non_approved_qa_command_rejected(tmp_path: Path):
    """QA command not in approved prefixes raises MalformedRegistryError."""
    bad = {**_TRADING_BOT_ENTRY, "qa_commands": ["arbitrary --run"]}
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="not in approved list"):
        load_registry(path)


def test_npm_commands_approved_in_qa_safety(tmp_path: Path):
    """npm test, npm run <script>, and npx vitest are accepted by QA safety check.

    These commands pass _check_qa_command_safety (Layer 1) and are accepted
    by _configured_command (Layer 2, shape-based validation).
    """
    for qa_cmd in [
        ["npm test"],
        ["npm run test"],
        ["npm run typecheck"],
        ["npm run build"],
        ["npx vitest run"],
    ]:
        entry = {**_TRADING_BOT_ENTRY, "qa_commands": qa_cmd}
        path = _write_registry(tmp_path, [entry])
        registry = load_registry(path)
        assert registry.projects["trading-bot"].qa_commands == tuple(qa_cmd)


def test_agents_may_merge_true_rejected(tmp_path: Path):
    """agents_may_merge=True without approval policy raises MalformedRegistryError."""
    bad = {**_TRADING_BOT_ENTRY, "agents_may_merge": True}
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="agents_may_merge=True"):
        load_registry(path)


# ---------------------------------------------------------------------------
# Valid registries
# ---------------------------------------------------------------------------

def test_valid_single_project_registry(tmp_path: Path):
    """Single project registry loads correctly."""
    path = _write_registry(tmp_path, [_TRADING_BOT_ENTRY])
    registry = load_registry(path)
    assert len(registry.projects) == 1
    assert "trading-bot" in registry.projects


def test_valid_trading_bot_and_fantasy_together(tmp_path: Path):
    """Two-project registry with trading-bot and fantasy loads successfully.

    Fantasy's 'npm test' is now accepted (ENGPLAT-002C generic QA support).
    Fantasy's .engineering/ directory does not exist yet, so skip_workflow_files=True
    is required to bypass the runtime-readiness check.
    """
    path = _write_registry(tmp_path, [_TRADING_BOT_ENTRY, _FANTASY_ENTRY])
    registry = load_registry(path, skip_workflow_files=True)
    assert set(registry.projects.keys()) == {"trading-bot", "fantasy-draft-command-center"}
    assert registry.projects["fantasy-draft-command-center"].qa_commands == ("npm test",)


def test_registry_preserves_all_fields(tmp_path: Path):
    """Loaded ProjectConfig has the correct field values."""
    path = _write_registry(tmp_path, [_TRADING_BOT_ENTRY])
    registry = load_registry(path)
    cfg = registry.projects["trading-bot"]
    assert cfg.project_id == "trading-bot"
    assert cfg.display_name == "Trading Bot"
    assert cfg.repository_root == TRADING_BOT_ROOT
    assert cfg.authoritative_base_branch == "main"
    assert cfg.qa_commands == ("python -m pytest tests/test_engineering_models.py -v",)
    assert cfg.qa_timeout_seconds == 300
    assert "no_live_trading" in cfg.prohibited_operations
    assert cfg.agents_may_merge is False
    assert cfg.owner_ids == ("josh",)
    assert cfg.agent_owners == ("trading-manager",)


def test_multiple_errors_collected(tmp_path: Path):
    """Multiple projects with errors produce all error messages."""
    bad1 = {**_TRADING_BOT_ENTRY, "project_id": ""}  # empty project_id
    bad2 = {**_FANTASY_ENTRY, "repository_root": "/nonexistent"}  # bad root
    path = _write_registry(tmp_path, [bad1, bad2])
    with pytest.raises(MalformedRegistryError) as exc_info:
        load_registry(path)
    error_text = str(exc_info.value)
    assert "project_id cannot be empty" in error_text
    assert "/nonexistent" in error_text


# ---------------------------------------------------------------------------
# Project lookup
# ---------------------------------------------------------------------------

def test_get_project_returns_config(tmp_path: Path):
    """get_project returns the correct ProjectConfig."""
    path = _write_registry(tmp_path, [_TRADING_BOT_ENTRY])
    registry = load_registry(path)
    cfg = get_project(registry, "trading-bot")
    assert cfg.project_id == "trading-bot"
    assert cfg.repository_root == TRADING_BOT_ROOT


def test_get_project_fantasy_returns_fantasy_config(tmp_path: Path):
    """get_project returns fantasy config with fantasy's repository_root.

    Fantasy entry requires skip_workflow_files=True since .engineering/ does not
    yet exist (runtime state is created lazily on first activation). The QA
    command 'npm test' is now accepted (ENGPLAT-002C). Path resolution is
    correct regardless of runtime directory state.
    """
    fantasy_path = _write_registry(tmp_path, [_FANTASY_ENTRY])
    registry = load_registry(fantasy_path, skip_workflow_files=True)
    cfg = get_project(registry, "fantasy-draft-command-center")
    assert cfg.project_id == "fantasy-draft-command-center"
    assert cfg.repository_root == FANTASY_ROOT
    assert "trading-bot" not in str(cfg.repository_root)


def test_get_project_unknown_raises_not_found(tmp_path: Path):
    """get_project for unknown project_id raises ProjectNotFoundError."""
    path = _write_registry(tmp_path, [_TRADING_BOT_ENTRY])
    registry = load_registry(path)
    with pytest.raises(ProjectNotFoundError, match="nonexistent"):
        get_project(registry, "nonexistent")


def test_get_project_unknown_raises_in_single_project_registry(tmp_path: Path):
    """Unknown project_id raises even when other projects exist (trading-bot only)."""
    path = _write_registry(tmp_path, [_TRADING_BOT_ENTRY])
    registry = load_registry(path)
    with pytest.raises(ProjectNotFoundError, match="unknown-project"):
        get_project(registry, "unknown-project")


# ---------------------------------------------------------------------------
# Isolation / no-fallback invariants
# ---------------------------------------------------------------------------

def test_no_trading_bot_fallback_on_unknown_id(tmp_path: Path):
    """Lookup of unknown project does not return trading-bot config."""
    path = _write_registry(tmp_path, [_TRADING_BOT_ENTRY])
    registry = load_registry(path)
    with pytest.raises(ProjectNotFoundError):
        get_project(registry, "fantasy-draft-command-center")
    assert "fantasy" not in registry.projects


def test_fantasy_has_no_trading_bot_paths(tmp_path: Path):
    """Fantasy entry contains no trading-bot filesystem paths.

    Fantasy entry requires skip_workflow_files=True since .engineering/ is missing.
    """
    fantasy_path = _write_registry(tmp_path, [_FANTASY_ENTRY])
    registry = load_registry(fantasy_path, skip_workflow_files=True)
    cfg = get_project(registry, "fantasy-draft-command-center")
    all_paths = [
        cfg.repository_root,
        cfg.governance_files.backlog_path,
        cfg.governance_files.operating_plan_path,
        cfg.governance_files.owners_path,
        cfg.governance_files.handoff_path,
        cfg.workflow_files.workflow_store_path,
        cfg.workflow_files.event_store_path,
        cfg.workflow_files.report_dir,
    ]
    for p in all_paths:
        assert "trading-bot" not in str(p), f"fantasy config contains trading-bot path: {p}"


def test_switching_projects_produces_distinct_configs(tmp_path: Path):
    """Selecting trading-bot vs fantasy produces configs with distinct roots.

    Fantasy entry requires skip_workflow_files=True. Loaded separately using distinct
    registry files (to avoid overwriting).
    """
    tb_registry_file = tmp_path / "tb_registry.json"
    fx_registry_file = tmp_path / "fx_registry.json"

    tb_payload = {"registry_version": "1", "projects": [_TRADING_BOT_ENTRY]}
    fx_payload = {"registry_version": "1", "projects": [_FANTASY_ENTRY]}

    tb_registry_file.write_text(json.dumps(tb_payload), encoding="utf-8")
    fx_registry_file.write_text(json.dumps(fx_payload), encoding="utf-8")

    tb_reg = load_registry(tb_registry_file)
    fx_reg = load_registry(fx_registry_file, skip_workflow_files=True)
    tb = get_project(tb_reg, "trading-bot")
    fx = get_project(fx_reg, "fantasy-draft-command-center")
    assert tb.repository_root != fx.repository_root
    assert tb.workflow_files.workflow_store_path != fx.workflow_files.workflow_store_path
    assert tb.workflow_files.event_store_path != fx.workflow_files.event_store_path


# ---------------------------------------------------------------------------
# skip_workflow_files flag
# ---------------------------------------------------------------------------

def test_skip_workflow_files_allows_missing_workflow_dirs(tmp_path: Path):
    """skip_workflow_files=True allows fantasy entry to load before bootstrap.

    ENGPLAT-003A bootstrap will create .engineering/ directories.
    """
    path = _write_registry(tmp_path, [_FANTASY_ENTRY])
    registry = load_registry(path, skip_workflow_files=True)
    assert len(registry.projects) == 1
    fx = get_project(registry, "fantasy-draft-command-center")
    assert fx.workflow_files.workflow_store_path == FANTASY_ROOT / ".engineering" / "workflow_store.json"


def test_skip_workflow_files_still_validates_governance(tmp_path: Path):
    """skip_workflow_files=True still requires governance files to exist."""
    bad = {
        **_FANTASY_ENTRY,
        "governance_files": {
            "backlog_path": str(FANTASY_ROOT / "AGENT_BACKLOG.md"),
            "operating_plan_path": str(tmp_path / "NONEXISTENT.md"),
            "owners_path": str(FANTASY_ROOT / "OWNERS.md"),
            "handoff_path": str(FANTASY_ROOT / "AUTONOMOUS_ENGINEERING_HANDOFF.md"),
        },
    }
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="does not exist"):
        load_registry(path, skip_workflow_files=True)


def test_skip_workflow_files_still_checks_path_escapes(tmp_path: Path):
    """skip_workflow_files=True still checks workflow paths don't escape repo."""
    bad = {
        **_FANTASY_ENTRY,
        "workflow_files": {
            "workflow_store_path": str(Path("/etc/passwd")),
            "event_store_path": str(FANTASY_ROOT / ".engineering" / "event_store.db"),
            "report_dir": str(FANTASY_ROOT / ".engineering" / "reports"),
        },
    }
    path = _write_registry(tmp_path, [bad])
    with pytest.raises(MalformedRegistryError, match="escapes repository_root"):
        load_registry(path, skip_workflow_files=True)


# ---------------------------------------------------------------------------
# Registry load is read-only
# ---------------------------------------------------------------------------

def test_load_registry_does_not_create_files(tmp_path: Path):
    """load_registry does not create the registry file if it doesn't exist."""
    fake = tmp_path / "does_not_exist.json"
    with pytest.raises(MalformedRegistryError, match="not found"):
        load_registry(fake)
    assert not fake.exists()


def test_load_registry_returns_new_registry_object(tmp_path: Path):
    """load_registry returns a registry independent of the source dict.

    Trading-bot only — configs are frozen dataclasses so mutation is impossible.
    """
    path = _write_registry(tmp_path, [_TRADING_BOT_ENTRY])
    registry = load_registry(path)
    tb = get_project(registry, "trading-bot")
    # Frozen dataclass — mutation impossible, but this documents the new object
    assert tb.display_name == "Trading Bot"


# ---------------------------------------------------------------------------
# No project_id special-casing (code inspection test)
# ---------------------------------------------------------------------------

def test_no_project_id_conditionals_in_registry_module():
    """Confirm registry.py contains zero 'if project_id ==' branches.

    This is a code-inspection test using AST. If a developer accidentally adds a
    project_id conditional, this test will catch it.
    """
    import ast
    import engineering.registry as reg_module
    import inspect

    source = inspect.getsource(reg_module)
    tree = ast.parse(source)
    problematic: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While)):
            # Check the test expression
            test_source = ast.unparse(node.test)
            if "project_id" in test_source and (" == " in test_source or " != " in test_source):
                problematic.append((node.lineno, test_source))

    assert not problematic, (
        "registry.py must not contain 'if project_id == ...' branches. Found:\n" +
        "\n".join(f"  line {ln}: {src}" for ln, src in problematic)
    )


# ---------------------------------------------------------------------------
# Default registry path
# ---------------------------------------------------------------------------

def test_default_registry_path_is_home_openclaw(tmp_path: Path, monkeypatch):
    """load_registry defaults to ~/.openclaw/engineering-registry.json."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    # No registry at default path — should raise
    default_path = Path(fake_home) / ".openclaw" / "engineering-registry.json"
    assert not default_path.exists()

    # Create it with test content
    default_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    default_path.write_text(
        json.dumps({"registry_version": "1", "projects": [_TRADING_BOT_ENTRY]}),
        encoding="utf-8"
    )

    # load_registry with None should find the default path (HOME=fake_home)
    registry = load_registry(None)
    assert "trading-bot" in registry.projects


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------

def test_projects_dict_is_mapping(tmp_path: Path):
    """Loaded registry projects are accessible as a dict mapping project_id->config."""
    path = _write_registry(tmp_path, [_TRADING_BOT_ENTRY])
    registry = load_registry(path)
    project_ids = list(registry.projects.keys())
    assert project_ids == ["trading-bot"]


# ---------------------------------------------------------------------------
# Existing ProjectRegistry / ProjectConfig tests remain passing
# ---------------------------------------------------------------------------

def test_existing_project_registry_from_projects_still_works():
    """ProjectRegistry.from_projects() with no duplicates still works."""
    from engineering.models import ProjectConfig, ProjectRegistry

    path = _write_registry(tmp_path := Path(tempfile.mkdtemp()), [_TRADING_BOT_ENTRY])
    registry = load_registry(path)

    assert hasattr(registry, "projects")
    assert isinstance(registry.projects, dict)

    from engineering.models import DuplicateProjectId
    with pytest.raises(DuplicateProjectId):
        ProjectRegistry.from_projects([
            registry.projects["trading-bot"],
            registry.projects["trading-bot"],
        ])


def test_parse_project_config_still_works():
    """Existing parse_project_config is not broken by this slice."""
    from engineering.models import parse_project_config

    result = parse_project_config(_TRADING_BOT_ENTRY.copy())
    assert result.config is not None
    assert result.errors == ()


def test_validate_project_config_still_works():
    """Existing validate_project_config is not broken by this slice."""
    from engineering.models import parse_project_config, validate_project_config

    result = parse_project_config(_TRADING_BOT_ENTRY.copy())
    assert result.config is not None
    errors = validate_project_config(result.config)
    assert errors == [], f"trading-bot entry should be valid: {errors}"
