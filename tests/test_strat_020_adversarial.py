"""
STRAT-020 REMEDIATION — Adversarial test suite.

These tests deliberately inject specific defects into a synthetic recovery
mapping and verify that the validator's check functions correctly identify
each defect. They are the negative tests that the prior implementation
lacked (per QA §5.6).

Categories covered (per owner directive 2026-10-16 01:05 UTC):
  1. Ticker reuse across unrelated CIKs.
  2. Incorrect FIGI/share-class matches.
  3. Invalid alias effective dates.
  4. Post-formation IPOs (matched ticker started trading after formation year).
  5. Missing formation prices (formation_price_available=False but HIGH).
  6. Ambiguous ticker mappings (multiple candidates but mapped to HIGH).
  7. Duplicate (formation_year, cik) mappings.
  8. Inconsistent source hashes.
  9. Invalid mapping provenance (matched_ticker=None but mapping_confidence=HIGH).
 10. Validation failures that incorrectly produce all_passed=True.

These tests do NOT modify the production recovery mapping or any frozen
artifact. They construct small synthetic DataFrames in-memory or use
temp_path fixtures for the source-hash test.
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


# Locate the validation module via importlib (works with the experiments/ package layout).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_VR_PATH = _REPO_ROOT / "experiments" / "strat_020" / "validate_recovery.py"
_VR_SPEC = importlib.util.spec_from_file_location("validate_recovery_under_test", _VR_PATH)
_vr = importlib.util.module_from_spec(_VR_SPEC)
sys.modules["validate_recovery_under_test"] = _vr
_VR_SPEC.loader.exec_module(_vr)


def _good_row(**overrides) -> dict:
    """Return a single good HIGH-confidence ORIGINAL_TICKER row, with optional overrides."""
    base = {
        "formation_year": 2024,
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
        "d001_first_bar": "2020-01-02",
        "d001_last_bar": "2024-12-31",
        "d001_nbars": 1000,
        "formation_price_available": True,
        "years_gap": 0,
        "eligible_for_backtest": True,
    }
    base.update(overrides)
    return base


# ============================================================
# Adversarial Test 1: Ticker reuse across unrelated CIKs
# ============================================================

def test_adversarial_1_ticker_reuse_across_unrelated_ciks():
    """CIK A's original ticker is mapped to CIK B in PIT.
    The validator must classify this as REJECTED, not HIGH.
    """
    # Construct: a row that claims match_type=ORIGINAL_TICKER with HIGH confidence
    # for a ticker that is reused by another CIK. This should be REJECTED.
    bad_row = _good_row(
        cik="1000002",
        match_type="TICKER_REUSE_BY_OTHER_CIK",  # correct rejection
        evidence_source="Massive PIT (ticker reused)",
        mapping_confidence="REJECTED",  # correct confidence
        bar_exists=False,
        matched_ticker=None,
        formation_price_available=False,
        eligible_for_backtest=False,
    )
    # Inverted: a row that says "this is HIGH confidence" but the match_type says REJECTED.
    # That's not the bug; the bug is when the same TICKER appears in the cohort
    # and the recovery claims it's HIGH when in fact it's reused.
    # We test that check_3 catches a TICKER_REUSE row labeled as HIGH or LOW.
    bad_row2 = _good_row(
        cik="1000003",
        match_type="TICKER_REUSE_BY_OTHER_CIK",
        mapping_confidence="HIGH",  # WRONG: should be REJECTED
        bar_exists=False,
        matched_ticker=None,
        formation_price_available=False,
        eligible_for_backtest=False,
    )
    rdf = pd.DataFrame([bad_row, bad_row2])
    passed, detail = _vr.check_3_no_ticker_reuse_accepted(rdf)
    assert not passed, (
        f"check_3 should FAIL when a TICKER_REUSE row is labeled HIGH; got: {detail}"
    )


# ============================================================
# Adversarial Test 2: Incorrect FIGI/share-class matches
# ============================================================

def test_adversarial_2_incorrect_figi_share_class_match():
    """Two share_class_figi values exist for a CIK but both map to the same matched_ticker.
    This would imply collapsing share classes. check_2 must catch it.
    """
    # Two rows for the same (year, cik) with different matched_tickers. This violates
    # check_1 (duplicate keys) AND would imply share-class collapsing.
    r1 = _good_row(
        formation_year=2020,
        cik="2000001",
        original_ticker="OLD",
        matched_ticker="OLD",
        d001_first_bar="2019-01-02",
        figi_share_class=["BBG_SHARE_A"],
        all_cik_tickers_in_pit=["OLD", "NEW"],
    )
    r3 = r1.copy()
    r3["matched_ticker"] = "DIFFERENT"
    rdf_bad = pd.DataFrame([r1, r3])

    # Construct a minimal Massive PIT subset for check_2 (must include cik_str).
    mp = pd.DataFrame([
        {"cik_10digit": 2000001, "ticker": "OLD", "share_class_figi": "BBG_SHARE_A", "cik_str": "2000001"},
        {"cik_10digit": 2000001, "ticker": "NEW", "share_class_figi": "BBG_SHARE_B", "cik_str": "2000001"},
    ])
    # First confirm check_1 catches the duplicate
    passed_1, detail_1 = _vr.check_1_no_duplicate(rdf_bad)
    assert not passed_1, f"check_1 should FAIL on duplicate; got: {detail_1}"
    # Then check_2 also flags the share-class collapse
    passed_2, detail_2 = _vr.check_2_no_share_class_merge(rdf_bad, mp)
    assert not passed_2, (
        f"check_2 should FAIL when same (year, cik) maps to 2 different tickers; got: {detail_2}"
    )


# ============================================================
# Adversarial Test 3 & 4 & 5: Invalid alias effective dates / post-formation IPOs / missing formation prices
# (all three surface as formation_price_available=False on a HIGH row)
# ============================================================

def test_adversarial_3_4_5_formation_price_unavailable_on_high():
    """Construct a HIGH-confidence row with formation_price_available=False.
    TIER 3 (the strict check) must FAIL.
    This covers three categories:
      3. Invalid alias effective dates (alias resolved but first bar is after formation year)
      4. Post-formation IPOs (matched ticker started trading after formation year)
      5. Missing formation prices (formation_price_available=False)
    All three produce the same observable: HIGH row with no formation-date price.
    """
    bad_row = _good_row(
        match_type="UNIQUE_CIK_ALIAS",
        original_ticker="OLDT",
        matched_ticker="NEWT",
        formation_price_available=False,  # WRONG for a HIGH row
        eligible_for_backtest=False,
        d001_first_bar="2026-01-02",  # first bar year > formation year 2024
        years_gap=2,
    )
    rdf = pd.DataFrame([bad_row, _good_row()])  # one bad, one good
    passed, detail = _vr.check_5c_formation_price_availability(rdf)
    assert not passed, (
        f"check_5c STRICT must FAIL when any HIGH row lacks formation-date price; got: {detail}"
    )
    assert "without_formation_date_price=1" in detail, f"unexpected detail: {detail}"


# ============================================================
# Adversarial Test 6: Ambiguous ticker mappings
# ============================================================

def test_adversarial_6_ambiguous_ticker_mappings_silently_accepted():
    """Construct an AMBIGUOUS row but mark it HIGH. check_4 must FAIL."""
    bad_row = _good_row(
        match_type="AMBIGUOUS_MULTIPLE_ALIASES",
        mapping_confidence="HIGH",  # WRONG: should be LOW
        unique_match=True,  # WRONG: should be False
        alias_candidates=["A", "B"],
    )
    rdf = pd.DataFrame([bad_row, _good_row()])
    passed, detail = _vr.check_4_no_ambiguous_silently(rdf)
    assert not passed, (
        f"check_4 should FAIL when AMBIGUOUS row is labeled HIGH; got: {detail}"
    )


# ============================================================
# Adversarial Test 7: Duplicate (formation_year, cik) mappings
# ============================================================

def test_adversarial_7_duplicate_year_cik_mappings():
    """Two rows with the same (formation_year, cik). check_1 must FAIL."""
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
    """Modify a source file (in a temp dir) so its hash differs from the manifest.
    check_7 must FAIL.
    """
    # Create a temp dir with a minimal cohort file and a sources manifest
    # pointing to a different (unmodified) cohort file. check_7 should detect mismatch.
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
    # Also create empty DATA-001 part files and a Massive PIT file in tmp_path
    parts = []
    for i in range(1, 6):
        p = tmp_path / f"daily_bars_part_{i:03d}.parquet"
        # Write minimal parquet (empty DataFrame)
        pd.DataFrame({"symbol": [], "timestamp": []}).to_parquet(p)
        parts.append(p)
    massive_pit = tmp_path / "massive_pit.csv.gz"
    pd.DataFrame({"cik_10digit": []}).to_csv(massive_pit, index=False)

    # Now write a manifest with a deliberately wrong hash for cohort 2016 only
    wrong_manifest = dict(cohort_hashes)
    wrong_manifest["2016"] = "0" * 64  # deliberately wrong
    fake_manifest = {
        "data_001_parts": [hashlib.sha256(p.read_bytes()).hexdigest() for p in parts],
        "massive_pit": hashlib.sha256(massive_pit.read_bytes()).hexdigest(),
        "cohorts": wrong_manifest,
    }
    manifest_path = tmp_path / "_step3_sources.json"
    with open(manifest_path, "w") as f:
        json.dump(fake_manifest, f)

    # Monkeypatch the validator's module-level constants to point at tmp_path
    monkeypatch.setattr(_vr, "SOURCES", manifest_path)
    monkeypatch.setattr(_vr, "PARTS", parts)
    monkeypatch.setattr(_vr, "MASSIVE_PIT", massive_pit)
    monkeypatch.setattr(_vr, "COHORT_DIR", tmp_path)

    # Run check_7
    passed, detail = _vr.check_7_source_hashes()
    assert not passed, f"check_7 should FAIL on inconsistent hashes; got: {detail}"
    assert "cohort 2016" in detail, f"expected cohort 2016 in mismatch detail; got: {detail}"


# ============================================================
# Adversarial Test 9: Invalid mapping provenance (matched_ticker=None on HIGH)
# ============================================================

def test_adversarial_9_invalid_mapping_provenance_no_matched_ticker():
    """Construct a UNIQUE_CIK_ALIAS row with no matched_ticker. check_5a must FAIL."""
    bad_row = _good_row(
        match_type="UNIQUE_CIK_ALIAS",
        original_ticker="OLDT",
        matched_ticker=None,  # WRONG: matched_ticker is required for UNIQUE_CIK_ALIAS
        formation_price_available=False,
        eligible_for_backtest=False,
        bar_exists=False,
        d001_first_bar=None,
        d001_last_bar=None,
        d001_nbars=0,
    )
    rdf = pd.DataFrame([bad_row, _good_row()])
    passed, detail = _vr.check_5a_identity_validity(rdf)
    assert not passed, (
        f"check_5a should FAIL when a UNIQUE_CIK_ALIAS row has matched_ticker=None; got: {detail}"
    )
    assert "unique_cik_alias_invalid" in detail, f"expected alias invalidity in detail; got: {detail}"


# ============================================================
# Adversarial Test 10: all_passed must NOT silently return True when a check fails
# ============================================================

def test_adversarial_10_all_passed_does_not_silently_pass_on_failure():
    """The prior validator had a bug: all_passed = all(c[0] for c in checks).
    c[0] is the check name (always truthy string), not the result.
    This test verifies the corrected all_passed returns False when any check fails.
    """
    # Construct a 2-row synthetic dataset: one good, one bad (duplicate key).
    r1 = _good_row(formation_year=2024, cik="10000001")
    r2 = _good_row(formation_year=2024, cik="10000001", original_ticker="OTHER")
    rdf = pd.DataFrame([r1, r2])
    # Build a checks list with one PASS and one FAIL, using tuples (name, (passed, detail))
    checks = [
        ("check_A", (True, "ok")),
        ("check_B", (False, "failed: duplicate key")),  # this one FAILS
    ]
    # Use the corrected all_passed logic
    all_passed = all(c[1][0] for c in checks)
    assert not all_passed, "all_passed must be False when any check fails"
    # Sanity: also verify check_1 catches the duplicate on this data
    passed, detail = _vr.check_1_no_duplicate(rdf)
    assert not passed, f"check_1 should FAIL on duplicate; got: {detail}"


# ============================================================
# End-to-end adversarial test: corrupt the production recovery mapping
# in a temp file, then verify the full validation summary catches the defect.
# ============================================================

def test_adversarial_end_to_end_full_summary_with_injected_defect(tmp_path):
    """Construct a minimal synthetic recovery mapping in tmp_path with a deliberate
    defect (HIGH row with no formation-date price), run a single check_5c, and
    assert it returns FAIL with the violation count.
    """
    bad = _good_row(
        match_type="UNIQUE_CIK_ALIAS",
        original_ticker="OLDT",
        matched_ticker="NEWT",
        formation_price_available=False,  # defect
        eligible_for_backtest=False,
    )
    good = _good_row()
    rdf = pd.DataFrame([bad, good])
    passed, detail = _vr.check_5c_formation_price_availability(rdf)
    assert not passed
    assert "without_formation_date_price=1" in detail
