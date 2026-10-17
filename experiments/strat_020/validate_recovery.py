"""
Validation suite for the STRAT-020 historical identity recovery mapping.

Validates the acceptance checks from the owner directive.
After STRAT-020 REMEDIATION (2026-10-15), check_5 is split into 4 sub-tiers
(5a identity, 5b bars, 5c formation-price, 5d eligibility) so that no
material invalid mapping is silently passed.

Checks (post-remediation, 11 total):
  1.  No duplicate company-security-date mappings (CIK is unique per row).
  2.  No incorrectly merged share classes.
  3.  No ticker reuse across unrelated CIKs (silently accepted).
  4.  No ambiguous mappings silently accepted.
  5a. TIER 1: Valid historical identity (HIGH rows have valid identity fields).
  5b. TIER 2: Existing daily-bar availability (HIGH rows have bar_exists=True).
  5c. TIER 3: Formation-date price availability (STRICT: HIGH rows must have
              formation_price_available=True). This is the strict check that
              previously silently passed. 211 HIGH rows lack formation-date
              price (post-formation-IPO cases); they are reported as FAIL
              with the violation count, not silently passed.
  5d. TIER 4: Eligibility to count as a successfully recovered formation-price
              observation (no AMBIGUOUS rows in eligible_for_backtest=True).
  6.  Frozen formation membership unchanged (recomputed CIK set equals cohort).
  7.  Source files and authoritative hashes unchanged (verified by sources manifest).
  8.  Recovered matches actually link to existing historical bars.

Also produces per-year before/after coverage counts and a list of unresolvable securities.

No return / no IC / no backtest / no API call. Read-only validation.
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


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_1_no_duplicate(rdf: pd.DataFrame) -> tuple[bool, str]:
    """Per (formation_year, cik) the recovery must have exactly one row."""
    n_rows = len(rdf)
    n_unique_keys = rdf[["formation_year", "cik"]].drop_duplicates().shape[0]
    n_dup_keys = rdf.groupby(["formation_year", "cik"]).size().gt(1).sum()
    return n_dup_keys == 0, f"n_rows={n_rows}, unique_keys={n_unique_keys}, dup_keys={n_dup_keys}"


def check_2_no_share_class_merge(rdf: pd.DataFrame, mp: pd.DataFrame) -> tuple[bool, str]:
    """Verify that the matched tickers do not collapse distinct share classes.
    For each CIK with multiple share_class_figi values in PIT, the matched tickers must
    not all be the same ticker (which would imply collapsing share classes).
    """
    scik = mp[mp["share_class_figi"] != ""].groupby("cik_str")["share_class_figi"].nunique()
    multi_share_class_ciks = set(scik[scik > 1].index)
    relevant = rdf[rdf["cik"].astype(str).isin(multi_share_class_ciks)]
    # Among these, check if any (cik, formation_year) maps to >1 distinct matched_ticker
    # This would imply the recovery incorrectly merged share classes.
    grouped = relevant.groupby(["formation_year", "cik"])["matched_ticker"].nunique()
    violations = (grouped > 1).sum()
    return violations == 0, f"multi_share_class_ciks={len(multi_share_class_ciks)}, violations={violations}"


def check_3_no_ticker_reuse_accepted(rdf: pd.DataFrame) -> tuple[bool, str]:
    """TICKER_REUSE_BY_OTHER_CIK matches must be REJECTED; they must NOT be HIGH confidence."""
    reused = rdf[rdf["match_type"] == "TICKER_REUSE_BY_OTHER_CIK"]
    high_count = (reused["mapping_confidence"] == "HIGH").sum() + (reused["mapping_confidence"] == "LOW").sum()
    return high_count == 0, f"reused_rows={len(reused)}, high_or_low_confidence={high_count}"


def check_4_no_ambiguous_silently(rdf: pd.DataFrame) -> tuple[bool, str]:
    """AMBIGUOUS_MULTIPLE_ALIASES must be marked LOW and unique_match=False."""
    ambig = rdf[rdf["match_type"] == "AMBIGUOUS_MULTIPLE_ALIASES"]
    bad = ((ambig["mapping_confidence"] == "HIGH") | (ambig["unique_match"] == True)).sum()
    return bad == 0, f"ambiguous_rows={len(ambig)}, bad_confidence_or_unique={bad}"


def check_5a_identity_validity(rdf: pd.DataFrame) -> tuple[bool, str]:
    """STRAT-020 REMEDIATION TIER 1: identity validity.

    For every HIGH-confidence row, the identity must be valid:
      - ORIGINAL_TICKER: the original ticker is in DATA-001 (data-001 membership is
        the identity claim; CIK/PIT lookup is not used for the original-ticker path,
        so an empty all_cik_tickers_in_pit for a CIK that has no PIT record is OK).
        matched_ticker must be set (sanity check).
      - UNIQUE_CIK_ALIAS: the CIK must have at least one PIT ticker (alias resolution
        requires a valid PIT record). matched_ticker must be set.
    For REJECTED/NO_MATCH/AMBIGUOUS rows, no identity claim is made (excluded).
    """
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    # ORIGINAL_TICKER rows: check matched_ticker is set.
    orig = high[high["match_type"] == "ORIGINAL_TICKER"]
    n_orig_invalid = orig["matched_ticker"].isna().sum()
    # UNIQUE_CIK_ALIAS rows: check matched_ticker is set AND all_cik_tickers_in_pit is non-empty.
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
        f"total_invalid={n_invalid} "
        f"(Note: ORIGINAL_TICKER with empty PIT is allowed; identity is data-001 membership)"
    )
    return n_invalid == 0, detail


def check_5b_bar_availability(rdf: pd.DataFrame) -> tuple[bool, str]:
    """STRAT-020 REMEDIATION TIER 2: bar availability.

    For every HIGH-confidence row, bar_exists must be True and d001_nbars > 0.
    This is a sanity check on the bar_exists flag.
    """
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    n_no_bars = (high["bar_exists"] == False).sum() + (high["d001_nbars"] == 0).sum()
    n_with_bars = (high["bar_exists"] == True).sum()
    detail = f"high_rows={len(high)}, with_bars={n_with_bars}, no_bars={n_no_bars}"
    return n_no_bars == 0, detail


def check_5c_formation_price_availability(rdf: pd.DataFrame) -> tuple[bool, str]:
    """STRAT-020 REMEDIATION TIER 3 (STRICT): formation-date price availability.

    The previous monolithic check_5 returned True unconditionally if the
    formation_price_available flag was computed for every matched row. The
    owner-directed remediation requires this to be a STRICT check: every
    HIGH-confidence row must have formation_price_available=True.

    Background: 211 HIGH-confidence rows have formation_price_available=False
    because the matched ticker did not have a public trading history at the
    formation date. These are documented limitations (post-formation IPOs,
    spin-offs, delayed renames like HSC->NVRI in 2023). They are NOT
    silently passed — this check returns FAIL with the violation count.

    Historical relationship (per §5.1-5.7 of the prior independent QA):
      - 182: original strict criterion (first_bar_year > formation_year + 1)
      - 216: broader count (bar_exists=True AND !formation_price_available)
             broken down as 151 ORIGINAL + 59 UNIQUE_CIK_ALIAS + 6 AMBIGUOUS
      - 59: subset of 216 = UNIQUE_CIK_ALIAS without formation-date price
             (the post-formation-IPO count among newly-recovered rows)
    The TIER 3 violation count is a strict superset of 182.
    """
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    n_no_fp = (~high["formation_price_available"]).sum()
    detail = (
        f"high_rows={len(high)}, "
        f"without_formation_date_price={n_no_fp} (STRICT; documented limitation: "
        f"post-formation IPOs/spin-offs/delayed renames; superset of original 182-violation count)"
    )
    return n_no_fp == 0, detail


def check_5d_eligibility_consistency(rdf: pd.DataFrame) -> tuple[bool, str]:
    """STRAT-020 REMEDIATION TIER 4: eligibility consistency.

    The "eligible_for_backtest" field (added in the build script) is True
    iff mapping_confidence == HIGH AND formation_price_available == True.
    No AMBIGUOUS row (mapping_confidence == LOW) may be eligible_for_backtest.

    This is the source-of-truth for the "formation-price-valid recovery count"
    in the headline figure (15,087 IDENTITY-recovery vs 14,876
    FORMATION-PRICE-VALID recovery).
    """
    if "eligible_for_backtest" not in rdf.columns:
        return False, "eligible_for_backtest field missing (build script not run with remediation)"
    ambig = rdf[rdf["match_type"] == "AMBIGUOUS_MULTIPLE_ALIASES"]
    n_ambiguous_eligible = int(ambig["eligible_for_backtest"].sum())
    n_total_eligible = int(rdf["eligible_for_backtest"].sum())
    n_high = int((rdf["mapping_confidence"] == "HIGH").sum())
    n_high_no_fp = int(((rdf["mapping_confidence"] == "HIGH") & (~rdf["formation_price_available"])).sum())
    detail = (
        f"total_eligible={n_total_eligible}, "
        f"high_total={n_high}, high_no_formation_price={n_high_no_fp}, "
        f"ambiguous_rows={len(ambig)}, ambiguous_eligible={n_ambiguous_eligible} (must be 0)"
    )
    return n_ambiguous_eligible == 0, detail


def check_6_frozen_membership(rdf: pd.DataFrame) -> tuple[bool, str]:
    """Recovery membership must equal cohort membership: per year, CIK set must match."""
    issues = []
    for year in YEARS:
        cohort = pd.read_csv(COHORT_DIR / f"_cohort_{year}.csv.gz")
        cohort_ciks = set(str(int(c)) for c in cohort["cik_10digit"].unique())
        rec_ciks = set(rdf[rdf["formation_year"] == year]["cik"].astype(str).unique())
        if cohort_ciks != rec_ciks:
            issues.append(f"{year}: missing={len(cohort_ciks - rec_ciks)}, extra={len(rec_ciks - cohort_ciks)}")
    return len(issues) == 0, "; ".join(issues) if issues else "all years match"


def check_7_source_hashes() -> tuple[bool, str]:
    """Re-hash sources and compare to manifest."""
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
    """Every HIGH-confidence recovered match must have non-zero bars in DATA-001."""
    high = rdf[rdf["mapping_confidence"] == "HIGH"]
    no_bars = high[high["d001_nbars"] == 0]
    return len(no_bars) == 0, f"high_confidence_rows={len(high)}, with_zero_bars={len(no_bars)}"


def per_year_coverage(rdf: pd.DataFrame) -> pd.DataFrame:
    """Per-year before/after descriptive coverage counts."""
    rows = []
    for year in YEARS:
        sub = rdf[rdf["formation_year"] == year]
        n_cohort = len(sub)
        n_in_data001_before = (sub["match_type"] == "ORIGINAL_TICKER").sum()
        n_recovered_unique_alias = (sub["match_type"] == "UNIQUE_CIK_ALIAS").sum()
        n_ambiguous = (sub["match_type"] == "AMBIGUOUS_MULTIPLE_ALIASES").sum()
        n_rejected = (sub["match_type"] == "TICKER_REUSE_BY_OTHER_CIK").sum()
        n_not_in_data001 = (sub["match_type"] == "NOT_IN_DATA001").sum()
        n_high_confidence = (sub["mapping_confidence"] == "HIGH").sum()
        n_with_bars = sub["bar_exists"].sum()
        n_with_formation_price = sub["formation_price_available"].sum()
        rows.append({
            "formation_year": year,
            "n_cohort": n_cohort,
            "in_data001_original": n_in_data001_before,
            "recovered_unique_alias": n_recovered_unique_alias,
            "ambiguous_low_confidence": n_ambiguous,
            "rejected_ticker_reuse": n_rejected,
            "not_in_data001": n_not_in_data001,
            "high_confidence_total": n_high_confidence,
            "with_bars_in_data001": int(n_with_bars),
            "with_formation_price": int(n_with_formation_price),
        })
    return pd.DataFrame(rows)


def unresolvable_listing(rdf: pd.DataFrame) -> pd.DataFrame:
    """List of securities that remain unresolvable after recovery."""
    return rdf[rdf["match_type"] == "NOT_IN_DATA001"].copy()


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Loading recovery mapping ...")
    rdf = pd.read_parquet(RECOVERY_PARQUET)
    print(f"  Rows: {len(rdf)}")
    print("Loading Massive PIT for share-class check ...")
    mp = pd.read_csv(MASSIVE_PIT)
    mp["share_class_figi"] = mp["share_class_figi"].fillna("").astype(str)
    mp["cik_str"] = mp["cik_10digit"].astype(str)

    print()
    print("=" * 60)
    print("STEP 5 — VALIDATION CHECKS")
    print("=" * 60)

    checks = [
        ("1. No duplicate (formation_year, cik) mappings", check_1_no_duplicate(rdf)),
        ("2. No share-class collapsing", check_2_no_share_class_merge(rdf, mp)),
        ("3. No ticker-reuse silently accepted", check_3_no_ticker_reuse_accepted(rdf)),
        ("4. No ambiguous mappings silently accepted", check_4_no_ambiguous_silently(rdf)),
        ("5a. TIER 1: identity validity (HIGH rows have valid identity fields)", check_5a_identity_validity(rdf)),
        ("5b. TIER 2: bar availability (HIGH rows have bar_exists=True)", check_5b_bar_availability(rdf)),
        ("5c. TIER 3: formation-price availability (STRICT; superset of 182)", check_5c_formation_price_availability(rdf)),
        ("5d. TIER 4: eligibility consistency (no AMBIGUOUS in eligible_for_backtest)", check_5d_eligibility_consistency(rdf)),
        ("6. Frozen membership unchanged", check_6_frozen_membership(rdf)),
        ("7. Source hashes unchanged", check_7_source_hashes()),
        ("8. Recovered matches link to existing bars", check_8_bars_actually_exist(rdf)),
    ]
    results = {}
    for name, (passed, detail) in checks:
        status = "PASS" if passed else "FAIL"
        results[name] = {"status": status, "detail": detail}
        print(f"  [{status}] {name}: {detail}")

    # Per-year coverage table
    cov = per_year_coverage(rdf)
    cov_path = OUT_DIR / "_step5_per_year_coverage.csv"
    cov.to_csv(cov_path, index=False)
    print()
    print("Per-year coverage saved to:", cov_path)
    print()
    print(cov.to_string(index=False))

    # Unresolvable listing
    unres = unresolvable_listing(rdf)
    unres_path = OUT_DIR / "_step5_unresolvable_securities.csv"
    cols = ["formation_year", "cik", "original_ticker", "all_cik_tickers_in_pit", "d001_nbars"]
    unres[cols].to_csv(unres_path, index=False)
    print()
    print(f"Unresolvable securities (NOT_IN_DATA001): {len(unres)}")
    print(f"  saved to: {unres_path}")

    # Persist validation results
    high_total = int((rdf["mapping_confidence"] == "HIGH").sum())
    n_eligible = int(rdf.get("eligible_for_backtest", pd.Series([False] * len(rdf))).sum()) if "eligible_for_backtest" in rdf.columns else None
    nr = rdf[rdf["match_type"] == "UNIQUE_CIK_ALIAS"]
    nr_eligible = int(nr["eligible_for_backtest"].sum()) if "eligible_for_backtest" in rdf.columns else None
    nr_ineligible = int((~nr["eligible_for_backtest"]).sum()) if "eligible_for_backtest" in rdf.columns else None
    summary = {
        "validation_run_at": datetime.now(timezone.utc).isoformat(),
        "validation_version": "STRAT-020 REMEDIATION 2026-10-15 (4-tier check_5)",
        "n_recovery_rows": int(len(rdf)),
        "identity_recovery_total": high_total,
        "formation_price_valid_total": n_eligible,
        "newly_recovered_total": int(len(nr)),
        "newly_recovered_formation_price_valid": nr_eligible,
        "newly_recovered_without_formation_price": nr_ineligible,
        "checks": results,
        "all_passed": all(c[1][0] for c in checks),
    }
    out_path = OUT_DIR / "_step5_validation.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    print()
    print(f"Validation summary -> {out_path}")
    print(f"ALL PASSED: {summary['all_passed']}")
    if not summary["all_passed"]:
        failed = [n for n, r in results.items() if r["status"] != "PASS"]
        print(f"FAILED CHECKS: {failed}")


if __name__ == "__main__":
    main()
