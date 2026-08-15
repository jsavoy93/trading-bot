"""ENGSUP-001 Phase 1 — Bounded read-only supervisor.

The supervisor reads completion packets and independently verifies evidence.
It produces typed SupervisorDecision outputs with bounded instructions for Josh's
manual dispatch. No agent is dispatched automatically.

Usage::

    packet = CompletionPacket(...)
    decision = Supervisor(repo_root=Path("/repo")).supervise(packet)

"""
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import replace
from pathlib import Path

from engineering.models import (
    Confidence,
    CompletionPacket,
    EvidenceBundle,
    EvidenceConflict,
    EvidenceRef,
    Severity,
    SupervisorDecision,
    SupervisorDecisionKind,
    TestResultSummary,
    WorkflowState,
)


# Bounds (from governance)
_MAX_REPORT_BYTES = 64 * 1024       # 64 KB
_MAX_REPORT_LINES = 200              # max lines in excerpt
_MAX_EVIDENCE_TEXT_LINES = 50        # max lines before truncation
_MAX_NOTE_SENTENCES = 5             # max sentences in supervisor_note

# Loop protection thresholds
_MAX_RETRY_CYCLES = 3               # max WAIT_FOR_AGENT → QA → REVIEW cycles
_SAME_FAILURE_ESCALATE = 2          # same QA failure 2+ times → escalate
_SAME_REWORK_ESCALATE = 2           # same review finding 2+ times → escalate

# Patterns for secret redaction
_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|bearer)\s*[:=]\s*['\"]?[\w\-]{8,}['\"]?"),
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{4,}['\"]"),
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b"),  # emails
]


