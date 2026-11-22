"""V2 news PROBE: does the Alpaca News API return historical (DEV-window) headlines at all?
Read-only GETs only. Nothing after 2024-12-31 is requested. Keys never printed."""
import json, time
from pathlib import Path
from datetime import datetime, timezone
import urllib.request, urllib.error
DATA = Path(__file__).resolve().parents[1] / "data"; DATA.mkdir(exist_ok=True)
CUT = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
env = {}
for line in Path("/root/deploy/trading-bot-main/.env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
H = {"APCA-API-KEY-ID": env["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET"],
     "Accept": "application/json"}
records = []
for sym in ["AAPL", "MSFT", "TSLA", "NVDA", "WOLF", "CAPR"]:
    for win in (("2021-06-01", "2021-06-08"), ("2024-06-03", "2024-06-10")):
        url = (f"https://data.alpaca.markets/v1beta1/news?symbols={sym}"
               f"&start={win[0]}T00:00:00Z&end={win[1]}T00:00:00Z&limit=50")
        assert datetime.fromisoformat(win[1]).replace(tzinfo=timezone.utc) <= CUT
        try:
            js = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30).read())
            n = len(js.get("news", [])); msg = js.get("message") or ""
            first = json.loads(json.dumps(js["news"][0]["created_at"])) if n else ""
            err = ""
        except urllib.error.HTTPError as e:
            n, msg, first, err = 0, "", "", f"HTTP {e.code}"
        except Exception as e:
            n, msg, first, err = 0, "", "", repr(e)[:80]
        records.append({"symbol": sym, "window": list(win), "n": n, "first": first, "error": err})
        print(sym, win[0], "n=", n, "first=", first, err, flush=True)
        time.sleep(0.3)
(DATA / "v2_probe.json").write_text(json.dumps({"probe": records, "cutoff": "2024-12-31",
    "verdict": "historical_unavailable" if all(r["n"] == 0 and not r["error"] for r in records) else "historical_available"}))
