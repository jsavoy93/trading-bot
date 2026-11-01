"""STRAT-027 — build the point-in-time fundamentals panel from local SEC XBRL data.

READ-ONLY source:
  /root/.openclaw/workspace/trading-bot/research_data/sec_fundamentals/_per_quarter/
      <YYYYqN>_num.parquet   XBRL facts      (adsh, tag, ddate, qtrs, segments, coreg, value)
      <YYYYqN>_sub.parquet   filing headers  (adsh, cik, form, fp, period, fy, filed, accepted)

OUTPUT: data/fundamentals_pit.parquet — FACT LEVEL, every version retained:
    symbol, cik, tag, ddate (fact period end), filed (public release date), value, qtrs

WHY FACT LEVEL
  A fact can be re-reported by later filings (a 10-K carries three years of income
  statement and a two-year balance sheet; a statement of stockholders' equity carries
  the prior year-end balance). Collapsing facts into one row per (company, fiscal
  period) destroys the information about WHEN each version became public, which is the
  whole point of a point-in-time study. The study therefore keeps every version and,
  for any historical date, uses the version that was public on that date.

DATA DEFECTS HANDLED (see DATA_AUDIT.md)
  1. XBRL DIMENSIONING — ~26% of (cik, tag, ddate) triples carry several DIFFERENT
     values inside one filing because `segments` / `coreg` hold dimensional context
     (e.g. ADP StockholdersEquity 20230630 has 6 rows; the consolidated total is
     3,509,100,000). Only rows with EMPTY `segments` AND EMPTY `coreg` are kept.
  2. Forms — only 10-K / 10-K/A with fp == "FY" exist in this dataset.
  3. Duration vs instant — `qtrs` distinguishes a duration fact (4 = annual) from an
     instant fact (0 = point in time); the right flavour of each tag is kept.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
OUT = HERE / "data"

SRC = Path(os.environ.get(
    "STRAT027_FUND_SRC",
    "/root/.openclaw/workspace/trading-bot/research_data/sec_fundamentals/_per_quarter",
))
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
UA = "trading-bot-research contact@example.com"

TAGS = [
    "NetIncomeLoss",
    "StockholdersEquity",
    "Assets",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "CommonStockSharesOutstanding",
    "CashAndCashEquivalentsAtCarryingValue",
]
# duration facts (annual) and instant facts
DURATION_TAG_QTRS = {"NetIncomeLoss": 4,
                     "RevenueFromContractWithCustomerExcludingAssessedTax": 4,
                     "Revenues": 4}
INSTANT_TAG_QTRS = {"StockholdersEquity": 0, "Assets": 0,
                    "CommonStockSharesOutstanding": 0,
                    "CashAndCashEquivalentsAtCarryingValue": 0}

MIN_DDATE = pd.Timestamp("1990-01-01")
MAX_DDATE = pd.Timestamp("2027-01-01")


def fetch_ticker_map(cache: Path) -> pd.DataFrame:
    """cik -> ticker from the SEC's official company_tickers.json."""
    if not cache.exists():
        req = urllib.request.Request(TICKER_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            cache.write_bytes(r.read())
    raw = json.loads(cache.read_text())
    rows = [{"cik": int(v["cik_str"]), "ticker": str(v["ticker"]).strip().upper(),
             "title": str(v["title"]).strip()}
            for v in raw.values()]
    df = pd.DataFrame(rows).drop_duplicates(subset=["cik"]).reset_index(drop=True)
    return df


def parse_ddate(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    ymd = s.str.fullmatch(r"\d{8}")
    if ymd.any():
        out.loc[ymd] = pd.to_datetime(s[ymd], format="%Y%m%d", errors="coerce")
    other = ~ymd & s.ne("")
    if other.any():
        out.loc[other] = pd.to_datetime(s[other], errors="coerce", format="mixed")
    return out


def main() -> int:
    if not SRC.exists():
        print(f"FATAL: SEC fundamentals source not found at {SRC}", file=sys.stderr)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    qdir = sorted(p for p in SRC.glob("*_num.parquet"))
    if not qdir:
        print(f"FATAL: no *_num.parquet under {SRC}", file=sys.stderr)
        return 2

    frames = []
    n_raw_rows = 0
    n_kept_rows = 0
    for num_path in qdir:
        sub_path = num_path.with_name(num_path.name.replace("_num.", "_sub."))
        if not sub_path.exists():
            continue
        num_ds = ds.dataset(num_path, format="parquet")
        tbl = num_ds.to_table(filter=ds.field("tag").isin(TAGS),
                              columns=["adsh", "tag", "ddate", "qtrs", "segments", "coreg", "value"])
        n_raw_rows += tbl.num_rows
        sub = pq.read_table(sub_path,
                            columns=["adsh", "cik", "form", "fp", "filed"]).to_pandas()
        sub = sub[sub["form"].isin(["10-K", "10-K/A"]) & (sub["fp"] == "FY")].copy()
        if sub.empty:
            continue
        df = tbl.to_pandas()
        if df.empty:
            continue
        # defect 1: consolidated (undimensioned) facts only
        df = df[(df["segments"].fillna("") == "") & (df["coreg"].fillna("") == "")]
        df = df.merge(sub[["adsh", "cik", "filed"]], on="adsh", how="inner")
        if df.empty:
            continue
        df["ddate"] = parse_ddate(df["ddate"])
        df = df[(df["ddate"] >= MIN_DDATE) & (df["ddate"] <= MAX_DDATE)]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df[df["value"].notna()]
        df["qtrs"] = pd.to_numeric(df["qtrs"], errors="coerce")
        keep = np.zeros(len(df), dtype=bool)
        for tag, q in DURATION_TAG_QTRS.items():
            keep |= (df["tag"].eq(tag) & df["qtrs"].eq(q)).to_numpy()
        for tag, q in INSTANT_TAG_QTRS.items():
            keep |= (df["tag"].eq(tag) & df["qtrs"].eq(q)).to_numpy()
        df = df[keep]
        n_kept_rows += len(df)
        frames.append(df[["cik", "adsh", "tag", "ddate", "qtrs", "value", "filed"]])
    if not frames:
        print("FATAL: no usable fact rows", file=sys.stderr)
        return 2
    facts = pd.concat(frames, ignore_index=True)
    print(f"fact rows: raw-in-tag-set {n_raw_rows} -> consolidated+joined {n_kept_rows}")

    facts["filed"] = pd.to_datetime(facts["filed"], format="%Y%m%d", errors="coerce")
    facts = facts[facts["filed"].notna()].copy()

    # ---- cik -> ticker ----
    tick = fetch_ticker_map(Path("/tmp/company_tickers.json"))
    facts["cik"] = pd.to_numeric(facts["cik"], errors="coerce").astype("int64")
    tick["cik"] = pd.to_numeric(tick["cik"], errors="coerce").astype("int64")
    facts = facts.merge(tick[["cik", "ticker"]], on="cik", how="left")
    facts = facts.rename(columns={"ticker": "symbol"})

    uni_path = OUT / "universe.csv"
    if uni_path.exists():
        uni = pd.read_csv(uni_path)
        universe_symbols = set(uni["symbol"].astype(str).str.upper())
        facts["symbol"] = facts["symbol"].astype(str).str.upper()
        before = len(facts)
        facts = facts[facts["symbol"].isin(universe_symbols)]
        print(f"facts inside the study universe: {len(facts)} of {before} "
              f"({len(facts)/max(before,1):.1%})")

    facts = facts[["symbol", "cik", "tag", "ddate", "qtrs", "value", "filed"]]
    facts = facts.sort_values(["symbol", "tag", "ddate", "filed"]).reset_index(drop=True)
    out_path = OUT / "fundamentals_pit.parquet"
    facts.to_parquet(out_path, index=False, compression="zstd")

    meta = {
        "source_dir": str(SRC),
        "quarters": len(qdir),
        "fact_rows_in_tag_set": int(n_raw_rows),
        "fact_rows_consolidated_joined": int(n_kept_rows),
        "panel_rows": int(len(facts)),
        "panel_symbols": int(facts["symbol"].nunique()),
        "panel_ciks": int(facts["cik"].nunique()),
        "min_filed": str(facts["filed"].min().date()),
        "max_filed": str(facts["filed"].max().date()),
        "tags": TAGS,
        "versions_per_fact": float(len(facts) / max(facts.groupby(["symbol", "tag", "ddate"]).ngroups, 1)),
        "rows_per_year": facts.groupby(facts["filed"].dt.year).size().to_dict(),
    }
    (OUT / "fundamentals_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    print(json.dumps({k: v for k, v in meta.items() if k != "rows_per_year"}, indent=2, default=str))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