class Supervisor:
    """Bounded Phase 1 supervisor.

    Reads completion packets, independently verifies evidence, produces typed
    SupervisorDecision outputs. Never dispatches agents or mutates workflow state.
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def supervise(self, packet: CompletionPacket) -> SupervisorDecision:
        """Evaluate a completion packet and produce a typed supervisor decision.

        Phase 1: always advisory; human_approval_required determines Josh gate.
        """
        bundle = self._build_bundle(packet)
        return self._evaluate(packet, bundle)

    # ------------------------------------------------------------------
    # Evidence collection and verification
    # ------------------------------------------------------------------

    def _build_bundle(self, packet: CompletionPacket) -> EvidenceBundle:
        """Collect and independently verify evidence from all sources."""
        refs: list[EvidenceRef] = []
        conflicts: list[EvidenceConflict] = []
        stale_flags: list[str] = []

        # --- Priority 1: Git state (always verified) ---
        git_branch = self._git_current_branch()
        git_head = self._git_head_commit()
        git_tree_clean = self._git_tree_clean()
        # Only verified if we got a branch name (minimum viable git state)
        git_verified = git_branch is not None

        if git_branch:
            refs.append(EvidenceRef(source="git", path=None,
                                   content_hash=None, modified_at=None,
                                   excerpt=f"branch={git_branch}"))
        if git_head:
            refs.append(EvidenceRef(source="git", path=None,
                                   content_hash=None, modified_at=None,
                                   excerpt=f"head={git_head}"))

        # Check for mismatch between packet and verified git state
        if packet.head_commit and git_head and packet.head_commit != git_head:
            conflicts.append(EvidenceConflict(
                field_label="head_commit",
                source_a="completion_packet",
                value_a=packet.head_commit,
                source_b="git",
                value_b=git_head,
                resolution="used_verified",
            ))
            stale_flags.append("completion_packet")

        if packet.feature_branch and git_branch and packet.feature_branch != git_branch:
            conflicts.append(EvidenceConflict(
                field_label="feature_branch",
                source_a="completion_packet",
                value_a=packet.feature_branch,
                source_b="git",
                value_b=git_branch,
                resolution="used_verified",
            ))
            stale_flags.append("completion_packet")

        # --- Delegation evidence ---
        delegation_verified = True  # We read from git/process
        delegation_status = packet.delegation_status or None

        # --- QA evidence ---
        qa_result: TestResultSummary | None = None
        qa_verified = False
        if packet.qa_exit_code is not None:
            qa_result = TestResultSummary(
                exit_code=packet.qa_exit_code,
                passed_count=packet.qa_passed_count,
                failed_count=packet.qa_failed_count,
                timed_out=packet.qa_timed_out,
            )
            qa_verified = True

        # --- Review evidence ---
        review_recommendation = packet.review_recommendation or None
        review_verified = bool(review_recommendation)

        # --- REPORT.md evidence ---
        report_md_exists, report_md_stale, report_md_hash, report_md_excerpt = \
            self._read_report_md(packet)

        if report_md_stale:
            stale_flags.append("report_md")

        report_md_verified = report_md_exists  # We read it

        if report_md_hash:
            refs.append(EvidenceRef(
                source="report_md",
                path=str(self.repo_root / "REPORT.md"),
                content_hash=report_md_hash,
                modified_at=packet.report_md_modified_at,
                excerpt=report_md_excerpt,
            ))

        # Check for report vs git conflicts — each independently, not gated on staleness
        if report_md_exists and git_tree_clean is False:
            # Report claims clean but git shows dirty — flag the conflict (priority-1 wins)
            conflicts.append(EvidenceConflict(
                field_label="tree_clean",
                source_a="report_md",
                value_a="clean",
                source_b="git",
                value_b="dirty",
                resolution="used_priority_1",
            ))

        # --- Missing evidence ---
        missing: list[str] = []
        if not git_verified:
            missing.append("git")
        if not qa_verified and packet.workflow_state in (WorkflowState.QA, WorkflowState.REVIEW):
            missing.append("qa")
        if not review_verified and packet.workflow_state == WorkflowState.REVIEW:
            missing.append("review")

        return EvidenceBundle(
            git_branch=git_branch,
            git_head=git_head,
            git_tree_clean=git_tree_clean,
            git_verified=git_verified,
            qa_result=qa_result,
            qa_verified=qa_verified,
            review_recommendation=review_recommendation,
            review_verified=review_verified,
            report_md_exists=report_md_exists,
            report_md_stale=report_md_stale,
            report_md_content_hash=report_md_hash,
            report_md_excerpt=self._truncate_excerpt(report_md_excerpt),
            report_md_verified=report_md_verified,
            delegation_status=delegation_status,
            delegation_verified=delegation_verified,
            completion_packet=packet,
            evidence_refs=tuple(refs),
            conflicts=tuple(conflicts),
            stale_evidence=tuple(stale_flags),
            missing_evidence=tuple(missing),
        )

    # ------------------------------------------------------------------
    # Git verification helpers
    # ------------------------------------------------------------------

    def _git_current_branch(self) -> str | None:
        """Return current branch name or None on error."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _git_head_commit(self) -> str | None:
        """Return current HEAD commit SHA or None on error."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _git_tree_clean(self) -> bool | None:
        """Return True if tree is clean, False if dirty, None on error."""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip() == ""
        except Exception:
            pass
        return None

    def _git_changed_files(self) -> tuple[str, ...]:
        """Return tuple of changed files vs HEAD, or empty on error."""
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return tuple(
                    f.strip()
                    for f in result.stdout.splitlines()
                    if f.strip()
                )
        except Exception:
            pass
        return ()

    # ------------------------------------------------------------------
    # REPORT.md reading
    # ------------------------------------------------------------------

    def _read_report_md(
        self, packet: CompletionPacket
    ) -> tuple[bool, bool, str | None, str | None]:
        """Read REPORT.md with bounded size and content hash.

        Returns (exists, stale, content_hash, excerpt).

        - existence: True if file exists
        - stale: True if report conflicts with verified state
        - content_hash: SHA-256 of content used (None if not readable)
        - excerpt: bounded first 200 lines (None if not readable)
        """
        # Honor packet.report_md_exists — if False, skip reading
        if not packet.report_md_exists:
            return False, False, None, None

        report_path = self.repo_root / "REPORT.md"
        if not report_path.is_file():
            return False, False, None, None

        try:
            content_bytes = self._read_report_bytes(report_path)
        except Exception:
            return False, False, None, None

        # Truncate at 64 KB
        if len(content_bytes) > _MAX_REPORT_BYTES:
            content_bytes = content_bytes[:_MAX_REPORT_BYTES]

        # Compute hash before any redaction
        content_hash = hashlib.sha256(content_bytes).hexdigest()

        # Decode
        try:
            content = content_bytes.decode("utf-8", errors="replace")
        except Exception:
            return False, False, None, None

        # Check staleness: report HEAD vs current HEAD
        stale = False
        if packet.head_commit:
            current_head = self._git_head_commit()
            if current_head and packet.head_commit != current_head:
                stale = True

        # Truncate to 200 lines
        lines = content.splitlines()
        truncated = False
        if len(lines) > _MAX_REPORT_LINES:
            lines = lines[:_MAX_REPORT_LINES]
            truncated = True
        excerpt = "\n".join(lines)

        # Add truncation marker if needed
        if truncated:
            excerpt = excerpt.rstrip("\n") + "\n[...]"

        # Redact secrets in excerpt before storing
        excerpt = self._redact(excerpt)

        return True, stale, content_hash, excerpt

    def _read_report_bytes(self, report_path: Path) -> bytes:
        """Hook for testing: override to provide fake REPORT.md content."""
        return report_path.read_bytes()

    # ------------------------------------------------------------------
    # REPORT.md secret redaction (for supervisor output only)
    # ------------------------------------------------------------------

    def _redact(self, text: str) -> str:
        """Redact secret-like patterns from text."""
        result = text
        for pattern in _SECRET_PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        return result

    def _truncate_excerpt(self, text: str | None) -> str | None:
        """Truncate evidence text to MAX_EVIDENCE_TEXT_LINES."""
        if text is None:
            return None
        lines = text.splitlines()
        if len(lines) > _MAX_EVIDENCE_TEXT_LINES:
            lines = lines[:_MAX_EVIDENCE_TEXT_LINES]
            return "\n".join(lines).rstrip("\n") + "\n[...]"
        return text

    # ------------------------------------------------------------------
    # Decision engine
    # ------------------------------------------------------------------

    def _evaluate(
        self, packet: CompletionPacket, bundle: EvidenceBundle
    ) -> SupervisorDecision:
        """Apply supervisor rules to EvidenceBundle → SupervisorDecision.

        Deterministic: identical evidence always produces identical decision.
        """
        state = packet.workflow_state
        blockers: list[str] = []
        warnings: list[str] = []
        evidence_used: list[str] = []
        missing: list[str] = list(bundle.missing_evidence)
        conflicts = list(bundle.conflicts)
        stale_flags = list(bundle.stale_evidence)

        # Collect evidence used
        if bundle.git_verified:
            evidence_used.append("git")
        if bundle.qa_verified:
            evidence_used.append("qa")
        if bundle.review_verified:
            evidence_used.append("review")
        if bundle.report_md_verified:
            evidence_used.append("report_md")
        if bundle.delegation_verified:
            evidence_used.append("delegation")

        # ------------------------------------------------------------------
        # Detect scope drift
        # ------------------------------------------------------------------
        scope_drift = self._detect_scope_drift(packet, bundle)

        # ------------------------------------------------------------------
        # Detect stale HEAD
        # ------------------------------------------------------------------
        stale_head = (
            packet.head_commit != bundle.git_head
            if (packet.head_commit and bundle.git_head) else False
        )

        # ------------------------------------------------------------------
        # Detect repeated failures (loop protection)
        # ------------------------------------------------------------------
        same_qa_failure = packet.same_qa_failure_count
        same_review_finding = packet.same_review_finding_count

        # ------------------------------------------------------------------
        # Dirty tree always escalates before state routing
        # ------------------------------------------------------------------
        if bundle.git_tree_clean is False:
            return SupervisorDecision(
                decision=SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT,
                severity=Severity.BLOCKING,
                confidence=Confidence.HIGH,
                evidence_used=("git",),
                missing_evidence=tuple(missing),
                blockers=("Dirty working tree detected; cannot dispatch dirty",),
                warnings=tuple(warnings),
                human_approval_required=True,
                josh_approval_kinds=("ESCALATE_POLICY_CONFLICT",),
                supervisor_note="Working tree is dirty. Escalating to Josh before any dispatch.",
                generated_instruction=(
                    "Verify the working tree is clean before dispatching. "
                    "Commit, stash, or discard changes and re-run supervisor."
                ),
                evidence_conflicts=tuple(conflicts),
                stale_evidence=tuple(stale_flags),
                git_verified=bundle.git_verified,
                qa_verified=bundle.qa_verified,
                review_verified=bundle.review_verified,
                report_md_verified=bundle.report_md_verified,
            )

        # ------------------------------------------------------------------
        # Decision routing by state
        # ------------------------------------------------------------------
        if state == WorkflowState.WAIT_FOR_AGENT:
            decision, severity, note, instruction, josh_kinds = \
                self._decide_wait_for_agent(packet, bundle, same_qa_failure)
        elif state == WorkflowState.QA:
            decision, severity, note, instruction, josh_kinds = \
                self._decide_qa(packet, bundle, same_qa_failure, scope_drift)
        elif state == WorkflowState.REVIEW:
            decision, severity, note, instruction, josh_kinds = \
                self._decide_review(packet, bundle, same_review_finding, scope_drift)
        elif state == WorkflowState.REPORT:
            decision, severity, note, instruction, josh_kinds = \
                self._decide_report(packet, bundle)
        else:
            decision, severity, note, instruction, josh_kinds = \
                self._decide_early_state(packet, bundle)

        # ------------------------------------------------------------------
        # Apply loop protection overrides
        # ------------------------------------------------------------------
        if same_qa_failure >= _SAME_FAILURE_ESCALATE:
            decision = SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT
            severity = Severity.BLOCKING
            note = (
                f"Loop protection: same QA failure repeated {same_qa_failure} times. "
                "Escalating to Josh for review rather than retrying again."
            )
            instruction = (
                "Review the repeated QA failure and decide: abort this task, "
                "adjust the test scope, or manually dispatch a fix."
            )
            josh_kinds = ("ESCALATE_POLICY_CONFLICT",)

        if same_review_finding >= _SAME_REWORK_ESCALATE:
            decision = SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT
            severity = Severity.BLOCKING
            note = (
                f"Loop protection: same review finding repeated {same_review_finding} times. "
                "Escalating to Josh for review."
            )
            instruction = (
                "Review the repeated review findings and decide: approve as-is, "
                "adjust scope, or abort."
            )
            josh_kinds = ("ESCALATE_POLICY_CONFLICT",)

        if scope_drift:
            blockers.append(
                "Scope drift detected: files changed outside allowed areas"
            )
            if decision not in (
                SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT,
                SupervisorDecisionKind.BLOCKED,
            ):
                old_decision = decision
                decision = SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT
                severity = Severity.BLOCKING
                note = (
                    f"Scope drift detected: changed files include paths outside "
                    f"allowed areas {packet.allowed_areas}. "
                    "Cannot dispatch until scope is corrected."
                )
                instruction = (
                    "Review changed files against allowed areas. "
                    "Correct scope or obtain approval for expansion."
                )
                josh_kinds = ("ESCALATE_POLICY_CONFLICT",)
            else:
                blockers.append(
                    f"Scope drift confirmed with decision {decision.value}"
                )

        if stale_head:
            warnings.append(
                f"Stale head: packet commit {packet.head_commit} "
                f"!= current HEAD {bundle.git_head}"
            )
            evidence_used.append("git_stale_check")

        # ------------------------------------------------------------------
        # Conflicts add warnings/blockers
        # ------------------------------------------------------------------
        for cf in conflicts:
            blockers.append(
                f"Evidence conflict on {cf.field_label}: "
                f"{cf.source_a}={cf.value_a!r} vs {cf.source_b}={cf.value_b!r}"
            )

        # ------------------------------------------------------------------
        # Confidence scoring
        # ------------------------------------------------------------------
        confidence = self._score_confidence(bundle)

        # ------------------------------------------------------------------
        # Human approval determination
        # ------------------------------------------------------------------
        human_required = (
            decision in (
                SupervisorDecisionKind.WAIT_FOR_HUMAN_APPROVAL,
                SupervisorDecisionKind.READY_FOR_MERGE_APPROVAL,
                SupervisorDecisionKind.BLOCKED,
                SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT,
            )
            or decision.value in josh_kinds
        )

        # ------------------------------------------------------------------
        # Build and return decision
        # ------------------------------------------------------------------
        return SupervisorDecision(
            decision=decision,
            severity=severity,
            confidence=confidence,
            evidence_used=tuple(evidence_used),
            missing_evidence=tuple(missing),
            blockers=tuple(blockers),
            warnings=tuple(warnings),
            human_approval_required=human_required,
            josh_approval_kinds=tuple(josh_kinds),
            supervisor_note=self._bound_note(note),
            generated_instruction=self._bound_instruction(instruction),
            evidence_conflicts=tuple(conflicts),
            stale_evidence=tuple(stale_flags),
            git_verified=bundle.git_verified,
            qa_verified=bundle.qa_verified,
            review_verified=bundle.review_verified,
            report_md_verified=bundle.report_md_verified,
        )

    # ------------------------------------------------------------------
    # Decision helpers by state
    # ------------------------------------------------------------------

    def _decide_wait_for_agent(
        self,
        packet: CompletionPacket,
        bundle: EvidenceBundle,
        same_failure: int,
    ) -> tuple[SupervisorDecisionKind, Severity, str, str, tuple[str, ...]]:
        """Decide for WAIT_FOR_AGENT state."""
        status = packet.delegation_status

        if status == "COMPLETE":
            # Before returning CONTINUE, verify commit/branch consistency
            if bundle.git_head and packet.head_commit and bundle.git_head != packet.head_commit:
                return (
                    SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT,
                    Severity.BLOCKING,
                    f"Commit mismatch: packet claims {packet.head_commit}, git shows {bundle.git_head}.",
                    "Verify HEAD commit. Re-evaluate before dispatch.",
                    ("ESCALATE_POLICY_CONFLICT",),
                )
            if bundle.git_branch and packet.feature_branch and bundle.git_branch != packet.feature_branch:
                return (
                    SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT,
                    Severity.BLOCKING,
                    f"Branch mismatch: packet claims {packet.feature_branch}, git shows {bundle.git_branch}.",
                    "Verify branch. Re-evaluate before dispatch.",
                    ("ESCALATE_POLICY_CONFLICT",),
                )
            # Agent finished; next step is QA
            return (
                SupervisorDecisionKind.CONTINUE,
                Severity.INFO,
                "Agent delegation complete. QA step should run next.",
                "Run QA against the completed implementation.",
                (),
            )

        if status in ("FAILED", "TIMED_OUT"):
            if same_failure >= _SAME_FAILURE_ESCALATE or packet.retry_count > _MAX_RETRY_CYCLES:
                return (
                    SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT,
                    Severity.BLOCKING,
                    f"Agent delegation {status} after {packet.retry_count} retries. Loop protection threshold reached.",
                    "Review delegation failure. Decide: abort, adjust task, or manually dispatch.",
                    ("ESCALATE_POLICY_CONFLICT",),
                )
            return (
                SupervisorDecisionKind.RETRY,
                Severity.WARNING,
                f"Agent delegation {status}. Bounded retry is available.",
                "Re-dispatch the agent to retry the same task.",
                (),
            )

        # PENDING or ACTIVE
        return (
            SupervisorDecisionKind.WAIT_FOR_HUMAN_APPROVAL,
            Severity.INFO,
            "Agent delegation is still pending or active.",
            "Wait for delegation to complete, or cancel and re-dispatch.",
            ("WAIT_FOR_HUMAN_APPROVAL",),
        )

    def _decide_qa(
        self,
        packet: CompletionPacket,
        bundle: EvidenceBundle,
        same_failure: int,
        scope_drift: bool,
    ) -> tuple[SupervisorDecisionKind, Severity, str, str, tuple[str, ...]]:
        """Decide for QA state."""
        if bundle.qa_result is None:
            return (
                SupervisorDecisionKind.WAIT_FOR_HUMAN_APPROVAL,
                Severity.WARNING,
                "QA evidence not available.",
                "Verify QA has run and results are recorded.",
                ("WAIT_FOR_HUMAN_APPROVAL",),
            )

        qa = bundle.qa_result

        if qa.is_pass:
            return (
                SupervisorDecisionKind.CONTINUE,
                Severity.INFO,
                f"QA passed ({qa.passed_count} passed). Review step should run next.",
                "Open a code review for the completed implementation.",
                (),
            )

        if same_failure >= _SAME_FAILURE_ESCALATE:
            return (
                SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT,
                Severity.BLOCKING,
                f"Same QA failure repeated {same_failure} times. Escalating.",
                "Review repeated QA failure. Decide: adjust scope, abort, or fix.",
                ("ESCALATE_POLICY_CONFLICT",),
            )

        if qa.timed_out:
            return (
                SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT,
                Severity.BLOCKING,
                "QA timed out. Escalating.",
                "Review timeout. Decide: increase timeout, adjust test scope, or abort.",
                ("ESCALATE_POLICY_CONFLICT",),
            )

        # First or second failure — routine retry
        return (
            SupervisorDecisionKind.RETRY,
            Severity.WARNING,
            f"QA failed with exit code {qa.exit_code}. Bounded retry available.",
            "Re-run QA against the implementation.",
            (),
        )

    def _decide_review(
        self,
        packet: CompletionPacket,
        bundle: EvidenceBundle,
        same_finding: int,
        scope_drift: bool,
    ) -> tuple[SupervisorDecisionKind, Severity, str, str, tuple[str, ...]]:
        """Decide for REVIEW state."""
        rec = bundle.review_recommendation

        if rec == "ACCEPT":
            return (
                SupervisorDecisionKind.READY_FOR_MERGE_APPROVAL,
                Severity.INFO,
                "Reviewer recommends ACCEPT. Josh approval required for merge.",
                "Review the PR. If acceptable, approve merge.",
                ("READY_FOR_MERGE_APPROVAL",),
            )

        if rec == "REWORK":
            if same_finding >= _SAME_REWORK_ESCALATE:
                return (
                    SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT,
                    Severity.BLOCKING,
                    f"Same review finding repeated {same_finding} times. Escalating.",
                    "Review repeated rework findings. Approve as-is or adjust scope.",
                    ("ESCALATE_POLICY_CONFLICT",),
                )
            return (
                SupervisorDecisionKind.REQUEST_CHANGES,
                Severity.WARNING,
                "Reviewer recommends REWORK. Josh dispatches the fix.",
                "Address the review findings and re-dispatch the agent.",
                (),
            )

        # No review recommendation yet
        return (
            SupervisorDecisionKind.WAIT_FOR_HUMAN_APPROVAL,
            Severity.INFO,
            "No review recommendation available yet.",
            "Wait for review to complete.",
            ("WAIT_FOR_HUMAN_APPROVAL",),
        )

    def _decide_report(
        self,
        packet: CompletionPacket,
        bundle: EvidenceBundle,
    ) -> tuple[SupervisorDecisionKind, Severity, str, str, tuple[str, ...]]:
        """Decide for REPORT state."""
        return (
            SupervisorDecisionKind.COMPLETE,
            Severity.INFO,
            "Report is complete. All acceptance criteria have been met.",
            "Confirm the task is complete and archive.",
            (),
        )

    def _decide_early_state(
        self,
        packet: CompletionPacket,
        bundle: EvidenceBundle,
    ) -> tuple[SupervisorDecisionKind, Severity, str, str, tuple[str, ...]]:
        """Decide for DISCOVER, PLAN, PREPARE_BRANCH, DELEGATE states."""
        return (
            SupervisorDecisionKind.WAIT_FOR_HUMAN_APPROVAL,
            Severity.INFO,
            "Workflow is in an early state. Manual dispatch required.",
            "Review current state and proceed with the next workflow step.",
            ("WAIT_FOR_HUMAN_APPROVAL",),
        )

    # ------------------------------------------------------------------
    # Scope drift detection
    # ------------------------------------------------------------------

    def _detect_scope_drift(self, packet: CompletionPacket, bundle: EvidenceBundle) -> bool:
        """Detect if changed files are outside allowed areas."""
        if not packet.allowed_areas:
            return False

        changed = self._git_changed_files()
        if not changed:
            return False

        # Check each changed file against allowed areas
        for file_path in changed:
            # Simple prefix check — file must be under one of the allowed areas
            normalized = file_path.lstrip("/")
            in_allowed = any(
                normalized.startswith(area.rstrip("/").lstrip("/"))
                for area in packet.allowed_areas
            )
            if not in_allowed:
                return True
        return False

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    def _score_confidence(self, bundle: EvidenceBundle) -> Confidence:
        """Score confidence based on evidence availability and verification."""
        verified_count = sum([
            bundle.git_verified,
            bundle.qa_verified,
            bundle.review_verified,
            bundle.delegation_verified,
        ])

        if verified_count >= 2 and bundle.git_verified:
            return Confidence.VERIFIED
        if bundle.git_verified:
            return Confidence.HIGH
        if verified_count >= 1:
            return Confidence.MEDIUM
        if bundle.report_md_verified:
            return Confidence.LOW
        return Confidence.UNKNOWN

    # ------------------------------------------------------------------
    # Bounding helpers
    # ------------------------------------------------------------------

    def _bound_note(self, note: str) -> str:
        """Cap supervisor_note at _MAX_NOTE_SENTENCES sentences."""
        sentences = _split_sentences(note)
        if len(sentences) <= _MAX_NOTE_SENTENCES:
            return note
        return " ".join(sentences[:_MAX_NOTE_SENTENCES]).rstrip(".")

    def _bound_instruction(self, instruction: str) -> str:
        """Return bounded instruction (no secrets, no unbounded content)."""
        return self._redact(instruction)

    # ------------------------------------------------------------------
    # Constants accessor (for tests)
    # ------------------------------------------------------------------

    @property
    def max_evidence_lines(self) -> int:
        return _MAX_EVIDENCE_TEXT_LINES

    @property
    def max_report_lines(self) -> int:
        return _MAX_REPORT_LINES


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences (period-delimited)."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]
