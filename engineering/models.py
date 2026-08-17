from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Existing types (preserved)
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    REVIEW = "REVIEW"
    DONE = "DONE"


class WorkflowState(str, Enum):
    DISCOVER = "DISCOVER"
    PLAN = "PLAN"
    PREPARE_BRANCH = "PREPARE_BRANCH"
    DELEGATE = "DELEGATE"
    WAIT_FOR_AGENT = "WAIT_FOR_AGENT"
    QA = "QA"
    REVIEW = "REVIEW"
    REPORT = "REPORT"
    COMPLETE = "COMPLETE"


class DelegationStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class CriterionStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class ReviewRecommendation(str, Enum):
    ACCEPT = "ACCEPT"
    REWORK = "REWORK"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Complexity(str, Enum):
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"


# ---------------------------------------------------------------------------
# ENGSUP-001 Phase 1: Supervisor types
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    """Decision severity level."""
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"
    INFO = "INFO"


class Confidence(str, Enum):
    """Evidence confidence level based on source verification."""
    VERIFIED = "VERIFIED"   # Independently confirmed by >= 2 sources
    HIGH = "HIGH"          # Single authoritative source confirmed
    MEDIUM = "MEDIUM"      # Corroborated but not independently verified
    LOW = "LOW"            # Single uncorroborated source
    UNKNOWN = "UNKNOWN"     # No evidence available


class SupervisorDecisionKind(str, Enum):
    """Advisory decisions produced by the supervisor.

    These are NOT WorkflowState values. They are supervisor recommendations
    that Josh evaluates for manual dispatch.
    """
    # Routine: next step is clear, no human gate required
    CONTINUE = "CONTINUE"                   # Agent done; next step is QA
    RUN_QA = "RUN_QA"                       # QA should be re-run
    RUN_READ_ONLY_REVIEW = "RUN_READ_ONLY_REVIEW"
    RETRY = "RETRY"                         # Bounded retry; same agent re-dispatch
    REQUEST_CHANGES = "REQUEST_CHANGES"     # Routine rework; Josh dispatches

    # Human approval required
    WAIT_FOR_HUMAN_APPROVAL = "WAIT_FOR_HUMAN_APPROVAL"  # Human gate; cannot auto-dispatch
    READY_FOR_MERGE_APPROVAL = "READY_FOR_MERGE_APPROVAL"  # Josh must approve PR merge

    # Blocked / escalated
    BLOCKED = "BLOCKED"                     # Missing predecessor or impossible condition
    ESCALATE_POLICY_CONFLICT = "ESCALATE_POLICY_CONFLICT"  # Loop protection, scope drift, mismatch

    # Terminal
    COMPLETE = "COMPLETE"                   # All criteria met; task done


@dataclass(frozen=True)
class EvidenceConflict:
    """A detected conflict between two evidence sources."""
    field_label: str
    source_a: str       # e.g. "git", "completion_packet", "report_md", "github_api"
    value_a: str
    source_b: str
    value_b: str
    resolution: str     # e.g. "used_verified", "used_priority_1", "flagged_blocking"


