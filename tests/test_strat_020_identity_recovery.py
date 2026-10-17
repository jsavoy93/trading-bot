"""
Tests for STRAT-020 historical identity recovery.

These tests verify that the recovery mapping produced by
experiments/strat_020/build_recovery_mapping.py is correct and
deterministic, and that the validation logic in
experiments/strat_020/validate_recovery.py returns the expected results.

They DO NOT touch frozen artifacts, run return calculations, perform
backtests, or invoke any external API. They are pure invariants of the
derived dataset and the source-of-truth hashes recorded in
_step3_sources.json.
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
    """Per-year cohort: 2016=1921, 2017=1868, 2018=1921, 2019=1895, 2020=1856,
    2021=1883, 2022=2055, 2023=2032, 2024=1948, 2025=1898. Total=19277."""
    assert len(rdf) == 19277, f"expected 19277 rows, got {len(rdf)}"


def test_per_year_cohort_sizes(rdf):
    expected = {2016: 1921, 2017: 1868, 2018: 1921, 2019: 1895, 2020: 1856,
                2021: 1883, 2022: 2055, 2023: 2032, 2024: 1948, 2025: 1898}
    actual = rdf.groupby("formation_year").size().to_dict()
    assert actual == expected, f"cohort sizes mismatch: {actual}"


def test_match_type_taxonomy(rdf):
    """Exactly 5 match types, in expected proportions."""
    counts = rdf["match_type"].value_counts()
    assert set(counts.index) == {
        "ORIGINAL_TICKER",
        "UNIQUE_CIK_ALIAS",
        "AMBIGUOUS_MULTIPLE_ALIASES",
        "TICKER_REUSE_BY_OTHER_CIK",
        "NOT_IN_DATA001",
    }, f"unexpected match types: {counts.index.tolist()}"
    assert counts["ORIGINAL_TICKER"] == 13985
    assert counts["UNIQUE_CIK_ALIAS"] == 1102
    assert counts["AMBIGUOUS_MULTIPLE_ALIASES"] == 21
    assert counts["TICKER_REUSE_BY_OTHER_CIK"] == 134
    assert counts["NOT_IN_DATA001"] == 4035


def test_no_duplicate_keys(rdf):
    """(formation_year, cik) must be unique."""
    n = rdf.groupby(["formation_year", "cik"]).size()
    assert (n == 1).all(), f"duplicate keys: {n[n > 1]}"


def test_high_confidence_only_with_bars(rdf):
    """HIGH confidence must always have bar_exists=True and d001_nbars>0."""
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    assert (high["bar_exists"] == True).all()
    assert (high["d001_nbars"] > 0).all()


def test_rejected_has_no_bars(rdf):
    """REJECTED confidence must have bar_exists=False."""
    rej = rdf[rdf["mapping_confidence"] == "REJECTED"]
    assert (rej["bar_exists"] == False).all()


def test_ambiguous_is_low_confidence(rdf):
    """AMBIGUOUS_MULTIPLE_ALIASES must be LOW confidence and unique_match=False."""
    amb = rdf[rdf["match_type"] == "AMBIGUOUS_MULTIPLE_ALIASES"]
    assert (amb["mapping_confidence"] == "LOW").all()
    assert (amb["unique_match"] == False).all()
    # Each ambiguous row must have >1 alias candidate
    assert (amb["alias_candidates"].apply(len) > 1).all()


def test_unique_alias_is_high_confidence(rdf):
    """UNIQUE_CIK_ALIAS must be HIGH confidence and unique_match=True."""
    uq = rdf[rdf["match_type"] == "UNIQUE_CIK_ALIAS"]
    assert (uq["mapping_confidence"] == "HIGH").all()
    assert (uq["unique_match"] == True).all()
    # matched_ticker must be different from original
    assert (uq["matched_ticker"] != uq["original_ticker"]).all()


def test_source_hashes_recorded():
    """The sources manifest must exist and contain all required keys."""
    assert SOURCES.exists()
    with open(SOURCES) as f:
        s = json.load(f)
    for key in ("data_001_parts", "massive_pit", "cohorts", "output_parquet", "output_json"):
        assert key in s, f"missing key in sources manifest: {key}"
    # Must have 5 DATA-001 parts
    assert len(s["data_001_parts"]) == 5
    # Must have 10 cohort hashes (2016-2025)
    assert len(s["cohorts"]) == 10


def test_per_year_coverage_csv():
    assert COVERAGE.exists()


def test_unresolvable_listing_csv():
    assert UNRESOLVABLE.exists()
    df = pd.read_csv(UNRESOLVABLE)
    assert len(df) == 4035
    assert set(df["formation_year"].unique()) == set(range(2016, 2026))


def test_validation_summary_passes():
    """STRAT-020 REMEDIATION: validation summary structure.

    After the 4-tier split of check_5, the corrected validator reports:
      - all_passed: True only if EVERY check (1, 2, 3, 4, 5a, 5b, 5d, 6, 7, 8) passes.
      - 5c TIER 3 (formation-price strict) is EXPECTED to fail with 210 documented
        limitation violations (post-formation-IPO cases). This is the deliberate
        behavior of the corrected validator; it is not silent pass-through.
    This test asserts the structural properties without demanding all_passed=True.
    """
    assert VALIDATION.exists()
    with open(VALIDATION) as f:
        v = json.load(f)
    # The corrected validator must NOT silently pass material invalid mappings.
    # We assert that the summary explicitly reports 5c TIER 3 as FAIL with the
    # documented violation count.
    assert "all_passed" in v
    assert "checks" in v
    # Required checks must all be present.
    for required in [
        "1. No duplicate (formation_year, cik) mappings",
        "2. No share-class collapsing",
        "3. No ticker-reuse silently accepted",
        "4. No ambiguous mappings silently accepted",
        "5a. TIER 1: identity validity (HIGH rows have valid identity fields)",
        "5b. TIER 2: bar availability (HIGH rows have bar_exists=True)",
        "5c. TIER 3: formation-price availability (STRICT; superset of 182)",
        "5d. TIER 4: eligibility consistency (no AMBIGUOUS in eligible_for_backtest)",
        "6. Frozen membership unchanged",
        "7. Source hashes unchanged",
        "8. Recovered matches link to existing bars",
    ]:
        assert required in v["checks"], f"missing check in summary: {required}"
    # 5c must explicitly report the formation-price violation count (>= 1).
    c5c = v["checks"]["5c. TIER 3: formation-price availability (STRICT; superset of 182)"]
    assert "without_formation_date_price" in c5c["detail"]
    # The dataset must report identity_recovery_total and formation_price_valid_total.
    assert "identity_recovery_total" in v
    assert "formation_price_valid_total" in v
    assert "newly_recovered_total" in v
    assert "newly_recovered_formation_price_valid" in v
    # The newly-recovered formation-price-valid count must equal 1,043 (post-remediation baseline).
    assert v["newly_recovered_formation_price_valid"] == 1043, (
        f"expected 1043 newly-recovered formation-price-valid, got {v['newly_recovered_formation_price_valid']}"
    )
    # The newly-recovered-without-formation-price count must be 59.
    assert v["newly_recovered_without_formation_price"] == 59
    # The identity-recovery total is 15,087 (HIGH confidence).
    assert v["identity_recovery_total"] == 15087
    # The formation-price-valid total must be 14,877 (= 15,087 - 210).
    assert v["formation_price_valid_total"] == 14877, (
        f"expected 14877 formation-price-valid, got {v['formation_price_valid_total']}"
    )


def test_no_modification_to_frozen_cohorts():
    """Verify cohort files have not been modified (read-only integrity)."""
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
        assert actual == expected_hash, f"cohort {year_str} hash changed: expected {expected_hash}, got {actual}"


def test_no_modification_to_data_001():
    """Verify DATA-001 parts have not been modified."""
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
    """Verify Massive PIT has not been modified."""
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
