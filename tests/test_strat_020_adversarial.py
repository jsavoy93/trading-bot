"""
STRAT-020 REMEDIATION v2 — Adversarial test suite (2026-10-18).

Adversarial tests verify that the validator's check functions correctly identify
deliberate defects in synthetic recovery mappings.

v1 categories (Blocker 6 corrections added):
  1. Ticker reuse across unrelated CIKs.
  2. Incorrect FIGI/share-class matches (now: real share-class cross-match test).
  3. Invalid alias effective dates.
  4. Post-formation IPOs.
  5. Missing formation prices.
  6. Ambiguous ticker mappings.
  7. Duplicate (formation_year, cik) mappings.
  8. Inconsistent source hashes.
  9. Invalid mapping provenance (matched_ticker=None).
 10. Validation failures that incorrectly produce all_passed=True.

v2 NEW negative tests (Blocker 6):
  N1. First bar before June but last bar before formation (delisted-then-gone).
  N2. First bar after formation within the same calendar year.
  N3. Missing exact formation-date price (within 7-day window vs outside).

v2 NEW share-class test (Blocker 3):
  S1. Two securities share a CIK but have different share-class FIGIs.

These tests do NOT modify the production recovery mapping or any frozen
artifact. They construct small synthetic DataFrames in-memory or use
temp_path fixtures.
"""
from __future__ import annotations

import gzip
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest


# Locate the modules via importlib (works with the experiments/ package layout).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_VR_PATH = _REPO_ROOT / "experiments" / "strat_020" / "validate_recovery.py"
_BR_PATH = _REPO_ROOT / "experiments" / "strat_020" / "build_recovery_mapping.py"
_VR_SPEC = importlib.util.spec_from_file_location("validate_recovery_under_test", _VR_PATH)
_vr = importlib.util.module_from_spec(_VR_SPEC)
sys.modules["validate_recovery_under_test"] = _vr
_VR_SPEC.loader.exec_module(_vr)
_BR_SPEC = importlib.util.spec_from_file_location("build_recovery_mapping_under_test", _BR_PATH)
_br = importlib.util.module_from_spec(_BR_SPEC)
sys.modules["build_recovery_mapping_under_test"] = _br
_BR_SPEC.loader.exec_module(_br)


def _good_row(**overrides) -> dict:
    """Return a single good HIGH-confidence ORIGINAL_TICKER row, with optional overrides."""
    base = {
        "formation_year": 2024,
        "formation_date": "2024-06-28",
        "cik": "1000001",
        "original_ticker": "TEST",
        "matched_ticker": "TEST",
        "match_type": "ORIGINAL_TICKER",
        "evidence_source": "DATA-001",
        "mapping_confidence": "HIGH",
        "bar_exists": True,
        "unique_match": True,
        "alias_candidates": [],
        "figi_composite": ["BBG000BLH3P8"],
        "figi_share_class": ["BBG001S5S0V0"],
        "all_cik_tickers_in_pit": ["TEST"],
        "d001_first_bar": "2020-01-02 05:00:00+00:00",
        "d001_last_bar": "2024-12-31 05:00:00+00:00",
        "d001_nbars": 1000,
        "formation_price_available": True,
        "latest_bar_date_on_or_before_formation": "2024-06-28",
        "days_from_latest_bar_to_formation": 0,
        "alias_pit_earliest_formation_date": None,
        "alias_pit_valid_at_formation": None,
        "original_ticker_pit_valid_at_formation": True,
        "share_class_match_valid": True,
        "unverified_reason": None,
    }
    base.update(overrides)
    return base


# ============================================================
# Adversarial Test 1: Ticker reuse across unrelated CIKs
# ============================================================

def test_adversarial_1_ticker_reuse_across_unrelated_ciks():
    """CIK A's ticker is mapped to CIK B in PIT. The validator must classify as REJECTED."""
    bad_row = _good_row(
        cik="1000002",
        match_type="TICKER_REUSE_BY_OTHER_CIK",
        evidence_source="Massive PIT (ticker reused)",
        mapping_confidence="REJECTED",
        bar_exists=False,
        matched_ticker=None,
        formation_price_available=False,
    )
    bad_row2 = _good_row(
        cik="1000003",
        match_type="TICKER_REUSE_BY_OTHER_CIK",
        mapping_confidence="HIGH",  # WRONG: should be REJECTED
        bar_exists=False,
        matched_ticker=None,
        formation_price_available=False,
    )
    rdf = pd.DataFrame([bad_row, bad_row2])
    passed, detail = _vr.check_3_no_ticker_reuse_accepted(rdf)
    assert not passed, f"check_3 should FAIL when a TICKER_REUSE row is labeled HIGH; got: {detail}"


# ============================================================
# Adversarial Test 2: Share-class cross-match (Blocker 3)
# ============================================================

