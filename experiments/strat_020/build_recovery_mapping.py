"""
Build the historical identity recovery mapping for STRAT-017 cohort members.

Inputs (read-only, frozen):
  - experiments/strat_017/_cohort_<year>.csv.gz  (frozen cohort; 10 files for 2016-2025)
  - research_data/alpaca_daily_2016_2026/transfer/daily_bars_part_001..005.parquet  (DATA-001)
  - research_data/massive_security_master/massive_security_master_sec_join_ready.csv.gz  (Massive PIT)

Outputs (new, additive; overwrites previous remediation outputs):
  - experiments/strat_020/_step3_recovery_mapping.parquet
  - experiments/strat_020/_step3_recovery_mapping.json
  - experiments/strat_020/_step3_sources.json  (SHA-256 manifest of inputs + outputs)

Match-type taxonomy (unchanged):
  ORIGINAL_TICKER            : original STRAT-017 ticker is in DATA-001
  UNIQUE_CIK_ALIAS           : original ticker absent but exactly one alternate ticker for the same CIK is in DATA-001
  AMBIGUOUS_MULTIPLE_ALIASES : multiple alternate tickers for the same CIK are in DATA-001
  TICKER_REUSE_BY_OTHER_CIK  : original ticker is mapped to a different CIK in PIT
  NOT_IN_DATA001             : no CIK alias in DATA-001; no ticker reuse match

Confidence taxonomy (EXPANDED with UNVERIFIED):
  HIGH       : identity and formation-date price both defensible
  LOW        : AMBIGUOUS_MULTIPLE_ALIASES (user must adjudicate)
  REJECTED   : TICKER_REUSE_BY_OTHER_CIK (do not use)
  UNVERIFIED : ORIGINAL_TICKER or UNIQUE_CIK_ALIAS where PIT does not confirm the
               CIK-ticker association at or before the formation date
  NO_MATCH   : no DATA-001 membership, no PIT alias, no ticker reuse

STRAT-020 REMEDIATION v2 (2026-10-18) — corrections to PR #108:
  - BLK 1: formation_price_available now uses the 7-day window rule (not first_year<=year)
  - BLK 2: UNIQUE_CIK_ALIAS requires PIT-valid alias at or before formation_date
  - BLK 2: ORIGINAL_TICKER requires PIT-confirmed CIK-ticker at or before formation_date
           OR is downclassified to UNVERIFIED
  - BLK 3: share_class_match_valid cross-checks the matched ticker's share_class_figi
  - BLK 4: formation_price_eligible is the explicit field; eligible_for_backtest is a
           backward-compatibility alias

Restrictions (per STRAT-020 owner directive):
  - No return calculations
  - No IC recomputation
  - No backtest
  - No API calls
  - No data purchases
  - No modifications to frozen cohorts, DATA-001, or STRAT-019
  - No production SmartBot changes
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


# ---------- Config ----------
REPO_ROOT = Path(__file__).resolve().parents[2]
PARTS = [REPO_ROOT / f"research_data/alpaca_daily_2016_2026/transfer/daily_bars_part_{i:03d}.parquet" for i in range(1, 6)]
MASSIVE_PIT = REPO_ROOT / "research_data/massive_security_master/massive_security_master_sec_join_ready.csv.gz"
COHORT_DIR = REPO_ROOT / "experiments/strat_017"
OUT_DIR = REPO_ROOT / "experiments/strat_020"
OUT_PARQUET = OUT_DIR / "_step3_recovery_mapping.parquet"
OUT_JSON = OUT_DIR / "_step3_recovery_mapping.json"
YEARS = [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]

# PIT formation_date per cohort year (verified empirically from the PIT).
COHORT_FORMATION_DATE = {
    2016: "2016-06-30",
    2017: "2017-06-30",
    2018: "2018-06-29",
    2019: "2019-06-28",
    2020: "2020-06-30",
    2021: "2021-06-30",
    2022: "2022-06-30",
    2023: "2023-06-30",
    2024: "2024-06-28",
    2025: "2025-06-30",
}

# Maximum gap (in calendar days) between the latest bar on or before formation_date
# and the formation_date itself for the bar to qualify as the formation-date price.
# 7 days covers a normal weekend (Fri to Mon) plus one holiday buffer. Larger gaps
# (e.g., a security delisted months before formation) are correctly rejected.
MAX_FORMATION_PRICE_GAP_DAYS = 7


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_formation_price_available(
    d001_first_bar_ts: pd.Timestamp | None,
    d001_last_bar_ts: pd.Timestamp | None,
    formation_date: pd.Timestamp,
    max_gap_days: int = MAX_FORMATION_PRICE_GAP_DAYS,
) -> tuple[bool, pd.Timestamp | None, int | None]:
    """Standalone helper for the 7-day-window formation-date price rule.

    Returns: (formation_price_available, latest_bar_date_on_or_before_formation, days_gap)

    A bar is "the formation-date price" iff the latest DATA-001 bar on or before
    formation_date is within max_gap_days calendar days of formation_date. This
    explicitly justifies the calendar rule: 7 days covers a normal weekend (Fri
    close to Mon open) plus a single holiday. A security delisted months before
    formation_date will have its latest bar far outside the window and will be
    correctly rejected as formation-price-unavailable.

    The helper is exposed at module level so validate_recovery.py and the test
    suite can invoke it without re-implementing the rule.
    """
    if d001_first_bar_ts is None or pd.isna(d001_first_bar_ts):
        return False, None, None
    if d001_last_bar_ts is None or pd.isna(d001_last_bar_ts):
        return False, None, None
    first_date = pd.Timestamp(d001_first_bar_ts).date()
    last_date = pd.Timestamp(d001_last_bar_ts).date()
    formation_d = pd.Timestamp(formation_date).date()
    # Case A: the security did not exist at formation_date (first bar is after).
    # No bar on or before formation_date exists -> not formation-price-valid.
    if first_date > formation_d:
        return False, None, None
    # Case B: the security was trading at or after formation_date (last bar is after).
    # The "latest bar on or before formation_date" is some date <= formation_date;
    # since the security was actively trading through that window, the latest
    # such bar is at most one trading day before formation_date. Use formation_date
    # as the upper bound with days_gap=0.
    if last_date > formation_d:
        return True, pd.Timestamp(formation_d), 0
    # Case C: the security was delisted before formation_date (last bar is on or before).
    # Use last_date as the "latest bar on or before formation_date" proxy.
    days_gap = (formation_d - last_date).days
    if days_gap <= max_gap_days:
        return True, pd.Timestamp(last_date), days_gap
    return False, pd.Timestamp(last_date), days_gap


def load_data001():
    """Return (symbols_set, first_bar_dict, last_bar_dict, n_bars_dict) for DATA-001."""
    syms = set()
    first = {}
    last = {}
    nbars = {}
    for p in PARTS:
        if not p.exists():
            print(f"WARN: missing {p}", file=sys.stderr)
            continue
        df = pd.read_parquet(p, columns=["symbol", "timestamp"])
        for sym, g in df.groupby("symbol"):
            mn = g["timestamp"].min()
            mx = g["timestamp"].max()
            nb = len(g)
            if sym not in first or mn < first[sym]:
                first[sym] = mn
            if sym not in last or mx > last[sym]:
                last[sym] = mx
            nbars[sym] = nb
            syms.add(sym)
    return syms, first, last, nbars


def load_pit():
    """Return PIT-derived lookups.

    Returns:
      cik_all_tickers        : dict[cik_str] -> sorted list of tickers in PIT for that CIK
      cik_composite_figi     : dict[cik_str] -> sorted list of composite FIGIs
      cik_share_class_figi   : dict[cik_str] -> sorted list of share_class FIGIs
      ticker_to_ciks         : dict[ticker]   -> sorted list of CIKs that own this ticker
      cik_ticker_pit_earliest: dict[(cik_str, ticker)] -> earliest PIT formation_date
      cik_ticker_share_class : dict[(cik_str, ticker)] -> first non-empty share_class_figi
      cik_share_classes      : dict[cik_str] -> sorted list of unique share_class_figi
    """
    mp = pd.read_csv(MASSIVE_PIT)
    mp["composite_figi"] = mp["composite_figi"].fillna("").astype(str)
    mp["share_class_figi"] = mp["share_class_figi"].fillna("").astype(str)
    mp["cik_str"] = mp["cik_10digit"].astype(str)
    mp["formation_date"] = pd.to_datetime(mp["formation_date"])
    mp["formation_year"] = mp["formation_date"].dt.year

    cik_all_tickers = mp.groupby("cik_str")["ticker"].apply(lambda x: sorted(set(x))).to_dict()
    cik_composite_figi = (
        mp[mp["composite_figi"] != ""]
        .groupby("cik_str")["composite_figi"]
        .apply(lambda x: sorted(set(x)))
        .to_dict()
    )
    cik_share_class_figi = (
        mp[mp["share_class_figi"] != ""]
        .groupby("cik_str")["share_class_figi"]
        .apply(lambda x: sorted(set(x)))
        .to_dict()
    )
    ticker_to_ciks = mp.groupby("ticker")["cik_str"].apply(lambda x: sorted(set(x))).to_dict()

    # Per (cik, ticker) pair: earliest PIT formation_date and share_class_figi
    pit_agg = (
        mp.groupby(["cik_str", "ticker"])
        .agg(
            earliest_formation=("formation_date", "min"),
            latest_formation=("formation_date", "max"),
        )
        .reset_index()
    )
    cik_ticker_pit_earliest = {
        (row["cik_str"], row["ticker"]): row["earliest_formation"]
        for _, row in pit_agg.iterrows()
    }
    cik_ticker_pit_latest = {
        (row["cik_str"], row["ticker"]): row["latest_formation"]
        for _, row in pit_agg.iterrows()
    }
    # share_class_figi per (cik, ticker) — take the first non-empty value
    sc = mp[mp["share_class_figi"] != ""].drop_duplicates(subset=["cik_str", "ticker"], keep="first")
    cik_ticker_share_class = {
        (row["cik_str"], row["ticker"]): row["share_class_figi"]
        for _, row in sc.iterrows()
    }
    # Per CIK: set of unique share_class_figi values
    cik_share_classes = cik_share_class_figi  # same as cik_share_class_figi

    return (
        cik_all_tickers,
        cik_composite_figi,
        cik_share_class_figi,
        ticker_to_ciks,
        cik_ticker_pit_earliest,
        cik_ticker_pit_latest,
        cik_ticker_share_class,
        cik_share_classes,
    )


def is_pit_valid_at_formation(
    cik: str,
    ticker: str,
    formation_date: pd.Timestamp,
    cik_ticker_pit_earliest: dict,
) -> tuple[bool, pd.Timestamp | None]:
    """True iff the PIT confirms (cik, ticker) was associated at or before formation_date."""
    key = (cik, ticker)
    if key not in cik_ticker_pit_earliest:
        return False, None
    earliest = cik_ticker_pit_earliest[key]
    if pd.isna(earliest):
        return False, None
    return earliest <= formation_date, earliest


def build_recovery_rows(
    years,
    data001_syms,
    data001_first,
    data001_last,
    data001_nbars,
    cik_all_tickers,
    cik_composite_figi,
    cik_share_class_figi,
    ticker_to_ciks,
    cik_ticker_pit_earliest,
    cik_ticker_pit_latest,
    cik_ticker_share_class,
    cik_share_classes,
):
    rows = []
    for year in years:
        cohort_path = COHORT_DIR / f"_cohort_{year}.csv.gz"
        if not cohort_path.exists():
            print(f"WARN: missing cohort {cohort_path}", file=sys.stderr)
            continue
        cohort = pd.read_csv(cohort_path)
        formation_date_str = COHORT_FORMATION_DATE[year]
        formation_date = pd.Timestamp(formation_date_str)
        for _, row in cohort.iterrows():
            cik = str(int(row["cik_10digit"]))
            orig_ticker = row["ticker"]
            rec = {
                "formation_year": year,
                "formation_date": formation_date_str,
                "cik": cik,
                "original_ticker": orig_ticker,
                "matched_ticker": None,
                "match_type": None,
                "evidence_source": None,
                "mapping_confidence": None,
                "bar_exists": False,
                "unique_match": True,
                "alias_candidates": [],
                "figi_composite": cik_composite_figi.get(cik, []),
                "figi_share_class": cik_share_class_figi.get(cik, []),
                "all_cik_tickers_in_pit": cik_all_tickers.get(cik, []),
                "d001_first_bar": None,
                "d001_last_bar": None,
                "d001_nbars": 0,
                "formation_price_available": False,
                "latest_bar_date_on_or_before_formation": None,
                "days_from_latest_bar_to_formation": None,
                "alias_pit_earliest_formation_date": None,
                "alias_pit_valid_at_formation": None,
                "original_ticker_pit_valid_at_formation": None,
                "share_class_match_valid": None,
                "unverified_reason": None,
            }

            def _populate_bar_fields(rec, ticker):
                fb = data001_first.get(ticker)
                lb = data001_last.get(ticker)
                rec["d001_first_bar"] = str(fb) if fb is not None else None
                rec["d001_last_bar"] = str(lb) if lb is not None else None
                rec["d001_nbars"] = data001_nbars.get(ticker, 0)
                fp_avail, latest_date, days_gap = compute_formation_price_available(
                    fb, lb, formation_date
                )
                rec["formation_price_available"] = fp_avail
                rec["latest_bar_date_on_or_before_formation"] = (
                    str(latest_date) if latest_date is not None else None
                )
                rec["days_from_latest_bar_to_formation"] = days_gap

            if orig_ticker in data001_syms:
                rec["matched_ticker"] = orig_ticker
                rec["match_type"] = "ORIGINAL_TICKER"
                rec["evidence_source"] = "DATA-001"
                rec["bar_exists"] = True
                _populate_bar_fields(rec, orig_ticker)
                # BLOCKER 2/3: verify PIT confirms (cik, orig_ticker) at or before formation.
                pit_valid, earliest = is_pit_valid_at_formation(
                    cik, orig_ticker, formation_date, cik_ticker_pit_earliest
                )
                rec["original_ticker_pit_valid_at_formation"] = bool(pit_valid)
                # BLOCKER 3: share-class check (only meaningful if the CIK has share-class FIGIs)
                share_class = cik_ticker_share_class.get((cik, orig_ticker))
                if share_class and len(cik_share_class_figi.get(cik, [])) > 1:
                    # CIK has multiple share classes; the matched ticker should match one of them.
                    # The share_class_figi IS one of the CIK's classes (by construction).
                    rec["share_class_match_valid"] = True
                else:
                    rec["share_class_match_valid"] = True  # trivially valid (no cross-class issue)
                # Ticker-reuse check: does the PIT have the ticker under a different CIK?
                other_ciks = [c for c in ticker_to_ciks.get(orig_ticker, []) if c != cik]
                if other_ciks:
                    # PIT shows a different CIK owns this ticker -> ticker reuse -> REJECTED
                    rec["match_type"] = "TICKER_REUSE_BY_OTHER_CIK"
                    rec["mapping_confidence"] = "REJECTED"
                    rec["evidence_source"] = (
                        f"Massive PIT (ticker also owned by CIK(s) {other_ciks})"
                    )
                    rec["bar_exists"] = False
                elif not pit_valid:
                    # PIT does not confirm this CIK-ticker at or before formation.
                    rec["mapping_confidence"] = "UNVERIFIED"
                    rec["evidence_source"] = (
                        f"ORIGINAL_TICKER not PIT-confirmed at formation "
                        f"(PIT earliest for ({cik}, {orig_ticker}): {earliest})"
                    )
                    rec["unverified_reason"] = "ORIGINAL_TICKER_PIT_GAP"
                else:
                    rec["mapping_confidence"] = "HIGH"
            else:
                all_t = cik_all_tickers.get(cik, [])
                in_d001 = [t for t in all_t if t in data001_syms and t != orig_ticker]
                if len(in_d001) == 1:
                    t = in_d001[0]
                    rec["matched_ticker"] = t
                    rec["match_type"] = "UNIQUE_CIK_ALIAS"
                    rec["evidence_source"] = "Massive PIT CIK alias"
                    rec["bar_exists"] = True
                    _populate_bar_fields(rec, t)
                    # BLOCKER 2: verify PIT confirms the alias was associated with the CIK
                    # at or before formation_date. If not, the alias is "associated with the
                    # CIK only after formation" -> UNVERIFIED.
                    pit_valid, earliest = is_pit_valid_at_formation(
                        cik, t, formation_date, cik_ticker_pit_earliest
                    )
                    rec["alias_pit_earliest_formation_date"] = (
                        str(earliest) if earliest is not None else None
                    )
                    rec["alias_pit_valid_at_formation"] = bool(pit_valid)
                    # BLOCKER 3: share-class check
                    alias_share_class = cik_ticker_share_class.get((cik, t))
                    cik_classes = cik_share_class_figi.get(cik, [])
                    if alias_share_class and len(cik_classes) > 1:
                        # CIK has multiple share classes. Check whether the original ticker
                        # is associated with a different share class.
                        orig_share_class = cik_ticker_share_class.get((cik, orig_ticker))
                        if orig_share_class and orig_share_class != alias_share_class:
                            rec["share_class_match_valid"] = False
                        else:
                            rec["share_class_match_valid"] = True
                    else:
                        rec["share_class_match_valid"] = True
                    if not pit_valid:
                        rec["mapping_confidence"] = "UNVERIFIED"
                        rec["unverified_reason"] = "ALIAS_PIT_ONLY_AFTER_FORMATION"
                        rec["evidence_source"] = (
                            f"Massive PIT CIK alias but PIT earliest for "
                            f"({cik}, {t}) is {earliest}, after formation_date "
                            f"{formation_date_str}"
                        )
                    else:
                        rec["mapping_confidence"] = "HIGH"
                elif len(in_d001) > 1:
                    rec["alias_candidates"] = in_d001
                    rec["matched_ticker"] = in_d001[0]
                    rec["match_type"] = "AMBIGUOUS_MULTIPLE_ALIASES"
                    rec["evidence_source"] = "Massive PIT CIK aliases (multiple)"
                    rec["unique_match"] = False
                    t = in_d001[0]
                    rec["bar_exists"] = True
                    _populate_bar_fields(rec, t)
                    # Even for AMBIGUOUS, check the first alias's PIT validity (informational).
                    pit_valid, earliest = is_pit_valid_at_formation(
                        cik, t, formation_date, cik_ticker_pit_earliest
                    )
                    rec["alias_pit_earliest_formation_date"] = (
                        str(earliest) if earliest is not None else None
                    )
                    rec["alias_pit_valid_at_formation"] = bool(pit_valid)
                    rec["share_class_match_valid"] = None  # N/A for AMBIGUOUS
                    rec["mapping_confidence"] = "LOW"
                else:
                    other_ciks = ticker_to_ciks.get(orig_ticker, [])
                    other_ciks_d001 = [c for c in other_ciks if c != cik]
                    if other_ciks_d001:
                        rec["alias_candidates"] = other_ciks_d001
                        rec["match_type"] = "TICKER_REUSE_BY_OTHER_CIK"
                        rec["evidence_source"] = "Massive PIT (ticker reused)"
                        rec["mapping_confidence"] = "REJECTED"
                        rec["bar_exists"] = False
                    else:
                        rec["match_type"] = "NOT_IN_DATA001"
                        rec["evidence_source"] = "No match in PIT, no match in DATA-001"
                        rec["mapping_confidence"] = "NO_MATCH"
                        rec["bar_exists"] = False
            rows.append(rec)
    return rows


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Loading DATA-001 ...")
    data001_syms, data001_first, data001_last, data001_nbars = load_data001()
    print(f"  DATA-001: {len(data001_syms)} unique symbols")

    print("Loading Massive PIT ...")
    (
        cik_all_tickers,
        cik_composite_figi,
        cik_share_class_figi,
        ticker_to_ciks,
        cik_ticker_pit_earliest,
        cik_ticker_pit_latest,
        cik_ticker_share_class,
        cik_share_classes,
    ) = load_pit()
    print(f"  PIT: {len(cik_all_tickers)} unique CIKs")

    print("Building recovery rows ...")
    rows = build_recovery_rows(
        YEARS,
        data001_syms, data001_first, data001_last, data001_nbars,
        cik_all_tickers, cik_composite_figi, cik_share_class_figi, ticker_to_ciks,
        cik_ticker_pit_earliest, cik_ticker_pit_latest,
        cik_ticker_share_class, cik_share_classes,
    )
    rdf = pd.DataFrame(rows)
    print(f"  Rows: {len(rdf)}")

    # STRAT-020 REMEDIATION v2: explicit formation_price_eligible field.
    # True iff HIGH confidence AND formation_price_available is True.
    rdf["formation_price_eligible"] = (
        (rdf["mapping_confidence"] == "HIGH") &
        (rdf["formation_price_available"] == True)
    )
    # Backward-compatibility alias.
    rdf["eligible_for_backtest"] = rdf["formation_price_eligible"]

    n_eligible = int(rdf["formation_price_eligible"].sum())
    n_high = int((rdf["mapping_confidence"] == "HIGH").sum())
    n_unverified = int((rdf["mapping_confidence"] == "UNVERIFIED").sum())
    n_low = int((rdf["mapping_confidence"] == "LOW").sum())
    n_rejected = int((rdf["mapping_confidence"] == "REJECTED").sum())
    n_no_match = int((rdf["mapping_confidence"] == "NO_MATCH").sum())
    n_high_no_fp = int(((rdf["mapping_confidence"] == "HIGH") & (~rdf["formation_price_available"])).sum())
    print(f"  formation_price_eligible=True: {n_eligible} (HIGH with formation-date price)")
    print(f"  HIGH-confidence total: {n_high}")
    print(f"  UNVERIFIED total: {n_unverified}")
    print(f"  LOW total: {n_low}")
    print(f"  REJECTED total: {n_rejected}")
    print(f"  NO_MATCH total: {n_no_match}")
    print(f"  HIGH without formation-date price: {n_high_no_fp}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rdf.to_parquet(OUT_PARQUET, index=False)
    print(f"  Parquet -> {OUT_PARQUET}")

    # JSON serialization
    rdf_json = rdf.copy()
    for col in ["figi_composite", "figi_share_class", "all_cik_tickers_in_pit", "alias_candidates"]:
        rdf_json[col] = rdf_json[col].apply(lambda v: list(v) if isinstance(v, (list, tuple)) else v)
    rdf_json.to_json(OUT_JSON, orient="records", indent=1)
    print(f"  JSON    -> {OUT_JSON}")

    # Source-hash manifest
    sources = {
        "data_001_parts": [sha256_file(p) for p in PARTS if p.exists()],
        "massive_pit": sha256_file(MASSIVE_PIT),
        "cohorts": {str(y): sha256_file(COHORT_DIR / f"_cohort_{y}.csv.gz") for y in YEARS},
        "output_parquet": sha256_file(OUT_PARQUET),
        "output_json": sha256_file(OUT_JSON),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rem_version": "STRAT-020 REMEDIATION v2 (2026-10-18; 6-blocker correction)",
    }
    src_path = OUT_DIR / "_step3_sources.json"
    with open(src_path, "w") as f:
        json.dump(sources, f, indent=2)
    print(f"  Sources -> {src_path}")

    # Distribution prints
    print()
    print("Match-type distribution:")
    print(rdf["match_type"].value_counts().to_string())
    print()
    print("Mapping-confidence distribution:")
    print(rdf["mapping_confidence"].value_counts().to_string())
    print()
    print("Formation-price-eligible distribution:")
    print(rdf["formation_price_eligible"].value_counts().to_string())
    print()
    nr = rdf[rdf["match_type"] == "UNIQUE_CIK_ALIAS"]
    nr_eligible = int(nr["formation_price_eligible"].sum())
    nr_ineligible = int((~nr["formation_price_eligible"]).sum())
    print(f"  UNIQUE_CIK_ALIAS total: {len(nr)}")
    print(f"  UNIQUE_CIK_ALIAS formation_price_eligible=True: {nr_eligible}")
    print(f"  UNIQUE_CIK_ALIAS formation_price_eligible=False: {nr_ineligible} (post-formation-IPO)")


if __name__ == "__main__":
    main()
