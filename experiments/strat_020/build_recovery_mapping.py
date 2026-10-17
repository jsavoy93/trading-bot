"""
Build the historical identity recovery mapping for STRAT-017 cohort members.

Inputs (read-only, frozen):
  - experiments/strat_017/_cohort_<year>.csv.gz  (frozen cohort; 10 files for 2016-2025)
  - research_data/alpaca_daily_2016_2026/transfer/daily_bars_part_001..005.parquet  (DATA-001; 13,489 unique symbols)
  - research_data/massive_security_master/massive_security_master_sec_join_ready.csv.gz  (Massive PIT; 11 formation dates)

Outputs (new, additive):
  - experiments/strat_020/_step3_recovery_mapping.parquet
  - experiments/strat_020/_step3_recovery_mapping.json

This script does NOT modify any frozen artifact. It reads them and produces
a derived dataset that records, for each cohort member, the matched
DATA-001 ticker (if any) and the provenance of the match.

Match-type taxonomy:
  ORIGINAL_TICKER            : original STRAT-017 ticker is in DATA-001 (HIGH confidence)
  UNIQUE_CIK_ALIAS           : original ticker absent but exactly one alternate ticker for the same CIK is in DATA-001 (HIGH)
  AMBIGUOUS_MULTIPLE_ALIASES : multiple alternate tickers for the same CIK are in DATA-001 (LOW; user must adjudicate)
  TICKER_REUSE_BY_OTHER_CIK  : original ticker is mapped to a different CIK in PIT (REJECTED; do not use)
  NOT_IN_DATA001             : no CIK alias in DATA-001; no ticker reuse match (NO_MATCH)

STRAT-020 REMEDIATION — added eligibility field:
  eligible_for_backtest     : boolean derived field (post-2026-10-15 remediation).
                             True iff the row qualifies as a successfully-recovered
                             formation-price observation usable in IC/return computation.
                             Concretely: mapping_confidence == HIGH AND formation_price_available == True.
                             Note: AMBIGUOUS rows are LOW confidence by definition and therefore
                             are NEVER eligible_for_backtest, even if the first alias happens to
                             have bars and a formation-date price. The strict semantic prevents
                             "first-alias coincidence" matches from leaking into the headline count.

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


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
    """Return per-CIK lookup dicts from Massive PIT sec_join_ready."""
    mp = pd.read_csv(MASSIVE_PIT)
    mp["composite_figi"] = mp["composite_figi"].fillna("").astype(str)
    mp["share_class_figi"] = mp["share_class_figi"].fillna("").astype(str)
    mp["cik_str"] = mp["cik_10digit"].astype(str)
    mp["formation_date"] = pd.to_datetime(mp["formation_date"])

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
    return cik_all_tickers, cik_composite_figi, cik_share_class_figi, ticker_to_ciks


def build_recovery_rows(years, data001_syms, data001_first, data001_last, data001_nbars,
                        cik_all_tickers, cik_composite_figi, cik_share_class_figi, ticker_to_ciks):
    rows = []
    for year in years:
        cohort_path = COHORT_DIR / f"_cohort_{year}.csv.gz"
        if not cohort_path.exists():
            print(f"WARN: missing cohort {cohort_path}", file=sys.stderr)
            continue
        cohort = pd.read_csv(cohort_path)
        for _, row in cohort.iterrows():
            cik = str(int(row["cik_10digit"]))
            orig_ticker = row["ticker"]
            rec = {
                "formation_year": year,
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
                "years_gap": None,
            }
            def _populate_bar_fields(rec, ticker):
                rec["d001_first_bar"] = str(data001_first[ticker])
                rec["d001_last_bar"] = str(data001_last[ticker])
                rec["d001_nbars"] = data001_nbars[ticker]
                # formation_price_available: True iff first bar year <= formation_year
                first_year = pd.Timestamp(rec["d001_first_bar"]).year
                rec["years_gap"] = first_year - year
                rec["formation_price_available"] = first_year <= year

            if orig_ticker in data001_syms:
                rec["matched_ticker"] = orig_ticker
                rec["match_type"] = "ORIGINAL_TICKER"
                rec["evidence_source"] = "DATA-001"
                rec["mapping_confidence"] = "HIGH"
                rec["bar_exists"] = True
                _populate_bar_fields(rec, orig_ticker)
            else:
                all_t = cik_all_tickers.get(cik, [])
                in_d001 = [t for t in all_t if t in data001_syms and t != orig_ticker]
                if len(in_d001) == 1:
                    t = in_d001[0]
                    rec["matched_ticker"] = t
                    rec["match_type"] = "UNIQUE_CIK_ALIAS"
                    rec["evidence_source"] = "Massive PIT CIK alias"
                    rec["mapping_confidence"] = "HIGH"
                    rec["bar_exists"] = True
                    _populate_bar_fields(rec, t)
                elif len(in_d001) > 1:
                    rec["alias_candidates"] = in_d001
                    rec["matched_ticker"] = in_d001[0]
                    rec["match_type"] = "AMBIGUOUS_MULTIPLE_ALIASES"
                    rec["evidence_source"] = "Massive PIT CIK aliases"
                    rec["mapping_confidence"] = "LOW"
                    rec["unique_match"] = False
                    t = in_d001[0]
                    rec["bar_exists"] = True
                    _populate_bar_fields(rec, t)
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
    cik_all_tickers, cik_composite_figi, cik_share_class_figi, ticker_to_ciks = load_pit()
    print(f"  PIT: {len(cik_all_tickers)} unique CIKs")

    print("Building recovery rows ...")
    rows = build_recovery_rows(
        YEARS,
        data001_syms, data001_first, data001_last, data001_nbars,
        cik_all_tickers, cik_composite_figi, cik_share_class_figi, ticker_to_ciks,
    )
    rdf = pd.DataFrame(rows)
    print(f"  Rows: {len(rdf)}")

    # STRAT-020 REMEDIATION: derive eligible_for_backtest field.
    # True iff HIGH confidence AND formation_price_available is True.
    # This separates IDENTITY recovery (mapping_confidence == HIGH, 15,087 rows)
    # from FORMATION-PRICE-VALID recovery (eligible_for_backtest == True, 14,876 rows).
    rdf["eligible_for_backtest"] = (
        (rdf["mapping_confidence"] == "HIGH") &
        (rdf["formation_price_available"] == True)
    )
    n_eligible = int(rdf["eligible_for_backtest"].sum())
    n_high = int((rdf["mapping_confidence"] == "HIGH").sum())
    n_high_no_fp = int(((rdf["mapping_confidence"] == "HIGH") & (~rdf["formation_price_available"])).sum())
    print(f"  eligible_for_backtest=True: {n_eligible} (HIGH with formation-date price)")
    print(f"  HIGH-confidence total: {n_high}")
    print(f"  HIGH-confidence without formation-date price: {n_high_no_fp} (post-formation-IPO; documented limitation)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rdf.to_parquet(OUT_PARQUET, index=False)
    print(f"  Parquet -> {OUT_PARQUET}")

    # JSON with arrays
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
    }
    src_path = OUT_DIR / "_step3_sources.json"
    with open(src_path, "w") as f:
        json.dump(sources, f, indent=2)
    print(f"  Sources -> {src_path}")

    # Match-type summary
    print()
    print("Match-type distribution:")
    print(rdf["match_type"].value_counts().to_string())
    print()
    print("Mapping-confidence distribution:")
    print(rdf["mapping_confidence"].value_counts().to_string())
    print()
    print("Bar-exists distribution:")
    print(rdf["bar_exists"].value_counts().to_string())
    print()
    print("Eligible-for-backtest distribution:")
    print(rdf["eligible_for_backtest"].value_counts().to_string())
    print()
    print("Newly-recovered (UNIQUE_CIK_ALIAS) eligible-for-backtest breakdown:")
    nr = rdf[rdf["match_type"] == "UNIQUE_CIK_ALIAS"]
    nr_eligible = int(nr["eligible_for_backtest"].sum())
    nr_ineligible = int((~nr["eligible_for_backtest"]).sum())
    print(f"  UNIQUE_CIK_ALIAS total: {len(nr)}")
    print(f"  UNIQUE_CIK_ALIAS eligible_for_backtest=True: {nr_eligible}")
    print(f"  UNIQUE_CIK_ALIAS eligible_for_backtest=False: {nr_ineligible} (post-formation-IPO)")


if __name__ == "__main__":
    main()