@dataclass(frozen=True)
class TestResultSummary:
    """Summarised QA/test results for supervisor evaluation."""
    exit_code: int
    passed_count: int | None = None
    failed_count: int | None = None
    timed_out: bool = False
    output_summary: str = ""

    @property
    def is_pass(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class EvidenceRef:
    """Reference to a piece of evidence with content hash."""
    source: str                        # "git", "completion_packet", "report_md", "github_api"
    path: str | None = None            # File path if applicable
    content_hash: str | None = None    # SHA-256 of content used
    modified_at: str | None = None     # ISO-8601 timestamp
    excerpt: str | None = None         # Bounded excerpt (≤200 lines)


@dataclass(frozen=True)
class CompletionPacket:
    """Structured completion data from an agent run.

    This is NOT trusted blindly — the supervisor verifies independently.
    """
    version: str = "1.0"
    task_id: str = ""
    task_title: str = ""
    workflow_state: WorkflowState = WorkflowState.DISCOVER
    feature_branch: str = ""
    head_commit: str = ""
    agent_name: str = ""
    delegation_status: str = ""          # DelegationStatus value as string
    delegation_exit_code: int | None = None
    delegation_failure_reason: str = ""
    qa_exit_code: int | None = None
    qa_passed_count: int | None = None
    qa_failed_count: int | None = None
    qa_timed_out: bool = False
    review_recommendation: str = ""     # ReviewRecommendation value as string
    report_md_exists: bool = False
    report_md_modified_at: str | None = None
    report_md_content_hash: str | None = None
    changed_files: tuple[str, ...] = ()
    allowed_areas: tuple[str, ...] = ()
    retry_count: int = 0
    same_qa_failure_count: int = 0      # Repeated identical QA failure count
    same_review_finding_count: int = 0  # Repeated identical review finding count
    generated_at: str = ""


@dataclass(frozen=True)
class EvidenceBundle:
    """Verified evidence assembled by the supervisor.

    Built from authoritative evidence sources in priority order:
    1. Repository verification (Git/GitHub — directly verified)
    2. Structured completion packet
    3. Structured QA/test evidence
    4. REPORT.md (supporting narrative only)
    5. Timestamped implementation reports
    6. PR metadata

    Priority-1 state always overrides lower-priority conflicting state.
    """
    # Verified Git state
    git_branch: str | None = None
    git_head: str | None = None
    git_tree_clean: bool | None = None
    git_verified: bool = False          # True when git state was independently read

    # QA evidence
    qa_result: TestResultSummary | None = None
    qa_verified: bool = False

    # Review evidence
    review_recommendation: str | None = None   # "ACCEPT" or "REWORK"
    review_verified: bool = False

    # REPORT.md evidence
    report_md_exists: bool = False
    report_md_stale: bool = False      # True when report conflicts with verified state
    report_md_content_hash: str | None = None
    report_md_excerpt: str | None = None  # Bounded excerpt, max 200 lines
    report_md_verified: bool = False

    # Delegation evidence
    delegation_status: str | None = None
    delegation_verified: bool = False

    # Completion packet (unverified input)
    completion_packet: CompletionPacket | None = None

    # All evidence references
    evidence_refs: tuple[EvidenceRef, ...] = ()

    # Detected conflicts
    conflicts: tuple[EvidenceConflict, ...] = ()

    # Stale evidence flags
    stale_evidence: tuple[str, ...] = ()   # Source labels flagged as stale

    # Missing evidence (by source label)
    missing_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class SupervisorDecision:
    """Typed supervisor decision output.

    Produced by the supervisor after evaluating an EvidenceBundle.
    This is always advisory — Josh manually dispatches all actions.
    Phase 1: no auto-dispatch; human_approval_required determines Josh gate.
    """
    # Core decision
    decision: SupervisorDecisionKind
    severity: Severity = Severity.INFO
    confidence: Confidence = Confidence.UNKNOWN

    # Evidence used
    evidence_used: tuple[str, ...] = ()    # Source labels used in decision
    missing_evidence: tuple[str, ...] = () # Evidence types that were unavailable

    # Findings
    blockers: tuple[str, ...] = ()        # Blocking issues requiring resolution
    warnings: tuple[str, ...] = ()         # Non-blocking concerns

    # Human action
    human_approval_required: bool = False  # True when Josh must act before proceeding
    josh_approval_kinds: tuple[str, ...] = ()  # Which approval kinds are relevant

    # Bounded output
    supervisor_note: str = ""             # ≤ 5 sentences; no chain-of-thought
    generated_instruction: str = ""        # Bounded next-step instruction for Josh

    # Evidence state
    evidence_conflicts: tuple[EvidenceConflict, ...] = ()  # Any conflicts detected
    stale_evidence: tuple[str, ...] = ()   # Evidence IDs flagged as stale

    # Verification state
    git_verified: bool = False
    qa_verified: bool = False
    review_verified: bool = False
    report_md_verified: bool = False


# ---------------------------------------------------------------------------
# Existing dataclasses (preserved)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BacklogTask:
    task_id: str
    title: str
    status: TaskStatus
    owner: str
    priority: Priority
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    allowed_areas: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_available(self) -> bool:
        return self.status is TaskStatus.TODO


@dataclass(frozen=True)
class RepositoryState:
    root: Path
    branch: str
    is_clean: bool


@dataclass(frozen=True)
class ExecutionPlan:
    task: BacklogTask
    repository: RepositoryState
    feature_branch: str
    acceptance_criteria: tuple[str, ...]
    allowed_areas: tuple[str, ...]
    risk: RiskLevel
    complexity: Complexity
    workflow_state: WorkflowState = WorkflowState.DISCOVER


# ---------------------------------------------------------------------------
# ENGPLAT-001: Project Registration Contract
# ---------------------------------------------------------------------------

SUPPORTED_SCHEMA_VERSION = "1.0"

# Known top-level ProjectConfig field names (for unknown-field detection)
_PROJECT_CONFIG_FIELDS = frozenset({
    "schema_version",
    "project_id",
    "display_name",
    "repository_root",
    "authoritative_base_branch",
    "governance_files",
    "workflow_files",
    "qa_commands",
    "qa_timeout_seconds",
    "prohibited_operations",
    "agents_may_merge",
    "owner_ids",
    "agent_owners",
})

_GOVERNANCE_FILES_FIELDS = frozenset({
    "backlog_path",
    "operating_plan_path",
    "owners_path",
    "handoff_path",
})

_WORKFLOW_FILES_FIELDS = frozenset({
    "workflow_store_path",
    "event_store_path",
    "report_dir",
})

# ---------------------------------------------------------------------------
# Parse result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParseResult:
    """Result of parse_project_config().

    Attributes
    ----------
    config : ProjectConfig | None
        Non-None only when all structural checks pass.
    errors : tuple[str, ...]
        Deterministic, sanitized error strings. Empty means valid.
    warnings : tuple[str, ...]
        Bounded warnings for optional conditions.
    """

    config: "ProjectConfig | None" = None
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class DuplicateProjectId(Exception):
    """Raised when a project_id collision is detected in ProjectRegistry construction."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Duplicate project_id: {project_id!r}")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GovernanceFiles:
    """Paths to a project's governance documents."""

    backlog_path: Path
    operating_plan_path: Path
    owners_path: Path
    handoff_path: Path


@dataclass(frozen=True)
class WorkflowFiles:
    """Paths to a project's workflow and event persistence."""

    workflow_store_path: Path
    event_store_path: Path
    report_dir: Path


@dataclass(frozen=True)
class ProjectConfig:
    """Typed project registration contract for the engineering platform.

    Attributes
    ----------
    schema_version : str
        Schema version identifier. Only "1.0" is supported.
    project_id : str
        Unique project identifier (slug format).
    display_name : str
        Human-readable project name.
    repository_root : Path
        Absolute path to the managed repository.
    authoritative_base_branch : str
        Base branch for all feature work (e.g., "main").
    governance_files : GovernanceFiles
        Paths to the project's governance documents.
    workflow_files : WorkflowFiles
        Paths to the project's workflow and event persistence.
    qa_commands : tuple[str, ...]
        Pre-configured safe pytest commands.
    qa_timeout_seconds : int
        Maximum allowed QA runtime in seconds. Must be positive.
    prohibited_operations : tuple[str, ...]
        Operations banned for this project (e.g., no_live_trading).
    agents_may_merge : bool
        Whether agents may merge. Always False initially.
    owner_ids : tuple[str, ...]
        Human owner identifiers. At least one required.
    agent_owners : tuple[str, ...]
        Authorized agent identities for this project.
    """

    schema_version: str = "1.0"
    project_id: str = ""
    display_name: str = ""
    repository_root: Path = Path()
    authoritative_base_branch: str = ""
    governance_files: GovernanceFiles = field(
        default_factory=lambda: GovernanceFiles(
            backlog_path=Path(),
            operating_plan_path=Path(),
            owners_path=Path(),
            handoff_path=Path(),
        )
    )
    workflow_files: WorkflowFiles = field(
        default_factory=lambda: WorkflowFiles(
            workflow_store_path=Path(),
            event_store_path=Path(),
            report_dir=Path(),
        )
    )
    qa_commands: tuple[str, ...] = ()
    qa_timeout_seconds: int = 0
    prohibited_operations: tuple[str, ...] = ()
    agents_may_merge: bool = False
    owner_ids: tuple[str, ...] = ()
    agent_owners: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectRegistry:
    """Registry of managed projects.

    Use ``from_projects()`` to construct. Duplicate project IDs raise
    ``DuplicateProjectId`` before the registry is stored.
    """

    _projects: tuple[tuple[str, ProjectConfig], ...] = ()
    version: str = "1.0"

    @classmethod
    def from_projects(
        cls, projects: list[ProjectConfig] | tuple[ProjectConfig, ...]
    ) -> ProjectRegistry:
        """Build a registry from a sequence of ProjectConfig instances.

        Raises DuplicateProjectId if two configs share the same project_id.
        """
        seen: dict[str, ProjectConfig] = {}
        for p in projects:
            if p.project_id in seen:
                raise DuplicateProjectId(p.project_id)
            seen[p.project_id] = p
        return cls(
            _projects=tuple(seen.items()),
            version="1.0",
        )

    @property
    def projects(self) -> dict[str, ProjectConfig]:
        """Return projects as a dict mapping project_id to ProjectConfig."""
        return dict(self._projects)


# ---------------------------------------------------------------------------
# Parsing (structural validation)
# ---------------------------------------------------------------------------


def _parse_scalar(value: Any, expected_type: type, field_name: str) -> tuple[Any, list[str]]:
    """Coerce value to expected_type. Return (coerced, errors)."""
    errors: list[str] = []
    result: Any = value
    if expected_type is Path:
        if isinstance(value, Path):
            result = value
        elif isinstance(value, str):
            result = Path(value)
        else:
            errors.append(f"field '{field_name}': expected str or Path, got {type(value).__name__}")
            result = Path()
    elif expected_type is bool:
        if isinstance(value, bool):
            result = value
        else:
            # Reject non-bool types for bool fields — no silent string coercion
            errors.append(
                f"field '{field_name}': expected bool, got {type(value).__name__}"
            )
            result = False
    elif expected_type is int:
        if isinstance(value, int) and not isinstance(value, bool):
            result = value
        elif isinstance(value, str):
            try:
                result = int(value)
            except ValueError:
                errors.append(f"field '{field_name}': expected int, got {value!r}")
                result = 0
        else:
            errors.append(f"field '{field_name}': expected int, got {type(value).__name__}")
            result = 0
    elif expected_type is str:
        if isinstance(value, str):
            result = value
        else:
            errors.append(f"field '{field_name}': expected str, got {type(value).__name__}")
            result = ""
    elif expected_type is tuple:
        if isinstance(value, (list, tuple)):
            result = tuple(value)
        else:
            errors.append(f"field '{field_name}': expected list or tuple, got {type(value).__name__}")
            result = ()
    return result, errors


def parse_project_config(mapping: dict[str, Any]) -> ParseResult:
    """Parse a mapping into a ProjectConfig, collecting all structural errors.

    Structural checks
    -----------------
    - schema_version missing → error
    - schema_version != "1.0" → error
    - unknown top-level field → error
    - missing required field → error
    - wrong type for a known field → error
    - malformed GovernanceFiles sub-mapping → error
    - malformed WorkflowFiles sub-mapping → error

    Semantic concerns (path existence, policy conflicts, etc.) are NOT checked
    here; those are the responsibility of validate_project_config().
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. schema_version check
    schema_version = mapping.get("schema_version", None)
    if schema_version is None:
        errors.append("field 'schema_version': missing required field")
    elif not isinstance(schema_version, str) or schema_version != SUPPORTED_SCHEMA_VERSION:
        errors.append(
            f"field 'schema_version': unsupported version {schema_version!r}; "
            f"only {SUPPORTED_SCHEMA_VERSION!r} is supported"
        )
        # Cannot continue structural parse with wrong version
        return ParseResult(config=None, errors=tuple(errors), warnings=())

    # 2. Unknown field detection
    unknown_fields = set(mapping.keys()) - _PROJECT_CONFIG_FIELDS
    for k in sorted(unknown_fields):
        errors.append(f"unknown field: {k!r}")

    # 3. Required field presence and type checks
    required_string_fields = ["project_id", "display_name", "authoritative_base_branch"]
    required_tuple_fields = ["qa_commands", "prohibited_operations", "owner_ids", "agent_owners"]
    required_bool_fields = ["agents_may_merge"]
    required_int_fields = ["qa_timeout_seconds"]

    parsed: dict[str, Any] = {"schema_version": SUPPORTED_SCHEMA_VERSION}

    for fname in required_string_fields:
        val = mapping.get(fname, None)
        if val is None:
            errors.append(f"field '{fname}': missing required field")
        else:
            coerced, errs = _parse_scalar(val, str, fname)
            errors.extend(errs)
            parsed[fname] = coerced

    for fname in required_tuple_fields:
        val = mapping.get(fname, None)
        if val is None:
            errors.append(f"field '{fname}': missing required field")
        else:
            coerced, errs = _parse_scalar(val, tuple, fname)
            # additionally check all elements are strings
            if isinstance(val, (list, tuple)):
                for i, elem in enumerate(val):
                    if not isinstance(elem, str):
                        errors.append(
                            f"field '{fname}[{i}]': expected str, got {type(elem).__name__}"
                        )
            errors.extend(errs)
            parsed[fname] = coerced

    for fname in required_bool_fields:
        val = mapping.get(fname, None)
        coerced, errs = _parse_scalar(val, bool, fname)
        # bool is subclasses of int so isinstance check above catches it
        errors.extend(errs)
        parsed[fname] = coerced

    for fname in required_int_fields:
        val = mapping.get(fname, None)
        coerced, errs = _parse_scalar(val, int, fname)
        errors.extend(errs)
        parsed[fname] = coerced

    # 4. repository_root
    repo_root_val = mapping.get("repository_root", None)
    if repo_root_val is None:
        errors.append("field 'repository_root': missing required field")
        repo_root: Path = Path()
    else:
        repo_root, errs = _parse_scalar(repo_root_val, Path, "repository_root")
        errors.extend(errs)
    parsed["repository_root"] = repo_root

    # 5. governance_files sub-mapping
    gov_files_val = mapping.get("governance_files", None)
    if not isinstance(gov_files_val, dict):
        errors.append(
            "field 'governance_files': missing or non-dict value; "
            "required sub-fields: backlog_path, operating_plan_path, owners_path, handoff_path"
        )
        gov_files = GovernanceFiles(
            backlog_path=Path(),
            operating_plan_path=Path(),
            owners_path=Path(),
            handoff_path=Path(),
        )
    else:
        gov_errors, gov_files = _parse_governance_files(gov_files_val)
        errors.extend(gov_errors)
    parsed["governance_files"] = gov_files

    # 6. workflow_files sub-mapping
    wf_val = mapping.get("workflow_files", None)
    if not isinstance(wf_val, dict):
        errors.append(
            "field 'workflow_files': missing or non-dict value; "
            "required sub-fields: workflow_store_path, event_store_path, report_dir"
        )
        wf_files = WorkflowFiles(
            workflow_store_path=Path(),
            event_store_path=Path(),
            report_dir=Path(),
        )
    else:
        wf_errors, wf_files = _parse_workflow_files(wf_val)
        errors.extend(wf_errors)
    parsed["workflow_files"] = wf_files

    # 7. If errors, return early with None config
    if errors:
        return ParseResult(config=None, errors=tuple(errors), warnings=())

    # 8. Construct ProjectConfig
    try:
        config = ProjectConfig(**parsed)
    except TypeError as exc:
        # Should not happen if field list is correct, but guard anyway
        errors.append(f"construction error: {exc}")
        return ParseResult(config=None, errors=tuple(errors), warnings=())

    return ParseResult(config=config, errors=(), warnings=tuple(warnings))


def _parse_governance_files(m: dict[str, Any]) -> tuple[list[str], GovernanceFiles]:
    errors: list[str] = []
    unknown = set(m.keys()) - _GOVERNANCE_FILES_FIELDS
    for k in sorted(unknown):
        errors.append(f"unknown field in governance_files: {k!r}")

    result: dict[str, Path] = {}
    for fname in ["backlog_path", "operating_plan_path", "owners_path", "handoff_path"]:
        val = m.get(fname, None)
        if val is None:
            errors.append(f"field 'governance_files.{fname}': missing required field")
            result[fname] = Path()
        else:
            coerced, errs = _parse_scalar(val, Path, f"governance_files.{fname}")
            errors.extend(errs)
            result[fname] = coerced

    return errors, GovernanceFiles(**result)


def _parse_workflow_files(m: dict[str, Any]) -> tuple[list[str], WorkflowFiles]:
    errors: list[str] = []
    unknown = set(m.keys()) - _WORKFLOW_FILES_FIELDS
    for k in sorted(unknown):
        errors.append(f"unknown field in workflow_files: {k!r}")

    result: dict[str, Path] = {}
    for fname in ["workflow_store_path", "event_store_path", "report_dir"]:
        val = m.get(fname, None)
        if val is None:
            errors.append(f"field 'workflow_files.{fname}': missing required field")
            result[fname] = Path()
        else:
            coerced, errs = _parse_scalar(val, Path, f"workflow_files.{fname}")
            errors.extend(errs)
            result[fname] = coerced

    return errors, WorkflowFiles(**result)


# ---------------------------------------------------------------------------
# Semantic validation
# ---------------------------------------------------------------------------

# QA command safety: reject patterns
_QA_UNSAFE_PATTERNS = [
    (re.compile(r"rm\s+-rf\s+"), "destructive command (rm -rf)"),
    (re.compile(r"shutil\.rmtree"), "destructive command (shutil.rmtree)"),
    (re.compile(r"--live\b"), "live-trading flag (--live)"),
    (re.compile(r"--production\b"), "live-trading flag (--production)"),
    (re.compile(r"--real\b"), "live-trading flag (--real)"),
    (re.compile(r"(?<!-)\b-l\b(?!\s*-)"), "live-trading flag (-l)"),
    (re.compile(r"\$SECRET"), "secret-variable access ($SECRET)"),
    (re.compile(r"\$API_KEY"), "secret-variable access ($API_KEY)"),
    (re.compile(r"\$TOKEN"), "secret-variable access ($TOKEN)"),
    (re.compile(r"print\s*\(\s*.*secret", re.IGNORECASE), "secret-printing pattern"),
    (re.compile(r"&&"), "shell operator (&&)"),
    (re.compile(r"\|\|"), "shell operator (||)"),
    (re.compile(r";\s*curl"), "shell operator with curl (; curl)"),
    (re.compile(r"\|\s*curl"), "pipe to curl"),
]

_QA_ALLOWED_PREFIXES = (
    "pytest",
    "python -m pytest",
    "python -m unittest",
    "python -m nose",
    "npm test",
    "npm run",
    "npx vitest",
)


def _check_qa_command_safety(commands: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    for i, cmd in enumerate(commands):
        # Check for unsafe patterns
        for pattern, description in _QA_UNSAFE_PATTERNS:
            if pattern.search(cmd):
                # Do not include cmd value in error — it may contain secrets
                errors.append(
                    f"qa_commands[{i}]: unsafe pattern '{description}' in command"
                )
        # Check command family
        stripped = cmd.lstrip()
        if stripped and not any(stripped.startswith(p) for p in _QA_ALLOWED_PREFIXES):
            errors.append(
                f"qa_commands[{i}]: command not in approved list; "
                f"command must start with one of {_QA_ALLOWED_PREFIXES}"
            )
    return errors


def validate_project_config(config: ProjectConfig) -> list[str]:
    """Run semantic validation on a fully-constructed ProjectConfig.

    Returns a deterministic list of error strings. Empty list means valid.

    Semantic checks
    ---------------
    - project_id and display_name are non-empty
    - repository_root is absolute and exists
    - all governance file paths resolve under repository_root (no traversal)
    - all workflow file paths resolve under repository_root (no traversal)
    - required governance files exist on disk
    - workflow file parent directories exist on disk
    - report_dir parent directory exists (report_dir itself may be created)
    - qa_commands is non-empty
    - each qa_command passes safety checks
    - qa_timeout_seconds is positive
    - owner_ids and agent_owners are non-empty and have no duplicates
    - agents_may_merge=False (for trading-bot; True requires approval policy)
    """
    errors: list[str] = []

    # project_id must be non-empty
    if not config.project_id or not config.project_id.strip():
        errors.append("project_id cannot be empty")

    # display_name must be non-empty
    if not config.display_name or not config.display_name.strip():
        errors.append("display_name cannot be empty")

    # repository_root checks
    if not config.repository_root.is_absolute():
        errors.append(f"repository_root must be absolute; got: {config.repository_root}")

    if not config.repository_root.exists():
        errors.append(f"repository_root does not exist: {config.repository_root}")

    # Helper: check a path is under repository_root
    def _check_path_under_repo(path: Path, field_label: str) -> None:
        if not path.is_absolute():
            errors.append(f"{field_label}: must be absolute; got {path}")
            return
        try:
            resolved = path.resolve()
            repo_resolved = config.repository_root.resolve()
            # Ensure path is under repo (is_relative_to available in 3.11+)
            try:
                resolved.relative_to(repo_resolved)
            except ValueError:
                errors.append(
                    f"{field_label}: path escapes repository_root; "
                    f"path={path} resolved={resolved} root={repo_resolved}"
                )
        except OSError:
            # Could not resolve (e.g. broken symlink); fail closed
            errors.append(f"{field_label}: could not resolve path: {path}")

    # Check governance files
    gf = config.governance_files
    _check_path_under_repo(gf.backlog_path, "governance_files.backlog_path")
    _check_path_under_repo(gf.operating_plan_path, "governance_files.operating_plan_path")
    _check_path_under_repo(gf.owners_path, "governance_files.owners_path")
    _check_path_under_repo(gf.handoff_path, "governance_files.handoff_path")

    # Required governance files must exist
    for label, path in [
        ("governance_files.backlog_path", gf.backlog_path),
        ("governance_files.operating_plan_path", gf.operating_plan_path),
        ("governance_files.owners_path", gf.owners_path),
        ("governance_files.handoff_path", gf.handoff_path),
    ]:
        if not path.exists():
            errors.append(f"{label}: required governance file does not exist: {path}")

    # Check workflow files
    wf = config.workflow_files
    _check_path_under_repo(wf.workflow_store_path, "workflow_files.workflow_store_path")
    _check_path_under_repo(wf.event_store_path, "workflow_files.event_store_path")
    _check_path_under_repo(wf.report_dir, "workflow_files.report_dir")

    # Workflow parent directories must exist
    for label, path in [
        ("workflow_files.workflow_store_path", wf.workflow_store_path),
        ("workflow_files.event_store_path", wf.event_store_path),
        ("workflow_files.report_dir", wf.report_dir),
    ]:
        if not path.parent.exists():
            errors.append(f"{label}: required parent directory does not exist: {path.parent}")

    # qa_commands must be non-empty
    if not config.qa_commands:
        errors.append("qa_commands cannot be empty")

    # qa_commands safety
    if config.qa_commands:
        qa_errors = _check_qa_command_safety(config.qa_commands)
        errors.extend(qa_errors)

    # qa_timeout_seconds must be positive
    if config.qa_timeout_seconds <= 0:
        errors.append(
            f"qa_timeout_seconds must be positive; got {config.qa_timeout_seconds}"
        )

    # owner_ids must be non-empty and unique
    if not config.owner_ids:
        errors.append("owner_ids cannot be empty; at least one human owner required")
    elif len(set(config.owner_ids)) != len(config.owner_ids):
        errors.append("owner_ids contains duplicate entries")

    # agent_owners must be non-empty and unique
    if not config.agent_owners:
        errors.append("agent_owners cannot be empty")
    elif len(set(config.agent_owners)) != len(config.agent_owners):
        errors.append("agent_owners contains duplicate entries")

    # agents_may_merge must be False for trading-bot
    if config.agents_may_merge:
        errors.append(
            "agents_may_merge=True: trading-bot does not permit autonomous merging; "
            "set to False or define a full approval policy in governance files"
        )

    # prohibited_operations validation: warn if no_live_trading is not present
    if "no_live_trading" not in config.prohibited_operations:
        # This is a warning, not an error, since other prohibited ops may be defined
        pass  # Not adding to errors; governance decides what to prohibit

    return sorted(errors)


# ---------------------------------------------------------------------------
# Trading-bot project instance
# ---------------------------------------------------------------------------

# Resolve paths relative to the trading-bot repository root.
# The repository root is the directory containing this file's package root.
# engineering/ is at the repo root, so parent of Path(__file__).parent is the repo root.
_REPO_ROOT = Path(__file__).parent.parent.resolve()


TRADING_BOT_PROJECT = ProjectConfig(
    schema_version="1.0",
    project_id="trading-bot",
    display_name="Trading Bot",
    repository_root=_REPO_ROOT,
    authoritative_base_branch="main",
    governance_files=GovernanceFiles(
        backlog_path=_REPO_ROOT / "AGENT_BACKLOG.md",
        operating_plan_path=_REPO_ROOT / "AGENT_OPERATING_PLAN.md",
        owners_path=_REPO_ROOT / "OWNERS.md",
        handoff_path=_REPO_ROOT / "TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md",
    ),
    workflow_files=WorkflowFiles(
        workflow_store_path=_REPO_ROOT / "engineering" / "workflow_store.json",
        event_store_path=_REPO_ROOT / "engineering" / "event_store.db",
        report_dir=_REPO_ROOT / "reports",
    ),
    qa_commands=(
        "python -m pytest tests/test_engineering_project_config.py -v",
        "python -m pytest tests/test_engineering_models.py -v",
    ),
    qa_timeout_seconds=300,
    prohibited_operations=("no_live_trading", "no_brokerage_access"),
    agents_may_merge=False,
    owner_ids=("josh",),
    agent_owners=("trading-manager",),
)
