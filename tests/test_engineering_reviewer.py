import json
from pathlib import Path

import pytest

from engineering.models import CriterionStatus, ReviewRecommendation
from engineering.reviewer import MAX_EVIDENCE_BYTES, review_criteria


CRITERIA = ("First criterion", "Second criterion")


def write_evidence(
    tmp_path: Path,
    *,
    statuses: tuple[str, ...] = ("PASS", "PASS"),
) -> Path:
    path = tmp_path / "review.json"
    path.write_text(
        json.dumps(
            {
                "criteria": [
                    {
                        "criterion": criterion,
                        "proof_method": f"pytest -k criterion_{index}",
                        "exact_result": "1 passed",
                        "status": status,
                    }
                    for index, (criterion, status) in enumerate(
                        zip(CRITERIA, statuses, strict=True), start=1
                    )
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_all_passing_evidence_produces_accept(tmp_path: Path) -> None:
    decision = review_criteria(
        tmp_path,
        CRITERIA,
        evidence_path=write_evidence(tmp_path),
    )

    assert decision.recommendation is ReviewRecommendation.ACCEPT
    assert tuple(item.status for item in decision.criteria) == (
        CriterionStatus.PASS,
        CriterionStatus.PASS,
    )


def test_any_failure_produces_rework(tmp_path: Path) -> None:
    decision = review_criteria(
        tmp_path,
        CRITERIA,
        evidence_path=write_evidence(tmp_path, statuses=("PASS", "FAIL")),
    )

    assert decision.recommendation is ReviewRecommendation.REWORK


@pytest.mark.parametrize(
    "criteria",
    (
        ("First criterion",),
        ("First criterion", "First criterion"),
        ("First criterion", "Unknown criterion"),
    ),
)
def test_evidence_must_match_every_criterion_exactly_once(
    tmp_path: Path,
    criteria: tuple[str, ...],
) -> None:
    with pytest.raises(RuntimeError, match="match every acceptance criterion"):
        review_criteria(
            tmp_path,
            criteria,
            evidence_path=write_evidence(tmp_path),
        )


def test_blank_or_malformed_evidence_is_rejected(tmp_path: Path) -> None:
    path = write_evidence(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["criteria"][0]["proof_method"] = " "
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="cannot be blank"):
        review_criteria(tmp_path, CRITERIA, evidence_path=path)

    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="malformed"):
        review_criteria(tmp_path, CRITERIA, evidence_path=path)

    path.write_text(
        json.dumps(
            {
                "criteria": [
                    {
                        "criterion": "First criterion",
                        "proof_method": 123,
                        "exact_result": "1 passed",
                        "status": "PASS",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="malformed"):
        review_criteria(tmp_path, CRITERIA, evidence_path=path)


def test_review_requires_configuration_and_acceptance_criteria(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENGINEERING_REVIEW_EVIDENCE_PATH", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        review_criteria(tmp_path, CRITERIA)
    with pytest.raises(RuntimeError, match="without acceptance criteria"):
        review_criteria(tmp_path, (), evidence_path=write_evidence(tmp_path))


def test_evidence_must_be_bounded_and_inside_repository(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_text("x" * (MAX_EVIDENCE_BYTES + 1), encoding="utf-8")
    with pytest.raises(RuntimeError, match="65,536-byte limit"):
        review_criteria(tmp_path, CRITERIA, evidence_path=oversized)

    with pytest.raises(RuntimeError, match="inside the repository"):
        review_criteria(
            tmp_path,
            CRITERIA,
            evidence_path=tmp_path.parent / "outside.json",
        )
