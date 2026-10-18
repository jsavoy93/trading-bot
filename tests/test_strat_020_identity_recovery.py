"""
Tests for STRAT-020 historical identity recovery (REMEDIATION v2, 2026-10-18).

These tests verify that the recovery mapping produced by
experiments/strat_020/build_recovery_mapping.py is correct and deterministic,
and that the validation logic in experiments/strat_020/validate_recovery.py
returns the expected results.

They DO NOT touch frozen artifacts, run return calculations, perform backtests,
or invoke any external API. They are pure invariants of the derived dataset
and the source-of-truth hashes recorded in _step3_sources.json.

v2 changes from the v1 PR #108 baseline:
  - HIGH total dropped from 15,087 to 12,909
  - TICKER_REUSE rose from 134 to 704 (Blocker 3: ORIGINAL_TICKER ticker-reuse check)
  - UNVERIFIED is a new confidence level (1,608 rows; Blocker 2: PIT effective-date)
  - 7-day window rule replaces first_year<=year (Blocker 1)
  - formation_price_eligible replaces eligible_for_backtest (Blocker 5)
  - Per-year coverage CSV has distinct columns (Blocker 5)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
RECOVERY_PARQUET = REPO_ROOT / "experiments/strat_020/_step3_recovery_mapping.parquet"
SOURCES = REPO_ROOT / "experiments/strat_020/_step3_sources.json"
VALIDATION = REPO_ROOT / "experiments/strat_020/_step5_validation.json"
COVERAGE = REPO_ROOT / "experiments/strat_020/_step5_per_year_coverage.csv"
UNRESOLVABLE = REPO_ROOT / "experiments/strat_020/_step5_unresolvable_securities.csv"


@pytest.fixture(scope="module")
def rdf() -> pd.DataFrame:
    if not RECOVERY_PARQUET.exists():
        pytest.skip("Recovery mapping not yet generated; run build_recovery_mapping.py first")
    return pd.read_parquet(RECOVERY_PARQUET)


def test_recovery_mapping_exists():
    assert RECOVERY_PARQUET.exists(), "Recovery mapping parquet missing"


def test_recovery_mapping_size(rdf):
    assert len(rdf) == 19277, f"expected 19277 rows, got {len(rdf)}"


def test_per_year_cohort_sizes(rdf):
    expected = {2016: 1921, 2017: 1868, 2018: 1921, 2019: 1895, 2020: 1856,
                2021: 1883, 2022: 2055, 2023: 2032, 2024: 1948, 2025: 1898}
    actual = rdf.groupby("formation_year").size().to_dict()
    assert actual == expected, f"cohort sizes mismatch: {actual}"


def test_match_type_taxonomy(rdf):
    """v2: ORIGINAL_TICKER dropped from 13,985 to 13,415 (570 downclassified to TICKER_REUSE).
    TICKER_REUSE rose from 134 to 704. UNIQUE_CIK_ALIAS stays at 1,102 (but only 20 are HIGH)."""
    counts = rdf["match_type"].value_counts()
    assert set(counts.index) == {
        "ORIGINAL_TICKER",
        "UNIQUE_CIK_ALIAS",
        "AMBIGUOUS_MULTIPLE_ALIASES",
        "TICKER_REUSE_BY_OTHER_CIK",
        "NOT_IN_DATA001",
    }, f"unexpected match types: {counts.index.tolist()}"
    assert counts["ORIGINAL_TICKER"] == 13415, f"expected 13415 ORIGINAL_TICKER, got {counts['ORIGINAL_TICKER']}"
    assert counts["UNIQUE_CIK_ALIAS"] == 1102, f"expected 1102 UNIQUE_CIK_ALIAS, got {counts['UNIQUE_CIK_ALIAS']}"
    assert counts["AMBIGUOUS_MULTIPLE_ALIASES"] == 21
    assert counts["TICKER_REUSE_BY_OTHER_CIK"] == 704, f"expected 704 TICKER_REUSE, got {counts['TICKER_REUSE_BY_OTHER_CIK']}"
    assert counts["NOT_IN_DATA001"] == 4035


def test_confidence_taxonomy(rdf):
    """v2: UNVERIFIED is a new confidence level."""
    counts = rdf["mapping_confidence"].value_counts()
    expected_confidences = {"HIGH", "LOW", "REJECTED", "UNVERIFIED", "NO_MATCH"}
    assert set(counts.index) == expected_confidences, f"unexpected confidences: {set(counts.index)}"
    # Verify specific counts
    assert counts["HIGH"] == 12909
    assert counts["UNVERIFIED"] == 1608
    assert counts["LOW"] == 21
    assert counts["REJECTED"] == 704
    assert counts["NO_MATCH"] == 4035


def test_no_duplicate_keys(rdf):
    n = rdf.groupby(["formation_year", "cik"]).size()
    assert (n == 1).all(), f"duplicate keys: {n[n > 1]}"


def test_high_confidence_only_with_bars(rdf):
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    assert (high["bar_exists"] == True).all()
    assert (high["d001_nbars"] > 0).all()


def test_high_with_formation_price(rdf):
    """v2: 7-day window rule — HIGH rows with formation_price_available=False are the
    documented LIMITATION (post-formation IPOs that started trading after formation_date).
    This test asserts the LIMITATION count is exactly 79."""
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    n_no_fp = int((~high["formation_price_available"]).sum())
    assert n_no_fp == 79, f"expected 79 HIGH rows without formation-date price (LIMITATION), got {n_no_fp}"


def test_rejected_has_no_bars(rdf):
    rej = rdf[rdf["mapping_confidence"] == "REJECTED"]
    assert (rej["bar_exists"] == False).all()


def test_ambiguous_is_low_confidence(rdf):
    amb = rdf[rdf["match_type"] == "AMBIGUOUS_MULTIPLE_ALIASES"]
    assert (amb["mapping_confidence"] == "LOW").all()
    assert (amb["unique_match"] == False).all()
    assert (amb["alias_candidates"].apply(len) > 1).all()


def test_unique_alias_high_is_pit_valid(rdf):
    """v2: UNIQUE_CIK_ALIAS HIGH rows must be PIT-valid at or before formation_date."""
    uq_high = rdf[(rdf["match_type"] == "UNIQUE_CIK_ALIAS") & (rdf["mapping_confidence"] == "HIGH")]
    assert (uq_high["alias_pit_valid_at_formation"] == True).all()
    # matched_ticker must be different from original
    assert (uq_high["matched_ticker"] != uq_high["original_ticker"]).all()


def test_unverified_exists_with_specific_reasons(rdf):
    """v2: UNVERIFIED rows exist and have unverified_reason populated."""
    unv = rdf[rdf["mapping_confidence"] == "UNVERIFIED"]
    assert len(unv) == 1608
    # Each UNVERIFIED row has a reason
    assert unv["unverified_reason"].notna().all()
    # Reasons are from a known set
    known_reasons = {"ORIGINAL_TICKER_PIT_GAP", "ALIAS_PIT_ONLY_AFTER_FORMATION"}
    actual_reasons = set(unv["unverified_reason"].unique())
    assert actual_reasons.issubset(known_reasons), f"unexpected unverified reasons: {actual_reasons - known_reasons}"


def test_source_hashes_recorded():
    assert SOURCES.exists()
    with open(SOURCES) as f:
        s = json.load(f)
    for key in ("data_001_parts", "massive_pit", "cohorts", "output_parquet", "output_json"):
        assert key in s, f"missing key in sources manifest: {key}"
    assert len(s["data_001_parts"]) == 5
    assert len(s["cohorts"]) == 10


def test_per_year_coverage_csv():
    assert COVERAGE.exists()
    df = pd.read_csv(COVERAGE)
    # v2: per-year CSV has distinct columns (Blocker 5)
    required_columns = {
        "formation_year", "n_cohort",
        "in_data001_original", "recovered_unique_alias",
        "ambiguous_low_confidence", "rejected_ticker_reuse", "not_in_data001",
        "unverified_total",
        "high_confidence_total",
        "raw_bar_available", "valid_formation_date_price",
        "high_with_formation_price",
        "rejected_or_ambiguous_with_formation_price",
        "low_confidence_with_formation_price",
    }
    assert required_columns.issubset(set(df.columns)), (
        f"missing columns: {required_columns - set(df.columns)}"
    )
    # Per-year high_with_formation_price must sum to the headline (formation_price_valid)
    sum_high = int(df["high_with_formation_price"].sum())
    assert sum_high == 12830, f"per-year sum {sum_high} != headline 12830"


def test_unresolvable_listing_csv():
    assert UNRESOLVABLE.exists()
    df = pd.read_csv(UNRESOLVABLE)
    assert len(df) == 4035
    assert set(df["formation_year"].unique()) == set(range(2016, 2026))


def test_validation_summary_passes():
    """v2: critical_passed=True, limitations_present=True (79 HIGH rows lack formation-date price — LIMITATION).
    Exit code 0 (CRITICAL passes; LIMITATION is acceptable as documented)."""
    assert VALIDATION.exists()
    with open(VALIDATION) as f:
        v = json.load(f)
    # v2: explicit critical_passed and limitations_present
    assert v.get("critical_passed") == True, f"critical_passed={v.get('critical_passed')}"
    assert v.get("limitations_present") == True, f"limitations_present={v.get('limitations_present')}"
    # The LIMITATION must be exactly the 79 HIGH rows without formation-date price
    # (securities that started trading after formation_date; documented post-formation IPOs).
    assert v.get("all_passed") == False, "all_passed must be False when a LIMITATION is present"
    # Headline counts (v2; post-fix: 79 HIGH rows lack formation-date price — LIMITATION)
    assert v["identity_recovery_total"] == 12909
    assert v["formation_price_valid_total"] == 12830
    assert v["high_without_formation_price"] == 79
    assert v["newly_recovered_total"] == 1102
    assert v["newly_recovered_formation_price_valid"] == 15
    assert v["newly_recovered_without_formation_price"] == 1087
    assert v["unverified_total"] == 1608
    assert v["rejected_total"] == 704
    assert v["ambiguous_total"] == 21
    assert v["not_in_data001_total"] == 4035
    # All required CRITICAL checks present
    for required in [
        "1. No duplicate (formation_year, cik) mappings",
        "2. No share-class cross-match",
        "3. No ticker-reuse silently accepted",
        "4. No ambiguous mappings silently accepted",
        "5a. TIER 1: identity validity (HIGH rows have valid identity fields)",
        "5b. TIER 2: bar availability (HIGH rows have bar_exists=True)",
        "5c. TIER 3: formation-date price availability (STRICT; 7-day window)",
        "5d. TIER 4: eligibility consistency (no AMBIGUOUS in formation_price_eligible)",
        "5e. TIER 5: alias PIT-validity at formation (UNIQUE_CIK_ALIAS HIGH)",
        "5f. TIER 6: ORIGINAL_TICKER PIT-validity at formation (HIGH)",
        "5g. TIER 7: share-class match validity (HIGH rows)",
        "6. Frozen membership unchanged",
        "7. Source hashes unchanged",
        "8. Recovered matches link to existing bars",
    ]:
        assert required in v["checks"], f"missing check: {required}"
        # CRITICAL checks must all be PASS
        if v["checks"][required]["severity"] == "CRITICAL":
            assert v["checks"][required]["status"] == "PASS", (
                f"CRITICAL check {required} is {v['checks'][required]['status']}"
            )


def test_per_year_sum_equals_headline():
    """Blocker 5: year-by-year accepted counts must sum exactly to the headline."""
    cov = pd.read_csv(COVERAGE)
    with open(VALIDATION) as f:
        v = json.load(f)
    sum_per_year = int(cov["high_with_formation_price"].sum())
    headline = v["formation_price_valid_total"]
    assert sum_per_year == headline, f"per-year sum {sum_per_year} != headline {headline}"


def test_no_modification_to_frozen_cohorts():
    if not SOURCES.exists():
        pytest.skip("sources manifest not generated")
    import hashlib
    with open(SOURCES) as f:
        s = json.load(f)
    for year_str, expected_hash in s["cohorts"].items():
        path = REPO_ROOT / f"experiments/strat_017/_cohort_{year_str}.csv.gz"
        if not path.exists():
            pytest.skip(f"cohort {year_str} missing")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        actual = h.hexdigest()
        assert actual == expected_hash, f"cohort {year_str} hash changed"


def test_no_modification_to_data_001():
    if not SOURCES.exists():
        pytest.skip("sources manifest not generated")
    import hashlib
    with open(SOURCES) as f:
        s = json.load(f)
    for i, expected_hash in enumerate(s["data_001_parts"], start=1):
        path = REPO_ROOT / f"research_data/alpaca_daily_2016_2026/transfer/daily_bars_part_{i:03d}.parquet"
        if not path.exists():
            pytest.skip(f"data_001 part {i} missing")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        assert h.hexdigest() == expected_hash, f"data_001 part {i} hash changed"


def test_no_modification_to_massive_pit():
    if not SOURCES.exists():
        pytest.skip("sources manifest not generated")
    import hashlib
    with open(SOURCES) as f:
        s = json.load(f)
    path = REPO_ROOT / "research_data/massive_security_master/massive_security_master_sec_join_ready.csv.gz"
    if not path.exists():
        pytest.skip("Massive PIT missing")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    assert h.hexdigest() == s["massive_pit"], "Massive PIT hash changed"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
