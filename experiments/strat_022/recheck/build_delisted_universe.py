"""STRAT-023 Task A — build survivorship-bias additions universe from the local
DATA-001 parquet dataset (READ-ONLY source).

Selection criterion (no future information about RETURNS is used; only the
structural fact that the symbol's bar series TERMINATES mid-history, i.e. the
name is no longer listed today and therefore absent from the STRAT-022
"currently-known" pool):
  - symbol NOT already in the STRAT-022 universe (../data/*.csv)
  - >= 220 bars
  - last bar in [2020-06-01, 2026-06-01)  (delisted/acquired mid-history;
    names ending before 2020-06 cannot contribute to 2021+ entries anyway)
  - coarse_type in {common_or_other, adr} (match the operating-company nature
    of the original pool; exclude ETFs/units/rights/preferreds/SPACs)

Output: CSV per symbol in ./data_delisted/, same schema as STRAT-022 data
(t,o,h,l,c,v) with raw-style column names, dates as YYYY-MM-DD.
Terminal value handling: NO synthetic final bar is added. If a position is
still open when the symbol's data ends, the engine exits at the LAST available
close (same rule the original engine applies when data runs out).
"""
from __future__ import annotations
import os, sys
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = Path("/root/.openclaw/workspace/trading-bot/research_data/alpaca_daily_2016_2026/transfer")
OUT = HERE / "data_delisted"
ORIG = HERE.parent / "data"

KEEP_TYPES = {"common_or_other", "adr"}


def main():
    OUT.mkdir(exist_ok=True)
    orig = {f[:-4] for f in os.listdir(ORIG) if f.endswith(".csv")}
    assets = pd.read_parquet(SRC / "assets.parquet")
    types = dict(zip(assets["symbol"].astype(str), assets["coarse_type"].astype(str)))

    want = set(pd.read_csv(HERE / "additions_symbols.csv")["symbol"]) if (HERE / "additions_symbols.csv").exists() else None
    frames = []
    for i in range(1, 6):
        p = SRC / f"daily_bars_part_{i:03d}.parquet"
        df = pd.read_parquet(p, columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        df = df[df["symbol"].isin(want)] if want is not None else df
        frames.append(df)
    df = pd.concat(frames)
    del frames
    df["date"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None).dt.normalize()
    df = df.drop(columns="timestamp").drop_duplicates(subset=["symbol", "date"])
    g = df.groupby("symbol").agg(first=("date", "min"), last=("date", "max"), n=("close", "size"))
    sel = g[(g["last"] >= pd.Timestamp("2020-06-01")) & (g["last"] < pd.Timestamp("2026-06-01")) & (g["n"] >= 220)]
    sel = sel[~sel.index.isin(orig)]
    sel = sel[[types.get(s, "?") in KEEP_TYPES for s in sel.index]]
    print(f"selected {len(sel)} delisted additions")
    sel.to_csv(HERE / "_additions_selected.csv")
    for sym in sel.index:
        d = df[df["symbol"] == sym].sort_values("date")
        # keep_default_na=False semantics: write raw strings, reader uses keep_default_na=False
        out = pd.DataFrame({
            "t": d["date"].dt.strftime("%Y-%m-%d"),
            "o": d["open"].values, "h": d["high"].values,
            "l": d["low"].values, "c": d["close"].values,
            "v": d["volume"].values,
        })
        out.to_csv(OUT / f"{sym}.csv", index=False)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