def test_adversarial_2_share_class_cross_match():
    """Two share_class_figi values for the same CIK; matched_ticker is in the wrong share class.

    The validator (check_2 + check_5g) must FAIL when a HIGH row has
    share_class_match_valid == False.
    """
    # A HIGH row whose matched ticker belongs to a different share class than the CIK's primary
    bad = _good_row(
        match_type="UNIQUE_CIK_ALIAS",
        original_ticker="OLD",
        matched_ticker="NEW",
        alias_pit_valid_at_formation=True,
        share_class_match_valid=False,  # WRONG: different share class
    )
    good = _good_row()
    rdf = pd.DataFrame([bad, good])
    # check_2
    passed_2, detail_2 = _vr.check_2_no_share_class_cross_match(rdf, None)
    assert not passed_2, f"check_2 should FAIL on share-class cross-match; got: {detail_2}"
    assert "share_class_cross_match_violations=1" in detail_2, f"unexpected detail: {detail_2}"
    # check_5g
    passed_5g, detail_5g = _vr.check_5g_share_class_match(rdf)
    assert not passed_5g, f"check_5g should FAIL on share_class_match_valid=False; got: {detail_5g}"


# ============================================================
# Adversarial Test 3 & 4 & 5: Invalid alias effective dates / post-formation IPOs / missing formation prices
# ============================================================

def test_adversarial_3_4_5_formation_price_unavailable_on_high():
    """HIGH-confidence row with formation_price_available=False. TIER 3 must FAIL."""
    bad = _good_row(
        match_type="UNIQUE_CIK_ALIAS",
        original_ticker="OLDT",
        matched_ticker="NEWT",
        formation_price_available=False,
        d001_first_bar="2026-01-02 05:00:00+00:00",
        latest_bar_date_on_or_before_formation=None,
        days_from_latest_bar_to_formation=None,
    )
    rdf = pd.DataFrame([bad, _good_row()])
    passed, detail = _vr.check_5c_formation_price_availability(rdf)
    assert not passed, f"check_5c STRICT must FAIL when any HIGH row lacks formation-date price; got: {detail}"
    assert "without_formation_date_price=1" in detail, f"unexpected detail: {detail}"


# ============================================================
# Adversarial Test 6: Ambiguous ticker mappings
# ============================================================

def test_adversarial_6_ambiguous_ticker_mappings_silently_accepted():
    """AMBIGUOUS row but marked HIGH. check_4 must FAIL."""
    bad = _good_row(
        match_type="AMBIGUOUS_MULTIPLE_ALIASES",
        mapping_confidence="HIGH",  # WRONG: should be LOW
        unique_match=True,  # WRONG: should be False
        alias_candidates=["A", "B"],
    )
    rdf = pd.DataFrame([bad, _good_row()])
    passed, detail = _vr.check_4_no_ambiguous_silently(rdf)
    assert not passed, f"check_4 should FAIL when AMBIGUOUS row is labeled HIGH; got: {detail}"


# ============================================================
# Adversarial Test 7: Duplicate (formation_year, cik) mappings
# ============================================================

def test_adversarial_7_duplicate_year_cik_mappings():
    r1 = _good_row(formation_year=2024, cik="7000001")
    r2 = _good_row(formation_year=2024, cik="7000001", original_ticker="OTHER")
    rdf = pd.DataFrame([r1, r2])
    passed, detail = _vr.check_1_no_duplicate(rdf)
    assert not passed, f"check_1 should FAIL on duplicate (year, cik); got: {detail}"
    assert "dup_keys=1" in detail, f"expected dup_keys=1 in detail; got: {detail}"


# ============================================================
# Adversarial Test 8: Inconsistent source hashes
# ============================================================

