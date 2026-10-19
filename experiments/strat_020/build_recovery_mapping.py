"""
Build the historical identity recovery mapping for STRAT-017 cohort members.

Inputs (read-only, frozen):
  - experiments/strat_017/_cohort_<year>.csv.gz  (frozen cohort; 10 files for 2016-2025)
  - research_data/alpaca_daily_2016_2026/transfer/daily_bars_part_001..005.parquet  (DATA-001)
  - research_data/massive_security_master/massive_security_master_sec_join_ready.csv.gz  (Massive PIT)
  - experiments/strat_020/_authoritative_frozen_baseline.json  (independent SHA-256 baseline)

Outputs (new, additive; overwrites previous remediation outputs):
  - experiments/strat_020/_step3_recovery_mapping.parquet
  - experiments/strat_020/_step3_recovery_mapping.json
  - experiments/strat_020/_step3_sources.json  (current-run SHA-256 manifest)

Match-type taxonomy (unchanged):
  ORIGINAL_TICKER            : original STRAT-017 ticker is in DATA-001
  UNIQUE_CIK_ALIAS           : original ticker absent but exactly one alternate ticker for the same CIK is in DATA-001
  AMBIGUOUS_MULTIPLE_ALIASES : multiple alternate tickers for the same CIK are in DATA-001
  TICKER_REUSE_BY_OTHER_CIK  : original ticker is mapped to a different CIK in PIT
  NOT_IN_DATA001             : no CIK alias in DATA-001; no ticker reuse match

Confidence taxonomy (EXPANDED with UNVERIFIED, security_continuity):
  HIGH       : identity and formation-date price both defensible
  LOW        : AMBIGUOUS_MULTIPLE_ALIASES (user must adjudicate)
  REJECTED   : TICKER_REUSE_BY_OTHER_CIK (do not use)
  UNVERIFIED : ORIGINAL_TICKER or UNIQUE_CIK_ALIAS where PIT does not confirm the
               CIK-ticker association at or before the formation date
  NO_MATCH   : no DATA-001 membership, no PIT alias, no ticker reuse

Security-continuity taxonomy (NEW v4 — Blocker 2):
  VERIFIED_SAME_SECURITY       : matched ticker's PIT share_class_figi matches the
                                  CIK's primary share class, OR the CIK has a single
                                  share class
  VERIFIED_CONFLICTING_SECURITY: matched ticker's PIT share_class_figi conflicts
                                  with the original ticker's share class (or with
                                  other CIK share classes)
  UNVERIFIED_CONTINUITY        : missing metadata (PIT has no share_class_figi for
                                  the matched ticker, or the CIK has no share class info)
  AMBIGUOUS_IDENTITY           : multiple aliases exist; cannot programmatically
                                  determine which is the same security

STRAT-020 REMEDIATION v4 (2026-10-19) — corrections per owner re-review msg #8732:
  - BLK 1: ACTUAL formation-date bar lookup (not inferred from first/last timestamps).
           New fields: exact_formation_session_available, actual_formation_bar_timestamp,
           stale_prior_session_available (descriptive 7-day window), latest_prior_session_bar_date.
           The 7-day window is now DESCRIPTIVE METADATA ONLY and may not establish
           exact formation-session eligibility.
  - BLK 2: VERIFIED/UNVERIFIED/AMBIGUOUS identity continuity classification
           using existing PIT share_class_figi and composite_figi identifiers.
           Missing share-class FIGI is NOT auto-accepted as a match.
           ORIGINAL_TICKER path reviewed for ticker reuse and conflicting ownership.
  - BLK 3: Authoritative frozen-source baseline (independent of the build script)
           used for source-integrity verification. Build script's _step3_sources.json
           is for current-run record-keeping only.
  - BLK 4: Complete 9-item negative-test acceptance criteria (AC1-AC9).

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
AUTHORITATIVE_BASELINE = OUT_DIR / "_authoritative_frozen_baseline.json"
YEARS = [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]

# Official frozen June formation trading session per cohort year.
# These are the independently-determined formation dates (NOT the latest available
# bar date). They are derived from the frozen STRAT-017 cohort definition.
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

# Maximum gap (in calendar days) for the DESCRIPTIVE 7-day freshness calculation.
# This is metadata only and may NOT establish exact formation-session eligibility.
MAX_PRIOR_SESSION_GAP_DAYS = 7


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================
# BLOCKER 1 — ACTUAL formation-date bar lookup
# ============================================================

def load_data001_calendar():
    """Return (symbols_set, first_bar_dict, last_bar_dict, n_bars_dict, formation_date_lookup).

    The formation_date_lookup is the key new structure for Blocker 1: it is
    a dict[formation_date_str -> dict[symbol -> timestamp]] that contains ONLY
    the bars on the 10 frozen June formation dates. This is much smaller than
    the full calendar (~30K entries vs 3.9M) and allows exact formation-date
    bar lookup (NOT inferred from first/last).

    For the descriptive 7-day prior-session freshness, we also return a per-symbol
    sorted-date list.
    """
    syms = set()
    first = {}
    last = {}
    nbars = {}
    # Per-symbol sorted date list (for prior-session lookup, descriptive only)
    sym_dates = {}  # symbol -> sorted list of dates
    # formation_date_str -> {symbol -> timestamp}
    formation_lookups = {d: {} for d in COHORT_FORMATION_DATE.values()}
    # Also build a per-symbol {date -> timestamp} dict ONLY for dates near formation dates
    # to support prior-session lookup. This is much smaller than the full calendar.
    # We need: for each symbol, the latest bar on or before each formation_date.
    # We can build: sym -> {formation_date_str -> latest_prior_bar_date_or_None}
    # by checking bars within a 30-day window before each formation_date.

    target_dates_set = set()
    target_dates_list = []
    date_to_fdstr = {}  # date object -> formation_date_str (e.g. "2016-06-30")
    for y, d in COHORT_FORMATION_DATE.items():
        d_obj = pd.Timestamp(d).date()
        target_dates_set.add(d_obj)
        target_dates_list.append(d_obj)
        date_to_fdstr[d_obj] = d
    target_dates_list.sort()

    for p in PARTS:
        if not p.exists():
            print(f"WARN: missing {p}", file=sys.stderr)
            continue
        df = pd.read_parquet(p, columns=["symbol", "timestamp"])
        df["_date"] = pd.to_datetime(df["timestamp"]).dt.date

        # Vectorized aggregations
        g_agg = df.groupby("symbol")["timestamp"].agg(["min", "max", "count"])
        for sym, row in g_agg.iterrows():
            mn = row["min"]
            mx = row["max"]
            nb = int(row["count"])
            if sym not in first or mn < first[sym]:
                first[sym] = mn
            if sym not in last or mx > last[sym]:
                last[sym] = mx
            nbars[sym] = nb
            syms.add(sym)

        # For each symbol, find bars on the 10 formation dates
        # This is much smaller than the full calendar
        formation_bars = df[df["_date"].isin(target_dates_set)]
        for sym, g in formation_bars.groupby("symbol"):
            for _, row in g.iterrows():
                d = row["_date"]
                fd_str = date_to_fdstr.get(d)
                if fd_str is not None:
                    formation_lookups[fd_str][sym] = row["timestamp"]

        # For prior-session lookup, build per-symbol sorted date list
        # (limited to dates within 30 days of any formation date for efficiency)
        # Actually for simplicity, we use the full date set
        sym_date_lists = df.groupby("symbol")["_date"].apply(lambda x: sorted(x.unique()))
        for sym, dates in sym_date_lists.items():
            if sym not in sym_dates:
                sym_dates[sym] = dates
            else:
                # Merge (shouldn't happen since each symbol is in one part)
                sym_dates[sym] = sorted(set(sym_dates[sym]) | set(dates))

    return syms, first, last, nbars, formation_lookups, sym_dates


def compute_actual_formation_session(
    ticker: str,
    formation_date_str: str,
    formation_lookups: dict,
    sym_dates: dict,
) -> tuple[bool, pd.Timestamp | None, pd.Timestamp | None, int | None]:
    """BLOCKER 1: Look up the ACTUAL DATA-001 bar for the formation session.

    Returns: (exact_formation_session_available, actual_formation_bar_timestamp,
              latest_prior_session_bar_date, days_from_latest_prior_to_formation)

    A bar is "the exact formation session" iff a DATA-001 bar exists for the
    matched security on the exact calendar date of formation_date. This is a
    direct lookup, NOT an inference from first/last timestamps.

    formation_lookups: dict[formation_date_str -> dict[symbol -> timestamp]]
    sym_dates: dict[symbol -> sorted list of dates]
    """
    ts_map = formation_lookups.get(formation_date_str, {})
    if ticker in ts_map:
        # EXACT formation-session bar exists
        return True, ts_map[ticker], None, 0
    # Find the latest prior bar (descriptive, for stale_prior_session_available)
    dates = sym_dates.get(ticker, [])
    if not dates:
        return False, None, None, None
    formation_d = pd.Timestamp(formation_date_str).date()
    import bisect
    idx = bisect.bisect_left(dates, formation_d)
    if idx == 0:
        return False, None, None, None
    latest_prior = dates[idx - 1]
    days_gap = (formation_d - latest_prior).days
    if days_gap <= MAX_PRIOR_SESSION_GAP_DAYS:
        return False, None, latest_prior, days_gap
    return False, None, latest_prior, days_gap


# Backward-compatibility helper for v2 7-day window (descriptive metadata only).
# NOTE: This helper uses first/last timestamp inference, which is logically
# invalid for exact-session eligibility. It is retained only for descriptive
# 7-day freshness reporting. DO NOT use for eligibility.
def compute_prior_session_freshness(
    d001_first_bar_ts: pd.Timestamp | None,
    d001_last_bar_ts: pd.Timestamp | None,
    formation_date: pd.Timestamp,
    max_gap_days: int = MAX_PRIOR_SESSION_GAP_DAYS,
) -> tuple[bool, pd.Timestamp | None, int | None]:
    """DESCRIPTIVE 7-day window freshness check (Blocker 1: metadata only).

    Returns: (prior_session_fresh, latest_bar_date_on_or_before_formation, days_gap)
    A stale_prior_session is True iff the latest DATA-001 bar on or before
    formation_date is within max_gap_days calendar days of formation_date.
    """
    if d001_first_bar_ts is None or pd.isna(d001_first_bar_ts):
        return False, None, None
    if d001_last_bar_ts is None or pd.isna(d001_last_bar_ts):
        return False, None, None
    first_date = pd.Timestamp(d001_first_bar_ts).date()
    last_date = pd.Timestamp(d001_last_bar_ts).date()
    formation_d = pd.Timestamp(formation_date).date()
    if first_date > formation_d:
        return False, None, None
    if last_date > formation_d:
        return True, pd.Timestamp(formation_d), 0
    days_gap = (formation_d - last_date).days
    if days_gap <= max_gap_days:
        return True, pd.Timestamp(last_date), days_gap
    return False, pd.Timestamp(last_date), days_gap


# Backward-compat alias for v2 tests (same behavior as compute_prior_session_freshness
# when last_date > formation_d returns True with days_gap=0, which is the
# v2 "Case B" inference that v4 corrects via actual bar lookup).
def compute_formation_price_available(
    d001_first_bar_ts: pd.Timestamp | None,
    d001_last_bar_ts: pd.Timestamp | None,
    formation_date: pd.Timestamp,
    max_gap_days: int = MAX_PRIOR_SESSION_GAP_DAYS,
) -> tuple[bool, pd.Timestamp | None, int | None]:
    """DEPRECATED v2 helper. Use compute_actual_formation_session instead.

    Retained for backward compatibility with v2 tests. The v2 logic is
    logically invalid for exact-session eligibility (Case B inference)
    and should not be used for new code.
    """
    return compute_prior_session_freshness(
        d001_first_bar_ts, d001_last_bar_ts, formation_date, max_gap_days
    )


# ============================================================
# BLOCKER 2 — Share-class and identity continuity
# ============================================================

def classify_security_continuity(
    cik: str,
    orig_ticker: str,
    matched_ticker: str,
    cik_share_class_figi: dict,
    cik_ticker_share_class: dict,
) -> tuple[str, str, str]:
    """BLOCKER 2: Classify the security continuity between the original ticker
    and the matched alias.

    Returns: (security_continuity, orig_share_class, alias_share_class)

    Classifications:
      - VERIFIED_SAME_SECURITY: matched ticker's share_class_figi is consistent
        with the CIK's share-class structure and the original ticker's share
        class (or no conflicting metadata).
      - VERIFIED_CONFLICTING_SECURITY: matched ticker's share_class_figi
        conflicts with the original ticker's share_class_figi (different classes).
      - UNVERIFIED_CONTINUITY: missing metadata (CIK has no share_class_figi,
        or matched ticker has no share_class_figi).
      - AMBIGUOUS_IDENTITY: N/A for this classifier (use match_type for that).

    The classification uses existing PIT identifiers and FIGIs only; no fabricated
    metadata. Missing share-class FIGI is NOT auto-accepted as a match.
    """
    cik_classes = cik_share_class_figi.get(cik, [])
    orig_sc = cik_ticker_share_class.get((cik, orig_ticker))
    alias_sc = cik_ticker_share_class.get((cik, matched_ticker))

    if not cik_classes:
        # CIK has no share-class FIGIs in PIT at all -> cannot verify continuity
        return "UNVERIFIED_CONTINUITY", orig_sc or "", alias_sc or ""

    if not alias_sc:
        # Matched ticker has no share_class_figi -> cannot verify continuity
        return "UNVERIFIED_CONTINUITY", orig_sc or "", ""

    if len(cik_classes) == 1:
        # CIK has only one share class; matched ticker's class must match it
        if alias_sc == cik_classes[0]:
            return "VERIFIED_SAME_SECURITY", orig_sc or "", alias_sc
        else:
            return "VERIFIED_CONFLICTING_SECURITY", orig_sc or "", alias_sc

    # CIK has multiple share classes
    if not orig_sc:
        # Original ticker has no share_class_figi; cannot compare
        # The matched alias has a class; verify it's one of the CIK's classes
        if alias_sc in cik_classes:
            return "UNVERIFIED_CONTINUITY", "", alias_sc
        else:
            return "VERIFIED_CONFLICTING_SECURITY", "", alias_sc

    # Both original and alias have share_class_figi; compare them
    if orig_sc == alias_sc:
        return "VERIFIED_SAME_SECURITY", orig_sc, alias_sc
    else:
        return "VERIFIED_CONFLICTING_SECURITY", orig_sc, alias_sc


# ============================================================
# PIT loading
# ============================================================

def load_pit():
    """Return PIT-derived lookups.

    Returns:
      cik_all_tickers        : dict[cik_str] -> sorted list of tickers in PIT for that CIK
      cik_composite_figi     : dict[cik_str] -> sorted list of composite FIGIs
      cik_share_class_figi   : dict[cik_str] -> sorted list of share_class FIGIs
      ticker_to_ciks         : dict[ticker]   -> sorted list of CIKs that own this ticker
      cik_ticker_pit_earliest: dict[(cik_str, ticker)] -> earliest PIT formation_date
      cik_ticker_pit_latest  : dict[(cik_str, ticker)] -> latest PIT formation_date
      cik_ticker_share_class : dict[(cik_str, ticker)] -> first non-empty share_class_figi
      cik_ticker_composite   : dict[(cik_str, ticker)] -> first non-empty composite_figi
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
    sc = mp[mp["share_class_figi"] != ""].drop_duplicates(subset=["cik_str", "ticker"], keep="first")
    cik_ticker_share_class = {
        (row["cik_str"], row["ticker"]): row["share_class_figi"]
        for _, row in sc.iterrows()
    }
    co = mp[mp["composite_figi"] != ""].drop_duplicates(subset=["cik_str", "ticker"], keep="first")
    cik_ticker_composite = {
        (row["cik_str"], row["ticker"]): row["composite_figi"]
        for _, row in co.iterrows()
    }
    cik_share_classes = cik_share_class_figi

    return (
        cik_all_tickers,
        cik_composite_figi,
        cik_share_class_figi,
        ticker_to_ciks,
        cik_ticker_pit_earliest,
        cik_ticker_pit_latest,
        cik_ticker_share_class,
        cik_ticker_composite,
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


# ============================================================
# Row builder
# ============================================================

def build_recovery_rows(
    years,
    data001_syms,
    data001_first,
    data001_last,
    data001_nbars,
    data001_formation_lookups,
    data001_sym_dates,
    cik_all_tickers,
    cik_composite_figi,
    cik_share_class_figi,
    ticker_to_ciks,
    cik_ticker_pit_earliest,
    cik_ticker_pit_latest,
    cik_ticker_share_class,
    cik_ticker_composite,
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
                # BLOCKER 1 NEW: actual formation-session lookup (NOT inferred)
                "exact_formation_session_available": False,
                "actual_formation_bar_timestamp": None,
                "stale_prior_session_available": False,
                "latest_prior_session_bar_date": None,
                "days_from_latest_prior_to_formation": None,
                # DESCRIPTIVE 7-day window (Blocker 1: metadata only)
                "formation_price_available": False,
                "latest_bar_date_on_or_before_formation": None,
                "days_from_latest_bar_to_formation": None,
                # PIT validity
                "alias_pit_earliest_formation_date": None,
                "alias_pit_valid_at_formation": None,
                "original_ticker_pit_valid_at_formation": None,
                # BLOCKER 2 NEW: security continuity classification
                "security_continuity": None,
                "orig_share_class_figi": None,
                "alias_share_class_figi": None,
                # BLOCKER 2: backward-compat share_class_match_valid
                "share_class_match_valid": None,
                "unverified_reason": None,
            }

            def _populate_bar_fields(rec, ticker):
                fb = data001_first.get(ticker)
                lb = data001_last.get(ticker)
                rec["d001_first_bar"] = str(fb) if fb is not None else None
                rec["d001_last_bar"] = str(lb) if lb is not None else None
                rec["d001_nbars"] = data001_nbars.get(ticker, 0)
                # BLOCKER 1: ACTUAL formation-session bar lookup
                exact_avail, actual_ts, latest_prior, prior_gap = compute_actual_formation_session(
                    ticker, formation_date_str, data001_formation_lookups, data001_sym_dates
                )
                rec["exact_formation_session_available"] = bool(exact_avail)
                rec["actual_formation_bar_timestamp"] = str(actual_ts) if actual_ts is not None else None
                rec["stale_prior_session_available"] = (latest_prior is not None and prior_gap is not None and prior_gap <= MAX_PRIOR_SESSION_GAP_DAYS)
                rec["latest_prior_session_bar_date"] = str(latest_prior) if latest_prior is not None else None
                rec["days_from_latest_prior_to_formation"] = prior_gap
                # DESCRIPTIVE 7-day window (metadata only, uses first/last inference)
                fp_avail, latest_date, days_gap = compute_prior_session_freshness(
                    fb, lb, formation_date
                )
                rec["formation_price_available"] = bool(fp_avail)
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
                # BLOCKER 2: classify security continuity
                continuity, orig_sc, alias_sc = classify_security_continuity(
                    cik, orig_ticker, orig_ticker,
                    cik_share_class_figi, cik_ticker_share_class,
                )
                rec["security_continuity"] = continuity
                rec["orig_share_class_figi"] = orig_sc or None
                rec["alias_share_class_figi"] = alias_sc or None
                rec["share_class_match_valid"] = (continuity in ("VERIFIED_SAME_SECURITY",))
                # BLOCKER 2: ORIGINAL_TICKER ticker-reuse check
                other_ciks = [c for c in ticker_to_ciks.get(orig_ticker, []) if c != cik]
                if other_ciks:
                    rec["match_type"] = "TICKER_REUSE_BY_OTHER_CIK"
                    rec["mapping_confidence"] = "REJECTED"
                    rec["evidence_source"] = (
                        f"Massive PIT (ticker also owned by CIK(s) {other_ciks})"
                    )
                    rec["bar_exists"] = False
                elif continuity == "VERIFIED_CONFLICTING_SECURITY":
                    rec["mapping_confidence"] = "REJECTED"
                    rec["evidence_source"] = (
                        f"ORIGINAL_TICKER share_class_figi conflicts with CIK share classes "
                        f"(orig={orig_sc}, alias={alias_sc})"
                    )
                    rec["unverified_reason"] = "ORIGINAL_TICKER_SHARE_CLASS_CONFLICT"
                    rec["bar_exists"] = False
                elif continuity == "UNVERIFIED_CONTINUITY":
                    rec["mapping_confidence"] = "UNVERIFIED"
                    rec["evidence_source"] = (
                        f"ORIGINAL_TICKER security continuity UNVERIFIED "
                        f"(missing share_class_figi metadata; orig={orig_sc}, alias={alias_sc})"
                    )
                    rec["unverified_reason"] = "ORIGINAL_TICKER_MISSING_SHARE_CLASS_METADATA"
                elif not pit_valid:
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
                    pit_valid, earliest = is_pit_valid_at_formation(
                        cik, t, formation_date, cik_ticker_pit_earliest
                    )
                    rec["alias_pit_earliest_formation_date"] = (
                        str(earliest) if earliest is not None else None
                    )
                    rec["alias_pit_valid_at_formation"] = bool(pit_valid)
                    # BLOCKER 2: classify security continuity
                    continuity, orig_sc, alias_sc = classify_security_continuity(
                        cik, orig_ticker, t,
                        cik_share_class_figi, cik_ticker_share_class,
                    )
                    rec["security_continuity"] = continuity
                    rec["orig_share_class_figi"] = orig_sc or None
                    rec["alias_share_class_figi"] = alias_sc or None
                    rec["share_class_match_valid"] = (continuity in ("VERIFIED_SAME_SECURITY",))
                    if continuity == "VERIFIED_CONFLICTING_SECURITY":
                        rec["mapping_confidence"] = "UNVERIFIED"
                        rec["evidence_source"] = (
                            f"Massive PIT CIK alias but share_class_figi conflicts "
                            f"(orig={orig_sc}, alias={alias_sc}); this is a cross-class match"
                        )
                        rec["unverified_reason"] = "ALIAS_SHARE_CLASS_CONFLICT"
                    elif continuity == "UNVERIFIED_CONTINUITY":
                        rec["mapping_confidence"] = "UNVERIFIED"
                        rec["evidence_source"] = (
                            f"Massive PIT CIK alias but security continuity UNVERIFIED "
                            f"(missing share_class_figi metadata; orig={orig_sc}, alias={alias_sc})"
                        )
                        rec["unverified_reason"] = "ALIAS_MISSING_SHARE_CLASS_METADATA"
                    elif not pit_valid:
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
                    rec["security_continuity"] = "AMBIGUOUS_IDENTITY"
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


# ============================================================
# BLOCKER 3 — Authoritative source integrity
# ============================================================

def compare_to_authoritative_baseline() -> dict:
    """BLOCKER 3: Compare current frozen-source SHA-256 to the independent
    authoritative baseline.

    Returns a dict with per-source status: MATCH, MISMATCH, or BASELINE UNAVAILABLE.
    The authoritative baseline is stored in `_authoritative_frozen_baseline.json`
    and was extracted from the first STRAT-020 commit (b4700b5). It is NOT
    regenerated by the build script.
    """
    result = {
        "baseline_file": str(AUTHORITATIVE_BASELINE),
        "baseline_available": AUTHORITATIVE_BASELINE.exists(),
        "data_001_parts": [],
        "massive_pit": {"status": "BASELINE UNAVAILABLE"},
        "cohorts": {},
    }
    if not AUTHORITATIVE_BASELINE.exists():
        for p in PARTS:
            if p.exists():
                result["data_001_parts"].append({
                    "file": str(p),
                    "status": "BASELINE UNAVAILABLE",
                    "current_sha256": sha256_file(p),
                })
        if MASSIVE_PIT.exists():
            result["massive_pit"] = {
                "file": str(MASSIVE_PIT),
                "status": "BASELINE UNAVAILABLE",
                "current_sha256": sha256_file(MASSIVE_PIT),
            }
        for y in YEARS:
            cohort_path = COHORT_DIR / f"_cohort_{y}.csv.gz"
            if cohort_path.exists():
                result["cohorts"][str(y)] = {
                    "file": str(cohort_path),
                    "status": "BASELINE UNAVAILABLE",
                    "current_sha256": sha256_file(cohort_path),
                }
        return result

    with open(AUTHORITATIVE_BASELINE) as f:
        baseline = json.load(f)

    # DATA-001 parts
    recorded_parts = baseline.get("data_001_parts", [])
    for i, p in enumerate(PARTS):
        if not p.exists():
            continue
        actual = sha256_file(p)
        recorded = recorded_parts[i] if i < len(recorded_parts) else None
        if recorded is None:
            status = "BASELINE UNAVAILABLE"
        elif actual == recorded:
            status = "MATCH"
        else:
            status = "MISMATCH"
        result["data_001_parts"].append({
            "file": str(p),
            "status": status,
            "baseline_sha256": recorded,
            "current_sha256": actual,
        })

    # Massive PIT
    if MASSIVE_PIT.exists():
        actual = sha256_file(MASSIVE_PIT)
        recorded = baseline.get("massive_pit")
        if recorded is None:
            status = "BASELINE UNAVAILABLE"
        elif actual == recorded:
            status = "MATCH"
        else:
            status = "MISMATCH"
        result["massive_pit"] = {
            "file": str(MASSIVE_PIT),
            "status": status,
            "baseline_sha256": recorded,
            "current_sha256": actual,
        }

    # Cohorts
    recorded_cohorts = baseline.get("cohorts", {})
    for y in YEARS:
        cohort_path = COHORT_DIR / f"_cohort_{y}.csv.gz"
        if not cohort_path.exists():
            continue
        actual = sha256_file(cohort_path)
        recorded = recorded_cohorts.get(str(y))
        if recorded is None:
            status = "BASELINE UNAVAILABLE"
        elif actual == recorded:
            status = "MATCH"
        else:
            status = "MISMATCH"
        result["cohorts"][str(y)] = {
            "file": str(cohort_path),
            "status": status,
            "baseline_sha256": recorded,
            "current_sha256": actual,
        }

    return result


# ============================================================
# Main
# ============================================================

def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Loading DATA-001 ...")
    data001_syms, data001_first, data001_last, data001_nbars, data001_formation_lookups, data001_sym_dates = load_data001_calendar()
    n_formation_bars = sum(len(v) for v in data001_formation_lookups.values())
    print(f"  DATA-001: {len(data001_syms)} unique symbols; {n_formation_bars} bars on the 10 formation dates")

    print("Loading Massive PIT ...")
    (
        cik_all_tickers,
        cik_composite_figi,
        cik_share_class_figi,
        ticker_to_ciks,
        cik_ticker_pit_earliest,
        cik_ticker_pit_latest,
        cik_ticker_share_class,
        cik_ticker_composite,
        cik_share_classes,
    ) = load_pit()
    print(f"  PIT: {len(cik_all_tickers)} unique CIKs")

    print("Building recovery rows ...")
    rows = build_recovery_rows(
        YEARS,
        data001_syms, data001_first, data001_last, data001_nbars,
        data001_formation_lookups, data001_sym_dates,
        cik_all_tickers, cik_composite_figi, cik_share_class_figi, ticker_to_ciks,
        cik_ticker_pit_earliest, cik_ticker_pit_latest,
        cik_ticker_share_class, cik_ticker_composite, cik_share_classes,
    )
    rdf = pd.DataFrame(rows)
    print(f"  Rows: {len(rdf)}")

    # STRAT-020 REMEDIATION v4: exact-formation-session eligibility
    # True iff HIGH confidence AND exact_formation_session_available is True.
    rdf["formation_price_eligible"] = (
        (rdf["mapping_confidence"] == "HIGH") &
        (rdf["exact_formation_session_available"] == True)
    )
    # Backward-compatibility alias.
    rdf["eligible_for_backtest"] = rdf["formation_price_eligible"]

    n_eligible = int(rdf["formation_price_eligible"].sum())
    n_high = int((rdf["mapping_confidence"] == "HIGH").sum())
    n_unverified = int((rdf["mapping_confidence"] == "UNVERIFIED").sum())
    n_low = int((rdf["mapping_confidence"] == "LOW").sum())
    n_rejected = int((rdf["mapping_confidence"] == "REJECTED").sum())
    n_no_match = int((rdf["mapping_confidence"] == "NO_MATCH").sum())
    n_exact_session = int(rdf["exact_formation_session_available"].sum())
    n_stale_prior = int(rdf["stale_prior_session_available"].sum())
    n_high_no_exact = int(((rdf["mapping_confidence"] == "HIGH") & (~rdf["exact_formation_session_available"])).sum())
    print(f"  formation_price_eligible=True: {n_eligible} (HIGH with EXACT formation-session bar)")
    print(f"  HIGH-confidence total: {n_high}")
    print(f"  exact_formation_session_available=True: {n_exact_session}")
    print(f"  stale_prior_session_available=True: {n_stale_prior} (descriptive; does NOT establish eligibility)")
    print(f"  HIGH without exact formation-session bar: {n_high_no_exact}")
    print(f"  UNVERIFIED total: {n_unverified}")
    print(f"  LOW total: {n_low}")
    print(f"  REJECTED total: {n_rejected}")
    print(f"  NO_MATCH total: {n_no_match}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rdf.to_parquet(OUT_PARQUET, index=False)
    print(f"  Parquet -> {OUT_PARQUET}")

    # JSON serialization
    rdf_json = rdf.copy()
    for col in ["figi_composite", "figi_share_class", "all_cik_tickers_in_pit", "alias_candidates"]:
        rdf_json[col] = rdf_json[col].apply(lambda v: list(v) if isinstance(v, (list, tuple)) else v)
    rdf_json.to_json(OUT_JSON, orient="records", indent=1)
    print(f"  JSON    -> {OUT_JSON}")

    # Current-run source-hash manifest (separate from authoritative baseline)
    sources = {
        "data_001_parts": [sha256_file(p) for p in PARTS if p.exists()],
        "massive_pit": sha256_file(MASSIVE_PIT),
        "cohorts": {str(y): sha256_file(COHORT_DIR / f"_cohort_{y}.csv.gz") for y in YEARS},
        "output_parquet": sha256_file(OUT_PARQUET),
        "output_json": sha256_file(OUT_JSON),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rem_version": "STRAT-020 REMEDIATION v4 (2026-10-19; actual-bar lookup + share-class continuity + authoritative baseline)",
    }
    src_path = OUT_DIR / "_step3_sources.json"
    with open(src_path, "w") as f:
        json.dump(sources, f, indent=2)
    print(f"  Sources -> {src_path}")

    # BLOCKER 3: authoritative baseline comparison
    print()
    print("=" * 70)
    print("BLOCKER 3 — AUTHORITATIVE SOURCE INTEGRITY")
    print("=" * 70)
    bl = compare_to_authoritative_baseline()
    bl_path = OUT_DIR / "_step3_authoritative_baseline_comparison.json"
    with open(bl_path, "w") as f:
        json.dump(bl, f, indent=2)
    print(f"  Baseline file: {bl['baseline_file']}")
    print(f"  Baseline available: {bl['baseline_available']}")
    n_match = 0
    n_mismatch = 0
    n_unavail = 0
    for part in bl["data_001_parts"]:
        print(f"  DATA-001 {Path(part['file']).name}: {part['status']}")
    mp_status = bl["massive_pit"].get("status", "BASELINE UNAVAILABLE")
    print(f"  Massive PIT: {mp_status}")
    for y_str, info in bl["cohorts"].items():
        print(f"  Cohort {y_str}: {info['status']}")
    # Summary
    for part in bl["data_001_parts"]:
        s = part["status"]
        if s == "MATCH": n_match += 1
        elif s == "MISMATCH": n_mismatch += 1
        else: n_unavail += 1
    if mp_status == "MATCH": n_match += 1
    elif mp_status == "MISMATCH": n_mismatch += 1
    else: n_unavail += 1
    for y_str, info in bl["cohorts"].items():
        s = info["status"]
        if s == "MATCH": n_match += 1
        elif s == "MISMATCH": n_mismatch += 1
        else: n_unavail += 1
    print(f"  Summary: {n_match} MATCH, {n_mismatch} MISMATCH, {n_unavail} BASELINE UNAVAILABLE")
    print(f"  -> {bl_path}")

    # Distribution prints
    print()
    print("Match-type distribution:")
    print(rdf["match_type"].value_counts().to_string())
    print()
    print("Mapping-confidence distribution:")
    print(rdf["mapping_confidence"].value_counts().to_string())
    print()
    print("Security-continuity distribution:")
    print(rdf["security_continuity"].value_counts().to_string())
    print()
    print("Formation-price-eligible distribution:")
    print(rdf["formation_price_eligible"].value_counts().to_string())
    print()
    print("Exact-formation-session distribution:")
    print(rdf["exact_formation_session_available"].value_counts().to_string())


if __name__ == "__main__":
    main()
