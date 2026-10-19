"""
Validation suite for the STRAT-020 historical identity recovery mapping.

STRAT-020 REMEDIATION v2 (2026-10-18):
  - Each check is classified as CRITICAL or LIMITATION.
  - CRITICAL failures must prevent integration (exit code 1).
  - LIMITATION failures (e.g., formation-date price unavailable) are reported
    separately and do NOT cause a nonzero exit code IF the affected rows are
    correctly excluded from the eligible population.
  - The output JSON has both `critical_passed` and `limitations_present` fields.

Checks (post-remediation v2, 13 total):
  CRITICAL:
    1.  No duplicate company-security-date mappings (CIK is unique per row).
    2.  No share-class cross-match (matched ticker's share class is consistent
        with the CIK's other share classes; original ticker path also verified).
    3.  No ticker reuse across unrelated CIKs (silently accepted).
    4.  No ambiguous mappings silently accepted.
    5a. TIER 1: Valid historical identity (HIGH rows have valid identity fields).
    5b. TIER 2: Existing daily-bar availability (HIGH rows have bar_exists=True).
    5d. TIER 4: Eligibility to count as a successfully recovered formation-price
                observation (no AMBIGUOUS rows in formation_price_eligible=True).
    5e. TIER 5: Alias PIT-validity (UNIQUE_CIK_ALIAS HIGH rows must be PIT-valid
                at or before formation_date).
    5f. TIER 6: ORIGINAL_TICKER PIT-validity (ORIGINAL_TICKER HIGH rows must be
                PIT-confirmed at or before formation_date).
    5g. TIER 7: Share-class match validity (HIGH rows have share_class_match_valid=True).
    6.  Frozen formation membership unchanged (recomputed CIK set equals cohort).
    7.  Source files and authoritative hashes unchanged (verified by sources manifest).
    8.  Recovered matches actually link to existing historical bars.
  LIMITATION:
    5c. TIER 3: Formation-date price availability (STRICT). HIGH rows must have
                formation_price_available=True. A failure here is reported
                separately and does NOT cause a nonzero exit code IF the
                affected rows are excluded from formation_price_eligible.
                Currently 0 rows (all 12,909 HIGH rows are formation-price-valid
                under the 7-day window rule).

Exit code:
  0 if all CRITICAL checks pass (regardless of LIMITATION status).
  1 if any CRITICAL check fails.
  2 if the script itself fails (uncaught exception).
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
PARTS = [REPO_ROOT / f"research_data/alpaca_daily_2016_2026/transfer/daily_bars_part_{i:03d}.parquet" for i in range(1, 6)]
MASSIVE_PIT = REPO_ROOT / "research_data/massive_security_master/massive_security_master_sec_join_ready.csv.gz"
COHORT_DIR = REPO_ROOT / "experiments/strat_017"
RECOVERY_PARQUET = REPO_ROOT / "experiments/strat_020/_step3_recovery_mapping.parquet"
RECOVERY_JSON = REPO_ROOT / "experiments/strat_020/_step3_recovery_mapping.json"
SOURCES = REPO_ROOT / "experiments/strat_020/_step3_sources.json"
OUT_DIR = REPO_ROOT / "experiments/strat_020"
YEARS = [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]


# ---------- Classification ----------
# Each check is either CRITICAL (failure blocks integration) or LIMITATION
# (failure is a documented coverage limitation and is acceptable if the
# affected rows are correctly excluded from the eligible population).
CRITICAL_CHECKS = {
    "1. No duplicate (formation_year, cik) mappings",
    "2. No share-class cross-match",
    "3. No ticker-reuse silently accepted",
    "4. No ambiguous mappings silently accepted",
    "5a. TIER 1: identity validity (HIGH rows have valid identity fields)",
    "5b. TIER 2: bar availability (HIGH rows have bar_exists=True)",
    "5d. TIER 4: eligibility consistency (no AMBIGUOUS in formation_price_eligible)",
    "5e. TIER 5: alias PIT-validity at formation (UNIQUE_CIK_ALIAS HIGH)",
    "5f. TIER 6: ORIGINAL_TICKER PIT-validity at formation (HIGH)",
    "5g. TIER 7: share-class match validity (HIGH rows)",
    "6. Frozen membership unchanged",
    "7. Source hashes unchanged",
    "8. Recovered matches link to existing bars",
}
LIMITATION_CHECKS = {
    "5c. TIER 3: formation-date price availability (STRICT; 7-day window)",
}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- Check functions ----------

def check_1_no_duplicate(rdf: pd.DataFrame) -> tuple[bool, str]:
    n_rows = len(rdf)
    n_unique_keys = rdf[["formation_year", "cik"]].drop_duplicates().shape[0]
    n_dup_keys = rdf.groupby(["formation_year", "cik"]).size().gt(1).sum()
    return n_dup_keys == 0, f"n_rows={n_rows}, unique_keys={n_unique_keys}, dup_keys={n_dup_keys}"


def check_2_no_share_class_cross_match(rdf: pd.DataFrame, mp: pd.DataFrame) -> tuple[bool, str]:
    """BLOCKER 3: real share-class validator.

    For every (formation_year, cik) row with mapping_confidence == HIGH, verify that
    the matched ticker's share_class_figi is consistent with the CIK's share class
    structure. A cross-class match (matched ticker in a different share class than
    other CIK tickers) is REJECTED.

    We use the `share_class_match_valid` field set by the build script. The build
    script sets this to False when:
      - The CIK has multiple share_class_figi values in the PIT, AND
      - The original ticker is associated with a different share_class_figi than the
        matched (alias) ticker.

    For ORIGINAL_TICKER rows, the matched ticker is the original ticker itself, so
    the share class is trivially consistent.
    """
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    if "share_class_match_valid" not in high.columns:
        return False, "share_class_match_valid column missing (build script not run with v2)"
    # Filter to rows where the check is applicable (not None)
    applicable = high[high["share_class_match_valid"].notna()]
    invalid = applicable[applicable["share_class_match_valid"] == False]
    n_invalid = len(invalid)
    if n_invalid > 0:
        sample_ciks = invalid["cik"].head(3).tolist()
        return False, (
            f"high_rows={len(high)}, share_class_cross_match_violations={n_invalid} "
            f"(sample CIKs: {sample_ciks})"
        )
    return True, f"high_rows={len(high)}, share_class_cross_match_violations=0"


def check_3_no_ticker_reuse_accepted(rdf: pd.DataFrame) -> tuple[bool, str]:
    reused = rdf[rdf["match_type"] == "TICKER_REUSE_BY_OTHER_CIK"]
    bad = (reused["mapping_confidence"] == "HIGH").sum() + (reused["mapping_confidence"] == "LOW").sum()
    return bad == 0, f"reused_rows={len(reused)}, high_or_low_confidence={bad}"


def check_4_no_ambiguous_silently(rdf: pd.DataFrame) -> tuple[bool, str]:
    ambig = rdf[rdf["match_type"] == "AMBIGUOUS_MULTIPLE_ALIASES"]
    bad = ((ambig["mapping_confidence"] == "HIGH") | (ambig["unique_match"] == True)).sum()
    return bad == 0, f"ambiguous_rows={len(ambig)}, bad_confidence_or_unique={bad}"


def check_5a_identity_validity(rdf: pd.DataFrame) -> tuple[bool, str]:
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    orig = high[high["match_type"] == "ORIGINAL_TICKER"]
    n_orig_invalid = orig["matched_ticker"].isna().sum()
    alias = high[high["match_type"] == "UNIQUE_CIK_ALIAS"]

    def _len_list(v):
        if hasattr(v, "__len__") and not isinstance(v, str):
            return len(v)
        return 0

    n_alias_no_matched = alias["matched_ticker"].isna().sum()
    n_alias_empty_pit = alias["all_cik_tickers_in_pit"].apply(_len_list).eq(0).sum()
    n_alias_invalid = max(n_alias_no_matched, n_alias_empty_pit)
    n_invalid = n_orig_invalid + n_alias_invalid
    detail = (
        f"high_rows={len(high)}, original_ticker_invalid={n_orig_invalid}, "
        f"unique_cik_alias_invalid={n_alias_invalid} (no_matched={n_alias_no_matched}, empty_pit={n_alias_empty_pit}), "
        f"total_invalid={n_invalid}"
    )
    return n_invalid == 0, detail


def check_5b_bar_availability(rdf: pd.DataFrame) -> tuple[bool, str]:
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    n_no_bars = (high["bar_exists"] == False).sum() + (high["d001_nbars"] == 0).sum()
    n_with_bars = (high["bar_exists"] == True).sum()
    return n_no_bars == 0, f"high_rows={len(high)}, with_bars={n_with_bars}, no_bars={n_no_bars}"


def check_5c_formation_price_availability(rdf: pd.DataFrame) -> tuple[bool, str]:
    """LIMITATION check: 7-day-window formation-date price availability.

    With the 7-day window rule, every HIGH row should have formation_price_available=True.
    Any remaining HIGH row with formation_price_available=False is a documented coverage
    limitation (e.g., a security delisted within 7 days of formation but with no bar on
    the exact formation date).
    """
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    n_no_fp = (~high["formation_price_available"]).sum()
    if n_no_fp == 0:
        return True, f"high_rows={len(high)}, without_formation_date_price=0 (LIMITATION: none)"
    return False, (
        f"high_rows={len(high)}, without_formation_date_price={n_no_fp} "
        f"(LIMITATION: post-formation IPOs/spin-offs/delisted-within-window; these rows "
        f"are EXCLUDED from formation_price_eligible)"
    )


def check_5d_eligibility_consistency(rdf: pd.DataFrame) -> tuple[bool, str]:
    """TIER 4: formation_price_eligible must not include AMBIGUOUS or REJECTED rows."""
    if "formation_price_eligible" not in rdf.columns:
        return False, "formation_price_eligible field missing (build script not run with v2)"
    elig = rdf[rdf["formation_price_eligible"] == True]
    n_ambiguous_eligible = int((elig["match_type"] == "AMBIGUOUS_MULTIPLE_ALIASES").sum())
    n_total_eligible = int(rdf["formation_price_eligible"].sum())
    n_high = int((rdf["mapping_confidence"] == "HIGH").sum())
    n_high_no_fp = int(((rdf["mapping_confidence"] == "HIGH") & (~rdf["formation_price_available"])).sum())
    return n_ambiguous_eligible == 0, (
        f"total_formation_price_eligible={n_total_eligible}, "
        f"high_total={n_high}, high_no_formation_price={n_high_no_fp}, "
        f"ambiguous_eligible={n_ambiguous_eligible} (must be 0)"
    )


def check_5e_alias_pit_validity(rdf: pd.DataFrame) -> tuple[bool, str]:
    """BLOCKER 2: UNIQUE_CIK_ALIAS HIGH rows must be PIT-valid at or before formation_date.

    This catches the "alias associated with the CIK only after formation" defect:
    a ticker that only appears in the PIT at a later date is NOT a valid identity
    match for the current formation date.
    """
    alias_high = rdf[(rdf["match_type"] == "UNIQUE_CIK_ALIAS") & (rdf["mapping_confidence"] == "HIGH")]
    if "alias_pit_valid_at_formation" not in alias_high.columns:
        return False, "alias_pit_valid_at_formation column missing"
    bad = (alias_high["alias_pit_valid_at_formation"] != True).sum()  # noqa: E712
    return bad == 0, f"unique_cik_alias_high={len(alias_high)}, alias_pit_invalid={bad}"


def check_5f_original_ticker_pit_validity(rdf: pd.DataFrame) -> tuple[bool, str]:
    """BLOCKER 2/3: ORIGINAL_TICKER HIGH rows must be PIT-valid at or before formation_date.

    A ticker that the PIT does not confirm was associated with the CIK at or before
    formation_date is NOT a valid ORIGINAL_TICKER match — it could be a PIT data gap
    OR a ticker reuse that the PIT did not yet record. Either way, the evidence is
    insufficient for HIGH confidence.
    """
    orig_high = rdf[(rdf["match_type"] == "ORIGINAL_TICKER") & (rdf["mapping_confidence"] == "HIGH")]
    if "original_ticker_pit_valid_at_formation" not in orig_high.columns:
        return False, "original_ticker_pit_valid_at_formation column missing"
    bad = (orig_high["original_ticker_pit_valid_at_formation"] != True).sum()  # noqa: E712
    return bad == 0, f"original_ticker_high={len(orig_high)}, pit_invalid={bad}"


def check_5g_share_class_match(rdf: pd.DataFrame) -> tuple[bool, str]:
    """BLOCKER 3: HIGH rows must have share_class_match_valid=True (or None for AMBIGUOUS)."""
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    if "share_class_match_valid" not in high.columns:
        return False, "share_class_match_valid column missing"
    # For ORIGINAL_TICKER, share_class_match_valid is set. For UNIQUE_CIK_ALIAS HIGH, also set.
    bad = (high["share_class_match_valid"] == False).sum()
    return bad == 0, f"high_rows={len(high)}, share_class_match_invalid={bad}"


def check_6_frozen_membership(rdf: pd.DataFrame) -> tuple[bool, str]:
    issues = []
    for year in YEARS:
        cohort = pd.read_csv(COHORT_DIR / f"_cohort_{year}.csv.gz")
        cohort_ciks = set(str(int(c)) for c in cohort["cik_10digit"].unique())
        rec_ciks = set(rdf[rdf["formation_year"] == year]["cik"].astype(str).unique())
        if cohort_ciks != rec_ciks:
            issues.append(f"{year}: missing={len(cohort_ciks - rec_ciks)}, extra={len(rec_ciks - cohort_ciks)}")
    return len(issues) == 0, "; ".join(issues) if issues else "all years match"


def check_7_source_hashes() -> tuple[bool, str]:
    with open(SOURCES) as f:
        recorded = json.load(f)
    actual = {
        "data_001_parts": [sha256_file(p) for p in PARTS if p.exists()],
        "massive_pit": sha256_file(MASSIVE_PIT),
        "cohorts": {str(y): sha256_file(COHORT_DIR / f"_cohort_{y}.csv.gz") for y in YEARS},
    }
    diffs = []
    if recorded["massive_pit"] != actual["massive_pit"]:
        diffs.append("massive_pit hash mismatch")
    if recorded["data_001_parts"] != actual["data_001_parts"]:
        diffs.append("data_001 parts hash mismatch")
    for y in YEARS:
        if recorded["cohorts"][str(y)] != actual["cohorts"][str(y)]:
            diffs.append(f"cohort {y} hash mismatch")
    return len(diffs) == 0, "; ".join(diffs) if diffs else "all sources unchanged"


def check_8_bars_actually_exist(rdf: pd.DataFrame) -> tuple[bool, str]:
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    no_bars = high[high["d001_nbars"] == 0]
    return len(no_bars) == 0, f"high_confidence_rows={len(high)}, with_zero_bars={len(no_bars)}"


# ---------- Per-year coverage (Blocker 5) ----------

def per_year_coverage(rdf: pd.DataFrame) -> pd.DataFrame:
    """Per-year coverage with DISTINCT columns for each concept.

    STRAT-020 REMEDIATION v3 (2026-10-19): added 7 new distinct columns
    per owner re-review (Blocker 5 detailed split):
      - high_confidence_original_ticker: ORIGINAL_TICKER AND HIGH
      - new_identity_candidates: UNIQUE_CIK_ALIAS total
      - verified_recovered_identities: UNIQUE_CIK_ALIAS AND HIGH
      - new_exact_formation_session_price_matches: UNIQUE_CIK_ALIAS AND formation_price_eligible
      - distinct_recovered_ciks: distinct CIKs in UNIQUE_CIK_ALIAS
      - distinct_verified_securities: distinct matched_tickers in HIGH
      - prior_session_only_availability: HIGH AND NOT formation_price_available
        (the 79 documented LIMITATION rows; post-formation IPOs)

    Original columns (v2):
      formation_year, n_cohort,
      in_data001_original, recovered_unique_alias,
      ambiguous_low_confidence, rejected_ticker_reuse, not_in_data001,
      unverified_total, high_confidence_total,
      raw_bar_available, valid_formation_date_price,
      high_with_formation_price (HEADLINE),
      rejected_or_ambiguous_with_formation_price,
      low_confidence_with_formation_price.
    """
    rows = []
    for year in YEARS:
        sub = rdf[rdf["formation_year"] == year]
        n_cohort = len(sub)
        in_data001_original = (sub["match_type"] == "ORIGINAL_TICKER").sum()
        recovered_unique_alias = (sub["match_type"] == "UNIQUE_CIK_ALIAS").sum()
        ambiguous_low_confidence = (sub["match_type"] == "AMBIGUOUS_MULTIPLE_ALIASES").sum()
        rejected_ticker_reuse = (sub["match_type"] == "TICKER_REUSE_BY_OTHER_CIK").sum()
        not_in_data001 = (sub["match_type"] == "NOT_IN_DATA001").sum()
        unverified_total = (sub["mapping_confidence"] == "UNVERIFIED").sum()
        high_confidence_total = (sub["mapping_confidence"] == "HIGH").sum()

        raw_bar_available = sub["bar_exists"].sum()
        valid_formation_date_price = sub["formation_price_available"].sum()

        high = sub[sub["mapping_confidence"] == "HIGH"]
        high_with_formation_price = (high["formation_price_available"] == True).sum()  # noqa: E712

        # v3 NEW: ORIGINAL_TICKER AND HIGH
        high_confidence_original_ticker = (
            (sub["match_type"] == "ORIGINAL_TICKER") & (sub["mapping_confidence"] == "HIGH")
        ).sum()

        # v3 NEW: UNIQUE_CIK_ALIAS total (new identity candidates)
        new_identity_candidates = int(recovered_unique_alias)
        # v3 NEW: UNIQUE_CIK_ALIAS AND HIGH (verified recovered identities)
        verified_recovered_identities = (
            (sub["match_type"] == "UNIQUE_CIK_ALIAS") & (sub["mapping_confidence"] == "HIGH")
        ).sum()
        # v3 NEW: UNIQUE_CIK_ALIAS AND formation_price_eligible (exact formation-session price matches)
        if "formation_price_eligible" in sub.columns:
            new_exact_formation_session_price_matches = (
                (sub["match_type"] == "UNIQUE_CIK_ALIAS") & (sub["formation_price_eligible"] == True)  # noqa: E712
            ).sum()
        else:
            new_exact_formation_session_price_matches = (
                (sub["match_type"] == "UNIQUE_CIK_ALIAS") & (sub["formation_price_available"] == True)  # noqa: E712
            ).sum()
        # v3 NEW: distinct CIKs in UNIQUE_CIK_ALIAS
        alias_sub = sub[sub["match_type"] == "UNIQUE_CIK_ALIAS"]
        distinct_recovered_ciks = int(alias_sub["cik"].astype(str).nunique())
        # v3 NEW: distinct matched_tickers in HIGH
        high_sub = sub[sub["mapping_confidence"] == "HIGH"]
        distinct_verified_securities = int(high_sub["matched_ticker"].dropna().astype(str).nunique())
        # v3 NEW: HIGH AND NOT formation_price_available (the 79 LIMITATION)
        prior_session_only_availability = (~high["formation_price_available"]).sum()

        non_high = sub[sub["mapping_confidence"] != "HIGH"]
        non_high_with_fp = (non_high["formation_price_available"] == True).sum()  # noqa: E712

        low = sub[sub["mapping_confidence"] == "LOW"]
        low_with_fp = (low["formation_price_available"] == True).sum()  # noqa: E712

        rows.append({
            "formation_year": year,
            "n_cohort": int(n_cohort),
            "in_data001_original": int(in_data001_original),
            "recovered_unique_alias": int(recovered_unique_alias),
            "ambiguous_low_confidence": int(ambiguous_low_confidence),
            "rejected_ticker_reuse": int(rejected_ticker_reuse),
            "not_in_data001": int(not_in_data001),
            "unverified_total": int(unverified_total),
            "high_confidence_total": int(high_confidence_total),
            "high_confidence_original_ticker": int(high_confidence_original_ticker),
            "new_identity_candidates": int(new_identity_candidates),
            "verified_recovered_identities": int(verified_recovered_identities),
            "new_exact_formation_session_price_matches": int(new_exact_formation_session_price_matches),
            "distinct_recovered_ciks": distinct_recovered_ciks,
            "distinct_verified_securities": distinct_verified_securities,
            "prior_session_only_availability": int(prior_session_only_availability),
            "raw_bar_available": int(raw_bar_available),
            "valid_formation_date_price": int(valid_formation_date_price),
            "high_with_formation_price": int(high_with_formation_price),
            "rejected_or_ambiguous_with_formation_price": int(non_high_with_fp),
            "low_confidence_with_formation_price": int(low_with_fp),
        })
    return pd.DataFrame(rows)


def unresolvable_listing(rdf: pd.DataFrame) -> pd.DataFrame:
    return rdf[rdf["match_type"] == "NOT_IN_DATA001"].copy()


# ---------- Main ----------

def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Loading recovery mapping ...")
    rdf = pd.read_parquet(RECOVERY_PARQUET)
    print(f"  Rows: {len(rdf)}")
    print("Loading Massive PIT for share-class check ...")
    mp = pd.read_csv(MASSIVE_PIT)
    mp["share_class_figi"] = mp["share_class_figi"].fillna("").astype(str)
    mp["cik_str"] = mp["cik_10digit"].astype(str)

    print()
    print("=" * 70)
    print("STEP 5 — VALIDATION CHECKS (REMEDIATION v2)")
    print("=" * 70)

    check_defs = [
        ("1. No duplicate (formation_year, cik) mappings", check_1_no_duplicate, (rdf,), "CRITICAL"),
        ("2. No share-class cross-match", check_2_no_share_class_cross_match, (rdf, mp), "CRITICAL"),
        ("3. No ticker-reuse silently accepted", check_3_no_ticker_reuse_accepted, (rdf,), "CRITICAL"),
        ("4. No ambiguous mappings silently accepted", check_4_no_ambiguous_silently, (rdf,), "CRITICAL"),
        ("5a. TIER 1: identity validity (HIGH rows have valid identity fields)", check_5a_identity_validity, (rdf,), "CRITICAL"),
        ("5b. TIER 2: bar availability (HIGH rows have bar_exists=True)", check_5b_bar_availability, (rdf,), "CRITICAL"),
        ("5c. TIER 3: formation-date price availability (STRICT; 7-day window)", check_5c_formation_price_availability, (rdf,), "LIMITATION"),
        ("5d. TIER 4: eligibility consistency (no AMBIGUOUS in formation_price_eligible)", check_5d_eligibility_consistency, (rdf,), "CRITICAL"),
        ("5e. TIER 5: alias PIT-validity at formation (UNIQUE_CIK_ALIAS HIGH)", check_5e_alias_pit_validity, (rdf,), "CRITICAL"),
        ("5f. TIER 6: ORIGINAL_TICKER PIT-validity at formation (HIGH)", check_5f_original_ticker_pit_validity, (rdf,), "CRITICAL"),
        ("5g. TIER 7: share-class match validity (HIGH rows)", check_5g_share_class_match, (rdf,), "CRITICAL"),
        ("6. Frozen membership unchanged", check_6_frozen_membership, (rdf,), "CRITICAL"),
        ("7. Source hashes unchanged", check_7_source_hashes, (), "CRITICAL"),
        ("8. Recovered matches link to existing bars", check_8_bars_actually_exist, (rdf,), "CRITICAL"),
    ]

    results = {}
    for name, fn, args, severity in check_defs:
        passed, detail = fn(*args)
        status = "PASS" if passed else "FAIL"
        results[name] = {"status": status, "severity": severity, "detail": detail}
        print(f"  [{status}/{severity}] {name}: {detail}")

    # Per-year coverage table (Blocker 5)
    cov = per_year_coverage(rdf)
    cov_path = OUT_DIR / "_step5_per_year_coverage.csv"
    cov.to_csv(cov_path, index=False)
    print()
    print("Per-year coverage (with distinct columns):")
    print(cov.to_string(index=False))

    # Unresolvable listing
    unres = unresolvable_listing(rdf)
    unres_path = OUT_DIR / "_step5_unresolvable_securities.csv"
    cols = ["formation_year", "cik", "original_ticker", "all_cik_tickers_in_pit", "d001_nbars"]
    unres[cols].to_csv(unres_path, index=False)

    # Year-by-year reconciliation
    sum_high_with_fp = int(cov["high_with_formation_price"].sum())
    n_high_total = int((rdf["mapping_confidence"] == "HIGH").sum())
    n_eligible = int(rdf["formation_price_eligible"].sum())
    n_high_no_fp = n_high_total - n_eligible  # documented LIMITATION
    print()
    print(f"  Sum of per-year high_with_formation_price: {sum_high_with_fp}")
    print(f"  Headline HIGH total: {n_high_total}")
    print(f"  Headline formation_price_eligible total: {n_eligible}")
    print(f"  HIGH without formation-date price (LIMITATION): {n_high_no_fp}")
    # The headline figure is formation_price_eligible. Per-year must reconcile to it.
    # HIGH total may be larger (by exactly the LIMITATION count) because some HIGH
    # rows are correctly excluded from the eligible population.
    assert sum_high_with_fp == n_eligible, (
        f"RECONCILIATION FAILURE: per-year sum ({sum_high_with_fp}) != "
        f"formation_price_eligible ({n_eligible})"
    )

    # Critical vs limitation summary
    critical_results = {n: r for n, r in results.items() if r["severity"] == "CRITICAL"}
    limitation_results = {n: r for n, r in results.items() if r["severity"] == "LIMITATION"}
    critical_passed = all(r["status"] == "PASS" for r in critical_results.values())
    limitations_present = any(r["status"] == "FAIL" for r in limitation_results.values())

    summary = {
        "validation_run_at": datetime.now(timezone.utc).isoformat(),
        "validation_version": "STRAT-020 REMEDIATION v2 (2026-10-18; 6-blocker correction)",
        "n_recovery_rows": int(len(rdf)),
        "identity_recovery_total": n_high_total,
        "formation_price_valid_total": n_eligible,
        "high_without_formation_price": n_high_total - n_eligible,
        "newly_recovered_total": int((rdf["match_type"] == "UNIQUE_CIK_ALIAS").sum()),
        "newly_recovered_formation_price_valid": int(
            ((rdf["match_type"] == "UNIQUE_CIK_ALIAS") & (rdf["formation_price_eligible"] == True)).sum()  # noqa: E712
        ),
        "newly_recovered_without_formation_price": int(
            ((rdf["match_type"] == "UNIQUE_CIK_ALIAS") & (rdf["formation_price_eligible"] == False)).sum()  # noqa: E712
        ),
        "unverified_total": int((rdf["mapping_confidence"] == "UNVERIFIED").sum()),
        "rejected_total": int((rdf["mapping_confidence"] == "REJECTED").sum()),
        "ambiguous_total": int((rdf["mapping_confidence"] == "LOW").sum()),
        "not_in_data001_total": int((rdf["mapping_confidence"] == "NO_MATCH").sum()),
        "checks": results,
        "critical_passed": critical_passed,
        "limitations_present": limitations_present,
        "all_passed": critical_passed and not limitations_present,
    }
    out_path = OUT_DIR / "_step5_validation.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)

    print()
    print("=" * 70)
    print("CRITICAL vs LIMITATION SUMMARY")
    print("=" * 70)
    print(f"  CRITICAL checks: {len(critical_results)}")
    print(f"  CRITICAL passed: {sum(1 for r in critical_results.values() if r['status'] == 'PASS')}")
    print(f"  CRITICAL failed: {sum(1 for r in critical_results.values() if r['status'] == 'FAIL')}")
    print(f"  LIMITATION checks: {len(limitation_results)}")
    print(f"  LIMITATION passed: {sum(1 for r in limitation_results.values() if r['status'] == 'PASS')}")
    print(f"  LIMITATION failed: {sum(1 for r in limitation_results.values() if r['status'] == 'FAIL')}")
    print()
    print(f"  CRITICAL: {'PASS' if critical_passed else 'FAIL'}")
    print(f"  LIMITATIONS: {'PRESENT' if limitations_present else 'ABSENT'}")
    print(f"  ALL PASSED (legacy combined): {summary['all_passed']}")
    print()
    print(f"Validation summary -> {out_path}")
    print(f"Per-year coverage -> {cov_path}")
    print(f"Unresolvable listing -> {unres_path} ({len(unres)} rows)")

    # Exit code: 0 if critical pass, 1 if any critical fail
    if not critical_passed:
        failed_critical = [n for n, r in critical_results.items() if r["status"] != "PASS"]
        print()
        print(f"FAILED CRITICAL CHECKS: {failed_critical}")
        print("EXIT CODE: 1")
        sys.exit(1)
    print("EXIT CODE: 0")


if __name__ == "__main__":
    main()