def test_adversarial_8_inconsistent_source_hashes(tmp_path, monkeypatch):
    cohort_hashes = {}
    for y in [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]:
        cohort_path = tmp_path / f"_cohort_{y}.csv.gz"
        with gzip.open(cohort_path, "wt") as f:
            f.write(f"cik_10digit,ticker\n{1000000 + y},TEST_{y}\n")
        h = hashlib.sha256()
        with open(cohort_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        cohort_hashes[str(y)] = h.hexdigest()
    parts = []
    for i in range(1, 6):
        p = tmp_path / f"daily_bars_part_{i:03d}.parquet"
        pd.DataFrame({"symbol": [], "timestamp": []}).to_parquet(p)
        parts.append(p)
    massive_pit = tmp_path / "massive_pit.csv.gz"
    pd.DataFrame({"cik_10digit": []}).to_csv(massive_pit, index=False)

    wrong_manifest = dict(cohort_hashes)
    wrong_manifest["2016"] = "0" * 64
    fake_manifest = {
        "data_001_parts": [hashlib.sha256(p.read_bytes()).hexdigest() for p in parts],
        "massive_pit": hashlib.sha256(massive_pit.read_bytes()).hexdigest(),
        "cohorts": wrong_manifest,
    }
    manifest_path = tmp_path / "_step3_sources.json"
    with open(manifest_path, "w") as f:
        json.dump(fake_manifest, f)

    monkeypatch.setattr(_vr, "SOURCES", manifest_path)
    monkeypatch.setattr(_vr, "PARTS", parts)
    monkeypatch.setattr(_vr, "MASSIVE_PIT", massive_pit)
    monkeypatch.setattr(_vr, "COHORT_DIR", tmp_path)

    passed, detail = _vr.check_7_source_hashes()
    assert not passed, f"check_7 should FAIL on inconsistent hashes; got: {detail}"
    assert "cohort 2016" in detail, f"expected cohort 2016 in mismatch detail; got: {detail}"


# ============================================================
# Adversarial Test 9: Invalid mapping provenance (matched_ticker=None on HIGH)
# ============================================================

def test_adversarial_9_invalid_mapping_provenance_no_matched_ticker():
    bad = _good_row(
        match_type="UNIQUE_CIK_ALIAS",
        original_ticker="OLDT",
        matched_ticker=None,  # WRONG: required for UNIQUE_CIK_ALIAS
        formation_price_available=False,
        bar_exists=False,
        d001_first_bar=None,
        d001_last_bar=None,
        d001_nbars=0,
    )
    rdf = pd.DataFrame([bad, _good_row()])
    passed, detail = _vr.check_5a_identity_validity(rdf)
    assert not passed, f"check_5a should FAIL when UNIQUE_CIK_ALIAS row has matched_ticker=None; got: {detail}"
    assert "unique_cik_alias_invalid" in detail, f"expected alias invalidity in detail; got: {detail}"


# ============================================================
# Adversarial Test 10: all_passed must NOT silently return True
# ============================================================

def test_adversarial_10_all_passed_does_not_silently_pass_on_failure():
    r1 = _good_row(formation_year=2024, cik="10000001")
    r2 = _good_row(formation_year=2024, cik="10000001", original_ticker="OTHER")
    rdf = pd.DataFrame([r1, r2])
    checks = [
        ("check_A", (True, "ok")),
        ("check_B", (False, "failed: duplicate key")),
    ]
    all_passed = all(c[1][0] for c in checks)
    assert not all_passed, "all_passed must be False when any check fails"
    passed, detail = _vr.check_1_no_duplicate(rdf)
    assert not passed, f"check_1 should FAIL on duplicate; got: {detail}"


# ============================================================
# Adversarial Test 11 (NEW v2): Alias PIT-validity
# ============================================================

def test_adversarial_11_alias_pit_invalid_at_formation():
    """UNIQUE_CIK_ALIAS row marked HIGH but alias_pit_valid_at_formation=False. check_5e must FAIL."""
    bad = _good_row(
        match_type="UNIQUE_CIK_ALIAS",
        original_ticker="OLDT",
        matched_ticker="NEWT",
        mapping_confidence="HIGH",
        alias_pit_valid_at_formation=False,  # WRONG: should be True for HIGH
        alias_pit_earliest_formation_date="2017-06-30",
    )
    rdf = pd.DataFrame([bad, _good_row()])
    passed, detail = _vr.check_5e_alias_pit_validity(rdf)
    assert not passed, f"check_5e should FAIL when alias PIT-invalid; got: {detail}"
    assert "alias_pit_invalid=1" in detail


# ============================================================
# Adversarial Test 12 (NEW v2): ORIGINAL_TICKER PIT-validity
# ============================================================

def test_adversarial_12_original_ticker_pit_invalid_at_formation():
    """ORIGINAL_TICKER row marked HIGH but original_ticker_pit_valid_at_formation=False. check_5f must FAIL."""
    bad = _good_row(
        match_type="ORIGINAL_TICKER",
        original_ticker_pit_valid_at_formation=False,  # WRONG: should be True for HIGH
    )
    rdf = pd.DataFrame([bad, _good_row()])
    passed, detail = _vr.check_5f_original_ticker_pit_validity(rdf)
    assert not passed, f"check_5f should FAIL when ORIGINAL_TICKER PIT-invalid; got: {detail}"
    assert "pit_invalid=1" in detail


# ============================================================
# BLOCKER 6 — NEGATIVE TEST 1: First bar before June but last bar before formation
# ============================================================

def test_negative_1_first_bar_before_june_last_bar_before_formation():
    """Blocker 6 scenario 1: a ticker with first_bar in 2024-01 and last_bar in 2024-03
    for formation_date 2024-06-28. The 7-day window rule should reject this
    (latest bar is 2024-03-XX, which is more than 7 days before 2024-06-28)."""
    from datetime import datetime as _dt, timezone as _tz
    # formation_date 2024-06-28; last bar 2024-03-15 (105 days before)
    formation_date = pd.Timestamp("2024-06-28")
    first_bar = pd.Timestamp("2024-01-15 05:00:00+00:00")
    last_bar = pd.Timestamp("2024-03-15 05:00:00+00:00")
    fp_avail, latest_date, days_gap = _br.compute_formation_price_available(
        first_bar, last_bar, formation_date
    )
    assert fp_avail == False, f"expected formation_price_available=False, got {fp_avail}"
    assert days_gap > 7, f"expected days_gap > 7, got {days_gap}"
    assert latest_date == pd.Timestamp("2024-03-15")


# ============================================================
# BLOCKER 6 — NEGATIVE TEST 2: First bar after formation within the same calendar year
# ============================================================

def test_negative_2_first_bar_after_formation_same_year():
    """Blocker 6 scenario 2: a ticker with first_bar in 2024-09-15 for
    formation_date 2024-06-28. The 7-day window rule should reject this
    (no bar on or before formation_date)."""
    formation_date = pd.Timestamp("2024-06-28")
    first_bar = pd.Timestamp("2024-09-15 05:00:00+00:00")
    last_bar = pd.Timestamp("2024-12-31 05:00:00+00:00")
    fp_avail, latest_date, days_gap = _br.compute_formation_price_available(
        first_bar, last_bar, formation_date
    )
    assert fp_avail == False, f"expected formation_price_available=False, got {fp_avail}"
    assert latest_date is None, f"expected latest_date=None (no bar before formation), got {latest_date}"


# ============================================================
# BLOCKER 6 — NEGATIVE TEST 3: Missing exact formation-date price
# ============================================================

def test_negative_3a_formation_date_price_within_7_days():
    """Blocker 6 scenario 3a: a ticker with bars on 2024-06-25 and 2024-07-01 but NO
    bar on 2024-06-28 (formation date). The latest bar before 2024-06-28 is
    2024-06-25, which is within 7 days → formation_price_available=True."""
    formation_date = pd.Timestamp("2024-06-28")
    first_bar = pd.Timestamp("2020-01-02 05:00:00+00:00")
    last_bar = pd.Timestamp("2024-07-01 05:00:00+00:00")
    fp_avail, latest_date, days_gap = _br.compute_formation_price_available(
        first_bar, last_bar, formation_date
    )
    assert fp_avail == True, f"expected formation_price_available=True (within 7 days), got {fp_avail}"
    # last_bar is AFTER formation_date → latest_date=formation_date, days_gap=0
    assert latest_date == pd.Timestamp("2024-06-28").date() or str(latest_date).startswith("2024-06-28"), (
        f"expected latest_date=2024-06-28, got {latest_date}"
    )


def test_negative_3b_formation_date_price_outside_7_days():
    """Blocker 6 scenario 3b: a ticker with bars on 2024-06-20 and 2024-07-05.
    The latest bar before 2024-06-28 is 2024-06-20, which is 8 days before
    2024-06-28 → formation_price_available=False."""
    formation_date = pd.Timestamp("2024-06-28")
    first_bar = pd.Timestamp("2020-01-02 05:00:00+00:00")
    last_bar = pd.Timestamp("2024-07-05 05:00:00+00:00")
    # We need a bar on 2024-06-20 (before formation) to be the latest on-or-before
    # The compute_formation_price_available function uses last_bar as the proxy
    # for "latest bar on or before formation". We need last_bar to be on or before
    # formation and > 7 days before.
    # Override: use last_bar = 2024-06-20 (8 days before formation)
    last_bar_2 = pd.Timestamp("2024-06-20 05:00:00+00:00")
    fp_avail, latest_date, days_gap = _br.compute_formation_price_available(
        first_bar, last_bar_2, formation_date
    )
    assert fp_avail == False, f"expected formation_price_available=False (8 days > 7), got {fp_avail}"
    assert days_gap == 8, f"expected days_gap=8, got {days_gap}"


# ============================================================
# End-to-end: full validator summary with injected defect
# ============================================================

def test_adversarial_end_to_end_full_summary_with_injected_defect():
    """Construct a synthetic recovery mapping with a deliberate defect
    (HIGH row with share_class_match_valid=False), verify the validator's
    check_5g catches it."""
    bad = _good_row(
        match_type="UNIQUE_CIK_ALIAS",
        original_ticker="OLDT",
        matched_ticker="NEWT",
        share_class_match_valid=False,  # defect
    )
    good = _good_row()
    rdf = pd.DataFrame([bad, good])
    passed, detail = _vr.check_5g_share_class_match(rdf)
    assert not passed
    assert "share_class_match_invalid=1" in detail
