"""GAPFADE-SNAP-FIX — /v2/stocks/snapshots migration (offline, no network).

Covers: batching, the v2 response shape (flat {SYM: snap}, no wrapper),
field mapping into marketdata.expected_open (latestTrade.p then
minuteBar.c), and a regression guard that the dead beta endpoint is gone.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.gapfade_midcap_paper import alpaca, marketdata  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "alpaca_v2_snapshots_iex.json"
PKG = ROOT / "experiments" / "gapfade_midcap_paper"


class Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise alpaca.AlpacaError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None,
                timeout=None):
        self.calls.append({"method": method, "url": url, "params": params})
        for (m, frag), fn in self.routes.items():
            if m == method and frag in url:
                return fn(params)
        return Resp(404, {"message": "not found"})


def client(routes=None):
    return alpaca.AlpacaPaperClient("test-key", "test-secret",
                                    session=FakeSession(routes))


def loaded_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --- the frozen v2 shape ------------------------------------------------
def test_fixture_is_the_flat_v2_shape():
    body = loaded_fixture()
    assert "snapshots" not in body          # v2 has no wrapper object
    assert set(body) == {"AAA", "BBB", "CCC"}
    for sym, snap in body.items():
        assert isinstance(snap, dict), sym
        assert "prevDailyBar" in snap and "c" in snap["prevDailyBar"]


# --- URL + parsing -------------------------------------------------------
def test_snapshot_prices_uses_v2_and_returns_flat_dict():
    body = loaded_fixture()
    c = client({("GET", "/v2/stocks/snapshots"): lambda p: Resp(200, body)})
    out = c.snapshot_prices(["AAA", "BBB", "CCC"])
    assert out == body
    assert out["AAA"]["latestTrade"]["p"] == 10.25
    assert out["AAA"]["minuteBar"]["c"] == 10.26
    assert out["AAA"]["prevDailyBar"]["c"] == 10.5
    urls = [call["url"] for call in c.sess.calls]
    assert urls == ["https://data.alpaca.markets/v2/stocks/snapshots"]
    assert c.sess.calls[0]["params"]["feed"] == "iex"
    assert c.sess.calls[0]["params"]["symbols"] == "AAA,BBB,CCC"


def test_snapshot_prices_empty_symbols_makes_no_call():
    c = client({("GET", "/v2/stocks/snapshots"): lambda p: Resp(200, {})})
    assert c.snapshot_prices([]) == {}
    assert c.sess.calls == []


def test_snapshot_prices_batches_and_merges():
    symbols = [f"S{i:04d}" for i in range(450)]

    def fn(params):
        batch = params["symbols"].split(",")
        assert len(batch) <= alpaca.SNAPSHOT_BATCH_SIZE
        return Resp(200, {s: {"latestTrade": {"p": 1.0}} for s in batch})

    c = client({("GET", "/v2/stocks/snapshots"): fn})
    out = c.snapshot_prices(symbols)
    assert len(out) == 450
    assert len(c.sess.calls) == 3             # 200 + 200 + 50
    sizes = [len(call["params"]["symbols"].split(","))
             for call in c.sess.calls]
    assert sizes == [200, 200, 50]


def test_snapshot_prices_tolerates_missing_symbols():
    body = {"AAA": {"latestTrade": {"p": 10.0}}}
    c = client({("GET", "/v2/stocks/snapshots"): lambda p: Resp(200, body)})
    out = c.snapshot_prices(["AAA", "ZZZZ"])
    assert out == {"AAA": {"latestTrade": {"p": 10.0}}}


# --- field mapping through MarketData -----------------------------------
def _bars(sym, rows):
    return {sym: [{"t": f"{d}T04:00:00Z", "o": o, "h": h, "l": lo, "c": c,
                   "v": 1000} for d, o, h, lo, c in rows]}


def test_expected_open_maps_latest_trade_then_minute_bar():
    body = loaded_fixture()
    bars = {}
    bars.update(_bars("AAA", [("2026-11-24", 10.0, 11.0, 9.9, 10.5)]))
    bars.update(_bars("BBB", [("2026-11-24", 21.0, 21.5, 20.8, 21.0)]))
    bars.update(_bars("CCC", [("2026-11-24", 5.7, 5.9, 5.6, 5.8)]))
    routes = {
        ("GET", "/v2/stocks/bars"): lambda p: Resp(200, {"bars": bars}),
        ("GET", "/v2/stocks/snapshots"): lambda p: Resp(200, body),
    }
    data = marketdata.AlpacaMarketData(
        client(routes), cache_dir="/tmp/x", members=["AAA", "BBB", "CCC"],
        today=date(2026, 11, 25))
    data.load_day("2026-11-25")
    assert data.prior_close("2026-11-25") == {"AAA": 10.5, "BBB": 21.0,
                                              "CCC": 5.8}
    # AAA has latestTrade; CCC has only minuteBar -> falls back to its close
    assert data.expected_open("2026-11-25") == {"AAA": 10.25, "BBB": 20.5,
                                                "CCC": 5.55}


# --- regression guard ----------------------------------------------------
def test_no_deprecated_snapshot_endpoint_in_package():
    dead = "v1beta" + "1/stocks/snapshots"   # split so this file is no hit
    hits = []
    for path in sorted(PKG.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if dead in text:
            hits.append(path.name)
        assert "v1beta" not in text, f"{path.name} still mentions v1beta"
    assert hits == []


def test_alpaca_module_only_uses_v2_stocks_paths():
    text = (PKG / "alpaca.py").read_text(encoding="utf-8")
    paths = re.findall(r"\{self\.data_url\}(/[^\"'?\s]+)", text)
    assert paths, "expected data API paths in alpaca.py"
    assert all(p.startswith("/v2/stocks/") for p in paths), paths
    assert "/v2/stocks/snapshots" in paths
