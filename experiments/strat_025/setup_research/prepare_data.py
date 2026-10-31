"""STRAT-025 setup research — data preparation (PREREGISTRATION §2).

1. Download daily bars 2020-06-01..2026-10-28 for currently-listed union names from the
   Alpaca PAPER data API (adjustment=raw), STRAT-022 harness precedent.
2. Extract the 307 delisted additions from the DATA-001 parquet shards (read-only) into
   the same CSV format (t,o,h,l,c,v).
Outputs one CSV per symbol in ./data/ plus _data_manifest.json.
"""
from __future__ import annotations
import csv, json, os, time
from datetime import datetime, timezone
from pathlib import Path
import requests

HERE = Path(__file__).resolve().parent
MAIN_WS = Path("/root/.openclaw/workspace/trading-bot")  # read-only .env creds
OUT = HERE / "data"
OUT.mkdir(exist_ok=True)

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("UNIT_TESTING", "1")
for _line in (MAIN_WS / ".env").read_text().splitlines():
    if "=" in _line and not _line.strip().startswith("#"):
        k, _, v = _line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"'))
KEY = os.getenv("ALPACA_API_KEY")
SEC = os.getenv("ALPACA_API_SECRET")
assert KEY and SEC, "paper creds missing"
HEADERS = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SEC}
DATA = "https://data.alpaca.markets"

import universe as U

manifest = {"fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": {"alpaca": [], "parquet_delisted": []}}

def save_csv(sym: str, rows: list[dict]):
    with open(OUT / f"{sym}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "o", "h", "l", "c", "v"])
        for b in rows:
            w.writerow([b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]])

# ---- 1. delisted additions from parquet (read-only) ----
import pandas as pd
SHARDS = sorted((MAIN_WS / "experiments/data_001/bars_shards").glob("batch_*.parquet"))
want = set(U.DELISTED)
found = {}
for sh in SHARDS:
    if not want:
        break
    df = pd.read_parquet(sh, columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
    hit = want.intersection(df["symbol"].unique())
    for sym in hit:
        sub = df[df["symbol"] == sym].sort_values("timestamp")
        sub = sub[(sub["timestamp"] >= "2020-06-01") & (sub["timestamp"] < "2026-10-29")]
        if len(sub) >= 220:
            rows = [{"t": str(ts), "o": float(o), "h": float(h), "l": float(l),
                     "c": float(c), "v": float(v)}
                    for ts, o, h, l, c, v in zip(sub["timestamp"], sub["open"], sub["high"],
                                                  sub["low"], sub["close"], sub["volume"])]
            save_csv(sym, rows)
            found[sym] = len(rows)
        want.discard(sym)
    print(f"{sh.name}: cumulative delisted saved={len(found)}")
manifest["source"]["parquet_delisted"] = {"saved": found, "missing": sorted(want)}
print(f"delisted from parquet: {len(found)} saved, {len(want)} missing")

# ---- 2. currently-listed names from Alpaca paper API ----
sess = requests.Session()
ncalls = 0
symbols = U.listed_for_alpaca()
for i, sym in enumerate(symbols):
    if (OUT / f"{sym}.csv").exists():
        continue
    bars, token = [], None
    while True:
        p = {"symbols": sym, "timeframe": "1Day", "start": "2020-06-01",
             "end": "2026-10-29", "limit": 10000, "adjustment": "raw"}
        if token:
            p["page_token"] = token
        r = sess.get(f"{DATA}/v2/stocks/bars", headers=HEADERS, params=p, timeout=60)
        ncalls += 1
        if r.status_code == 429:
            time.sleep(20); continue
        if r.status_code != 200:
            manifest["source"]["alpaca"].append({"sym": sym, "status": r.status_code})
            bars = None; break
        j = r.json()
        raw = j.get("bars", [])
        if isinstance(raw, dict):
            raw = raw.get(sym, [])
        bars.extend(raw)
        token = j.get("next_page_token")
        if not token:
            break
    if bars is None:
        print(f"{sym}: HTTP fail")
    elif not bars:
        manifest["source"]["alpaca"].append({"sym": sym, "empty": True})
    else:
        save_csv(sym, bars)
    if (i + 1) % 50 == 0:
        print(f"{i+1}/{len(symbols)} alpaca done, {ncalls} calls")
        (HERE / "_data_manifest.json").write_text(json.dumps(manifest))
    time.sleep(0.3)
(HERE / "_data_manifest.json").write_text(json.dumps(manifest))
n = len(list(OUT.glob("*.csv")))
print(f"DONE csvs={n} alpaca_calls={ncalls}")
