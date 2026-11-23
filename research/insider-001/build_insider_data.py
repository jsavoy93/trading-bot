#!/usr/bin/env python3
"""INSIDER-001 data step -- filtered Form 4 open-market purchases (SEC, free).

Downloads the SEC "Insider Transactions Data Sets" quarterly ZIPs for 2019Q4..2024Q4
(fair access: User-Agent "SmartBot research (Josh Savoy)", <= 5 requests/second), keeps
ONLY qualifying non-derivative Form 4 open-market purchases as frozen in
PREREGISTRATION.md section 2:

  Form 4, NONDERIV_TRANS, transaction code P, acquired (A), at least one reporting
  owner who is an Officer or Director (ten-percent-owner-only does not qualify),
  shares x price >= $10,000, issuer CIK mapped to a ticker via the SECFILE-001 CIK map.
  Known date = SEC FILING date (never the transaction date). Filing date <= 2024-12-31.

Writes research/insider-001/data/insider_purchases.parquet and DELETES every downloaded
ZIP and extracted TSV afterwards. No bulk data is kept on the server.

Run:  python3 research/insider-001/build_insider_data.py [--cik-map PATH] [--keep-zips]
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "data"
OUT_PARQUET = OUT_DIR / "insider_purchases.parquet"

USER_AGENT = "SmartBot research (Josh Savoy)"
BASE_URL = ("https://www.sec.gov/files/structureddata/data/"
            "insider-transactions-data-sets")
QUARTERS = [f"{y}q{q}" for y in range(2019, 2025) for q in range(1, 5)]
QUARTERS = [q for q in QUARTERS if "2019q4" <= q <= "2024q4"]
MAX_FILING = pd.Timestamp("2024-12-31")
MIN_VALUE = 10_000.0
MIN_INTERVAL = 0.2            # <= 5 requests / second

DEFAULT_CIK_MAP = Path(
    "/root/research/secfile-001/data/company_tickers.json")     # SEC combined JSON
DEFAULT_TICKER_CIK = Path(
    "/root/research/secfile-001/data/ticker_cik.json")          # SECFILE-001 map

KEEP_COLS = ["symbol", "issuer_cik", "insider_cik", "filing_date", "transaction_date",
             "shares", "price", "value", "owner_relationship"]


# ---------------------------------------------------------------- CIK -> ticker
def load_cik_map(combined_path: Path, ticker_cik_path: Path) -> dict[int, str]:
    """CIK -> ticker. Primary source is SECFILE-001's ticker->CIK map (inverted);
    the SEC combined company_tickers.json fills CIKs the historical map lacks.
    On conflicts between the two files the SECFILE-001 map wins."""
    cik_to_ticker: dict[int, str] = {}
    if combined_path.exists():
        raw = json.loads(combined_path.read_text())
        rows = raw.values() if isinstance(raw, dict) else raw
        for r in rows:
            cik = r.get("cik") or r.get("cik_str")
            t = str(r.get("ticker", "")).strip().upper()
            if cik is None or not t:
                continue
            cik_to_ticker.setdefault(int(cik), t)
    if ticker_cik_path.exists():
        hist = json.loads(ticker_cik_path.read_text())
        for t, cik in hist.items():
            t = str(t).strip().upper()
            if t and t != "NONE" and cik is not None:
                cik_to_ticker[int(cik)] = t
    return cik_to_ticker


# ---------------------------------------------------------------- download
def fetch_zip(quarter: str, cache_dir: Path) -> Path:
    dest = cache_dir / f"{quarter}_form345.zip"
    url = f"{BASE_URL}/{quarter}_form345.zip"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=180) as r:
        dest.write_bytes(r.read())
    time.sleep(MIN_INTERVAL)
    return dest


def read_tsv(zpath: Path, name: str, usecols=None, dtype=str) -> pd.DataFrame:
    with zipfile.ZipFile(zpath) as z:
        with z.open(name) as f:
            return pd.read_csv(io.BytesIO(f.read()), sep="\t", dtype=dtype,
                               usecols=usecols, low_memory=False)


# ---------------------------------------------------------------- filtering
def filter_quarter(zpath: Path, cik_map: dict[int, str]) -> pd.DataFrame:
    sub = read_tsv(zpath, "SUBMISSION.tsv",
                   usecols=["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                            "ISSUERCIK", "ISSUERTRADINGSYMBOL"])
    sub = sub[sub["DOCUMENT_TYPE"].astype(str).str.strip() == "4"]
    tr = read_tsv(zpath, "NONDERIV_TRANS.tsv",
                  usecols=["ACCESSION_NUMBER", "TRANS_CODE", "TRANS_SHARES",
                           "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD",
                           "TRANS_DATE"])
    tr = tr[(tr["TRANS_CODE"].astype(str).str.strip() == "P")
            & (tr["TRANS_ACQUIRED_DISP_CD"].astype(str).str.strip() == "A")]
    if tr.empty:
        return pd.DataFrame(columns=KEEP_COLS)

    own = read_tsv(zpath, "REPORTINGOWNER.tsv",
                   usecols=["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"])
    rel = own["RPTOWNER_RELATIONSHIP"].fillna("").astype(str)
    qualified = rel.str.contains("Officer") | rel.str.contains("Director")
    own = own[qualified].rename(columns={"RPTOWNER_RELATIONSHIP": "owner_relationship"})[
        ["ACCESSION_NUMBER", "RPTOWNERCIK", "owner_relationship"]].drop_duplicates()

    df = tr.merge(sub, on="ACCESSION_NUMBER", how="inner")
    df = df.merge(own, on="ACCESSION_NUMBER", how="inner")
    df["shares"] = pd.to_numeric(df["TRANS_SHARES"], errors="coerce")
    df["price"] = pd.to_numeric(df["TRANS_PRICEPERSHARE"], errors="coerce")
    df = df[np.isfinite(df["shares"]) & np.isfinite(df["price"])]
    df = df[(df["shares"] > 0) & (df["price"] > 0)]
    df["value"] = df["shares"] * df["price"]
    df = df[df["value"] >= MIN_VALUE]
    if df.empty:
        return pd.DataFrame(columns=KEEP_COLS)

    df["filing_date"] = pd.to_datetime(df["FILING_DATE"], format="%d-%b-%Y",
                                       errors="coerce")
    df["transaction_date"] = pd.to_datetime(df["TRANS_DATE"], format="%d-%b-%Y",
                                            errors="coerce")
    df = df[df["filing_date"].notna()]
    df = df[df["filing_date"] <= MAX_FILING]

    # one row per (filing, insider) -- keep the largest purchase of each insider
    df["issuer_cik"] = pd.to_numeric(df["ISSUERCIK"], errors="coerce")
    df["insider_cik"] = pd.to_numeric(df["RPTOWNERCIK"], errors="coerce")
    df = df.sort_values("value", ascending=False).drop_duplicates(
        ["ACCESSION_NUMBER", "issuer_cik", "insider_cik"])
    df["symbol"] = df["issuer_cik"].map(lambda c: cik_map.get(int(c)) if np.isfinite(c) else None)
    out = df[KEEP_COLS].copy()
    out["issuer_cik"] = out["issuer_cik"].astype("int64")
    out["insider_cik"] = out["insider_cik"].fillna(-1).astype("int64")
    return out


def build(cik_map: dict[int, str], work_dir: Path) -> pd.DataFrame:
    parts = []
    for q in QUARTERS:
        zp = fetch_zip(q, work_dir)
        part = filter_quarter(zp, cik_map)
        parts.append(part)
        print(f"{q}: {len(part)} qualified purchase-rows", flush=True)
        zpath = zp
        zpath.unlink()                      # delete the ZIP immediately
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=KEEP_COLS)
    df = df.drop_duplicates(["issuer_cik", "insider_cik", "filing_date"])
    df = df.sort_values(["filing_date", "symbol"]).reset_index(drop=True)
    return df


def coverage(df: pd.DataFrame) -> dict:
    matched = df["symbol"].notna()
    return {
        "rows": int(len(df)),
        "rows_with_ticker": int(matched.sum()),
        "ticker_match_pct": float(100.0 * matched.mean()) if len(df) else 0.0,
        "distinct_tickers": int(df.loc[matched, "symbol"].nunique()),
        "distinct_issuers": int(df["issuer_cik"].nunique()),
        "filing_date_min": str(df["filing_date"].min().date()) if len(df) else None,
        "filing_date_max": str(df["filing_date"].max().date()) if len(df) else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="INSIDER-001 filtered Form 4 purchases")
    ap.add_argument("--cik-map", default=str(DEFAULT_CIK_MAP))
    ap.add_argument("--ticker-cik", default=str(DEFAULT_TICKER_CIK))
    ap.add_argument("--keep-zips", action="store_true",
                    help="debug only: keep downloaded ZIPs in the temp dir")
    args = ap.parse_args(argv)

    cik_map = load_cik_map(Path(args.cik_map), Path(args.ticker_cik))
    print(f"CIK map: {len(cik_map)} issuers", flush=True)
    work = Path(tempfile.mkdtemp(prefix="insider001_"))
    try:
        df = build(cik_map, work)
    finally:
        if args.keep_zips:
            print(f"keeping temp dir {work}", flush=True)
        else:
            shutil.rmtree(work, ignore_errors=True)

    assert len(df) > 0, "no qualifying purchases"
    assert df["filing_date"].max() <= MAX_FILING, "post-2024 filing leaked"
    df = df[df["symbol"].notna()].reset_index(drop=True)   # unmapped issuers dropped
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PARQUET, index=False)
    cov = coverage(df)
    cov["path"] = str(OUT_PARQUET)
    cov["size_kb"] = round(OUT_PARQUET.stat().st_size / 1024, 1)
    (OUT_DIR / "coverage.json").write_text(json.dumps(cov, indent=2))
    print(json.dumps(cov, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
