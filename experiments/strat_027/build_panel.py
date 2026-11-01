"""STRAT-027 — Step 1: build the price panel + universe metadata for the study.

READ-ONLY source: the local DATA-001 research dataset
  /root/.openclaw/workspace/trading-bot/research_data/alpaca_daily_2016_2026/
    - daily_bars_adjusted.parquet  (Alpaca daily OHLCV, adjustment="all")
    - assets.parquet                (Alpaca US equity asset metadata)

Nothing in the source tree is modified. Outputs are written to
experiments/strat_027/data/ (git-ignored; regenerable with this script).

Panel contract
--------------
  symbol, date (UTC date), open, close, volume
  - open/close are split+DIVIDEND adjusted (Alpaca adjustment="all"), so returns
    computed from them are total returns and are split-consistent.
  - date is the trading day (bar close timestamp normalized to UTC date).
  - Only common stock / ADR symbols are kept for the stock universe; SPY is kept
    as the buy-and-hold benchmark. ETFs/funds, preferreds, warrants, rights,
    units, notes and SPACs are excluded from the investable universe (they are
    not operating companies and their factor values are not comparable).
  - start date is configurable so 12-1 momentum and 12-month realized volatility
    are fully warmed up before the first development rebalance.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
OUT = HERE / "data"

SRC_DIR = Path(
    os.environ.get(
        "STRAT027_SRC",
        "/root/.openclaw/workspace/trading-bot/research_data/alpaca_daily_2016_2026",
    )
)
BARS = SRC_DIR / "daily_bars_adjusted.parquet"
ASSETS = SRC_DIR / "assets.parquet"

PANEL_START = os.environ.get("STRAT027_PANEL_START", "2019-01-01")
BENCHMARKS = {"SPY"}

EQUITY_TYPES = {"common_or_other", "adr"}


def main() -> int:
    if not BARS.exists() or not ASSETS.exists():
        print(f"FATAL: source dataset not found under {SRC_DIR}", file=sys.stderr)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)

    assets = pq.read_table(ASSETS).to_pandas()
    keep = assets["coarse_type"].astype(str).isin(EQUITY_TYPES) | assets["symbol"].isin(BENCHMARKS)
    keep &= assets.get("has_bars", True) if "has_bars" in assets.columns else True
    universe = assets.loc[keep, ["symbol", "name", "exchange", "status", "coarse_type", "has_bars"]]
    universe = universe.copy()
    universe["symbol"] = universe["symbol"].astype(str)
    universe["status"] = universe["status"].astype(str).str.split(".").str[-1]
    universe["exchange"] = universe["exchange"].astype(str).str.split(".").str[-1]
    universe = universe.sort_values("symbol").reset_index(drop=True)
    allowed = set(universe["symbol"])
    universe.to_csv(OUT / "universe.csv", index=False)
    print(f"universe: {len(universe)} symbols "
          f"(active={int((universe['status'] == 'ACTIVE').sum())}, "
          f"inactive={int((universe['status'] != 'ACTIVE').sum())})")

    table = pq.read_table(
        BARS,
        columns=["symbol", "timestamp", "open", "close", "volume"],
    )
    df = table.to_pandas()
    del table
    print(f"raw bars rows: {len(df)}")

    df["symbol"] = df["symbol"].astype(str)
    df = df[df["symbol"].isin(allowed)]
    df["date"] = df["timestamp"].dt.tz_convert("UTC").dt.normalize().dt.tz_localize(None)
    df = df[(df["date"] >= PANEL_START) & df["open"].notna() & df["close"].notna()]
    df = df[df["close"] > 0]
    df = df[["symbol", "date", "open", "close", "volume"]].copy()
    df = df.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last")
    df["volume"] = df["volume"].fillna(0.0)
    print(f"panel rows: {len(df)}  symbols: {df['symbol'].nunique()}  "
          f"range: {df['date'].min().date()} -> {df['date'].max().date()}")

    df.to_parquet(OUT / "panel.parquet", index=False, compression="zstd")

    meta = {
        "source_dir": str(SRC_DIR),
        "bars_file": str(BARS),
        "panel_start": PANEL_START,
        "panel_rows": int(len(df)),
        "panel_symbols": int(df["symbol"].nunique()),
        "panel_min_date": str(df["date"].min().date()),
        "panel_max_date": str(df["date"].max().date()),
        "universe_symbols": int(len(universe)),
        "universe_inactive_symbols": int((universe["status"] != "ACTIVE").sum()),
        "benchmark_symbols": sorted(BENCHMARKS),
    }
    (OUT / "panel_meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
