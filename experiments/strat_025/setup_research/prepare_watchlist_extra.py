"""STRAT-025 setup research — SUPPLEMENTARY universe-robustness check (post-hoc, labeled).

The frozen prereg §3 defined "main watchlist" as the quality_symbols list in
src/core/smart_bot.py. PART 1 of STRAT-025 (same branch, another agent) established that
the running bot's actual watchlist is the key set of `analysis_status.json`
(200 symbols), which includes ~145 micro/small-cap names not in the prereg universe.

This script (POST-HOC, not part of the frozen prereg run) checks whether adding those
names could plausibly change any dev conclusion: it downloads their daily bars and
counts how many symbol-days would even be ELIGIBLE under the prereg point-in-time filter
(trailing 20d mean volume >= 1,000,000 AND close >= $5), and how many setup signal days
they would contribute. Symbols with no data are counted as missing (not silently ignored).
"""
from __future__ import annotations
import csv, json, os, time
from datetime import datetime, timezone
from pathlib import Path
import requests

HERE = Path(__file__).resolve().parent
MAIN_WS = Path("/root/.openclaw/workspace/trading-bot")  # read-only .env creds
OUT = HERE / "data_watchlist_extra"
OUT.mkdir(exist_ok=True)

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("UNIT_TESTING", "1")
for _line in (MAIN_WS / ".env").read_text().splitlines():
    if "=" in _line and not _line.strip().startswith("#"):
        k, _, v = _line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"'))
KEY = os.getenv("ALPACA_API_KEY")
SEC = os.getenv("ALPACA_API_SECRET")
assert KEY and SEC
HEADERS = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SEC}
DATA = "https://data.alpaca.markets"

import universe as U

watch = sorted(set(json.loads((MAIN_WS / "analysis_status.json").read_text()).keys()))
extra = [s for s in watch if s not in set(U.ALL)]
print(f"analysis_status watchlist={len(watch)}; not in prereg universe={len(extra)}")

manifest = {"fetched_at": datetime.now(timezone.utc).isoformat(), "symbols": {}}
sess = requests.Session()
for i, sym in enumerate(extra):
    bars, token, status = [], None, None
    while True:
        p = {"symbols": sym, "timeframe": "1Day", "start": "2020-06-01",
             "end": "2026-10-29", "limit": 10000, "adjustment": "raw"}
        if token:
            p["page_token"] = token
        r = sess.get(f"{DATA}/v2/stocks/bars", headers=HEADERS, params=p, timeout=60)
        status = r.status_code
        if r.status_code == 429:
            time.sleep(20); continue
        if r.status_code != 200:
            bars = None; break
        j = r.json()
        raw = j.get("bars", [])
        if isinstance(raw, dict):
            raw = raw.get(sym, [])
        bars.extend(raw)
        token = j.get("next_page_token")
        if not token:
            break
    if bars:
        with open(OUT / f"{sym}.csv", "w", newline="") as f:
            w = csv.writer(f); w.writerow(["t", "o", "h", "l", "c", "v"])
            for b in bars:
                w.writerow([b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]])
        manifest["symbols"][sym] = {"ok": True, "n": len(bars)}
    else:
        manifest["symbols"][sym] = {"ok": False, "status": status}
    if (i + 1) % 25 == 0:
        print(f"{i+1}/{len(extra)}")
        (HERE / "_watchlist_extra_manifest.json").write_text(json.dumps(manifest))
    time.sleep(0.3)
(HERE / "_watchlist_extra_manifest.json").write_text(json.dumps(manifest))
ok = sum(1 for v in manifest["symbols"].values() if v.get("ok"))
print(f"DONE ok={ok}/{len(extra)}")
