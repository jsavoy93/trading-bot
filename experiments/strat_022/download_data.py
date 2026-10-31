"""STRAT-022: download daily bars 2020-06-01..2026-10-28 for the deduped
STRAT-006R curated liquid universe. Paper data API only. Saves one CSV per
symbol under experiments/strat_022/data/ plus a manifest with call log."""
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
import requests

HERE = Path(__file__).resolve().parent
MAIN_WS = Path("/root/.openclaw/workspace/trading-bot")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("UNIT_TESTING", "1")
for _line in (MAIN_WS / ".env").read_text().splitlines():
    if "=" in _line and not _line.strip().startswith("#"):
        k, _, v = _line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"\"'))
KEY = os.getenv("ALPACA_API_KEY")
SEC = os.getenv("ALPACA_API_SECRET")
assert KEY and SEC, "paper creds missing"
HEADERS = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SEC}
DATA = "https://data.alpaca.markets"

# universe: import curated list from STRAT-006R (read-only)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "cand", MAIN_WS / "experiments/strat_006r_candidate_universe.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
symbols = sorted({s for s in mod.POPULAR_LIQUID_US if s})
print(f"universe: {len(symbols)} symbols")

OUT = HERE / "data"
OUT.mkdir(exist_ok=True)
manifest = {"fetched_at": datetime.now(timezone.utc).isoformat(), "calls": [], "symbols": {}}
sess = requests.Session()
ncalls = 0
for i, sym in enumerate(symbols):
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
            manifest["calls"].append({"sym": sym, "status": r.status_code})
            bars = None; break
        j = r.json()
        raw = j.get("bars", [])
        if isinstance(raw, dict):  # multi-symbol shape: {sym: [bars]}
            raw = raw.get(sym, [])
        bars.extend(raw)
        token = j.get("next_page_token")
        if not token:
            break
    if bars is None:
        manifest["symbols"][sym] = {"ok": False}
        print(f"{sym}: HTTP fail")
    elif not bars:
        manifest["symbols"][sym] = {"ok": False, "empty": True}
    else:
        import csv
        with open(OUT / f"{sym}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t", "o", "h", "l", "c", "v"])
            for b in bars:
                w.writerow([b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]])
        manifest["symbols"][sym] = {"ok": True, "n": len(bars)}
    if (i + 1) % 25 == 0:
        print(f"{i+1}/{len(symbols)} done, {ncalls} calls")
        (HERE / "_download_manifest.json").write_text(json.dumps(manifest))
    time.sleep(0.35)
(HERE / "_download_manifest.json").write_text(json.dumps(manifest))
ok = sum(1 for v in manifest["symbols"].values() if v.get("ok"))
print(f"DONE ok={ok}/{len(symbols)} calls={ncalls}")
