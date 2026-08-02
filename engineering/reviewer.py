from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from engineering.models import CriterionStatus, ReviewRecommendation


MAX_EVIDENCE_BYTES = 65536


@dataclass(frozen=True)
class CriterionEvidence:
    criterion: str
    proof_method: str
    exact_result: str
    status: CriterionStatus


@dataclass(frozen=True)
class ReviewDecision:
    criteria: tuple[CriterionEvidence, ...]
    recommendation: ReviewRecommendation


def _resolve_evidence_path(repo_root: Path, evidence_path: Path | None) -> Path:
    configured_value = os.environ.get("ENGINEERING_REVIEW_EVIDENCE_PATH", "")
    if evidence_path is None and not configured_value:
        raise RuntimeError(
            "Review evidence is not configured; set "
            "ENGINEERING_REVIEW_EVIDENCE_PATH."
        )
    configured = evidence_path or Path(configured_value)
    resolved_root = repo_root.resolve()
    resolved = (
        configured if configured.is_absolute() else resolved_root / configured
    ).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise RuntimeError("Review evidence must be inside the repository.")
    if not resolved.is_file():
        raise RuntimeError(f"Review evidence file does not exist: {resolved}")
    if resolved.stat().st_size > MAX_EVIDENCE_BYTES:
        raise RuntimeError("Review evidence exceeds the 65,536-byte limit.")
    return resolved


def review_criteria(
    repo_root: Path,
    acceptance_criteria: tuple[str, ...],
    *,
    evidence_path: Path | None = None,
) -> ReviewDecision:
    if not acceptance_criteria:
        raise RuntimeError("Cannot review a task without acceptance criteria.")
    path = _resolve_evidence_path(repo_root, evidence_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_criteria = payload["criteria"]
        if not isinstance(raw_criteria, list):
            raise TypeError("criteria must be a list")
        if any(
            not isinstance(item, dict)
            or not all(
                isinstance(item.get(field), str)
                for field in ("criterion", "proof_method", "exact_result", "status")
            )
            for item in raw_criteria
        ):
            raise TypeError("criterion evidence fields must be strings")
        evidence = tuple(
            CriterionEvidence(
                criterion=item["criterion"],
                proof_method=item["proof_method"],
                exact_result=item["exact_result"],
                status=CriterionStatus(item["status"]),
            )
            for item in raw_criteria
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Review evidence is malformed.") from exc

    actual_criteria = tuple(item.criterion for item in evidence)
    if actual_criteria != acceptance_criteria:
        raise RuntimeError(
            "Review evidence must match every acceptance criterion exactly once "
            "and in authoritative order."
        )
    if any(
        not item.proof_method.strip() or not item.exact_result.strip()
        for item in evidence
    ):
        raise RuntimeError("Review proof method and exact result cannot be blank.")

    recommendation = (
        ReviewRecommendation.ACCEPT
        if all(item.status is CriterionStatus.PASS for item in evidence)
        else ReviewRecommendation.REWORK
    )
    return ReviewDecision(evidence, recommendation)
