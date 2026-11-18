"""TICKER-INFO-001A: /api/company/{symbol} company info endpoint.

No live network. The fake ``http_get`` dispatches on URL using an ORDERED
list of (prefix, response) pairs checked LONGEST-PREFIX-FIRST, so a
shorter prefix (e.g. ``https://data.alpaca.markets``) can never shadow a
longer one that shares it.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for _p in (str(REPO_ROOT), str(SRC_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ─────────────────────────────────────────────────────────────────────────
# Fake HTTP layer
# ─────────────────────────────────────────────────────────────────────────

class FakeResponse:
    """Minimal realistic stand-in for requests.Response."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeHTTP:
    """Dispatch on URL via ordered prefixes, longest first."""

    def __init__(self, routes):
        # routes: dict prefix -> payload (or callable(url, **kw))
        self.routes = sorted(routes.items(), key=lambda kv: len(kv[0]), reverse=True)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        self.timeouts = [kw.get("timeout") for _, kw in self.calls]
        for prefix, payload in self.routes:
            if url.startswith(prefix):
                if callable(payload):
                    return payload(url, **kwargs)
                return FakeResponse(payload)
        raise AssertionError(f"unexpected URL {url}")

    @property
    def count(self):
        return len(self.calls)

    @property
    def urls(self):
        return [u for u, _ in self.calls]


ASSET_URL = "https://paper-api.alpaca.markets/v2/assets/"
NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_PREFIX = "https://data.sec.gov/submissions/"

ASSET_BODY = {
    "id": "e2f3",
    "class": "us_equity",
    "exchange": "NASDAQ",
    "symbol": "AAPL",
    "name": "Apple Inc. Common Stock",
    "status": "active",
    "tradable": True,
    "shortable": True,
    "easy_to_borrow": True,
    "fractionable": True,
    "attributes": ["has_options"],          # must be dropped
    "maintenance_margin_requirement": 30,   # must be dropped
}

NEWS_BODY = {
    "news": [
        {
            "id": 1,
            "headline": "Apple ships something",
            "summary": "Short summary.",
            "source": "benzinga",
            "created_at": "2026-11-18T10:00:00Z",
            "url": "https://example.com/a",
            "symbols": ["AAPL"],       # must be dropped
            "author": "x",             # must be dropped
        }
    ],
    "next_page_token": None,
}

SEC_TICKERS_BODY = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
}

SEC_SUBMISSIONS_BODY = {
    "cik": "0000320193",
    "name": "Apple Inc.",
    "sicDescription": "Electronic Computers",
    "stateOfIncorporation": "CA",
    "website": "https://www.apple.com",
    "tickers": ["AAPL"],
}


def ok_routes():
    return {
        ASSET_URL: ASSET_BODY,
        NEWS_URL: NEWS_BODY,
        SEC_TICKERS_URL: SEC_TICKERS_BODY,
        SEC_SUBMISSIONS_PREFIX: SEC_SUBMISSIONS_BODY,
    }


@pytest.fixture
def company_info():
    """Fresh module-level caches for every test."""
    import analysis.company_info as ci

    ci.clear_caches()
    yield ci
    ci.clear_caches()


# ─────────────────────────────────────────────────────────────────────────
# Module-level tests (DI, no network)
# ─────────────────────────────────────────────────────────────────────────

def test_all_sources_ok_keeps_only_whitelisted_fields(company_info, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    fake = FakeHTTP(ok_routes())

    result = company_info.get_company_info("AAPL", http_get=fake)

    assert result["symbol"] == "AAPL"
    assert result["errors"] == []
    assert set(result["alpaca"]) == set(company_info.ASSET_FIELDS)
    assert "attributes" not in result["alpaca"]
    assert result["alpaca"]["name"] == "Apple Inc. Common Stock"
    assert set(result["sec"]) == set(company_info.SEC_FIELDS)
    assert result["sec"]["sicDescription"] == "Electronic Computers"
    assert len(result["news"]) == 1
    assert set(result["news"][0]) == set(company_info.NEWS_FIELDS)
    assert "symbols" not in result["news"][0]

    # every call used timeout=5
    assert fake.calls
    assert all(kw.get("timeout") == 5 for _, kw in fake.calls)


def test_sec_failure_still_returns_alpaca_and_news(company_info, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    routes = ok_routes()
    routes[SEC_SUBMISSIONS_PREFIX] = FakeResponse({"error": "boom"}, status_code=500)
    fake = FakeHTTP(routes)

    result = company_info.get_company_info("AAPL", http_get=fake)

    assert result["alpaca"]["name"] == "Apple Inc. Common Stock"
    assert len(result["news"]) == 1
    assert result["sec"] is None
    assert any(e.startswith("sec:") for e in result["errors"])


def test_sec_ticker_map_failure_is_isolated(company_info, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    routes = ok_routes()
    routes[SEC_TICKERS_URL] = FakeResponse({}, status_code=503)
    fake = FakeHTTP(routes)

    result = company_info.get_company_info("AAPL", http_get=fake)

    assert result["alpaca"] is not None
    assert result["news"]
    assert result["sec"] is None
    assert any(e.startswith("sec:") for e in result["errors"])


def test_news_failure_returns_empty_news_and_error(company_info, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    routes = ok_routes()

    def boom(url, **kw):
        raise TimeoutError("timed out")

    routes[NEWS_URL] = boom
    fake = FakeHTTP(routes)

    result = company_info.get_company_info("AAPL", http_get=fake)

    assert result["news"] == []
    assert result["alpaca"] is not None
    assert any(e.startswith("alpaca_news:") for e in result["errors"])


def test_missing_alpaca_env_produces_errors_not_crash(company_info, monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    fake = FakeHTTP(ok_routes())

    result = company_info.get_company_info("AAPL", http_get=fake)

    assert result["alpaca"] is None
    assert result["news"] == []
    assert any("not configured" in e for e in result["errors"])
    assert result["sec"] is not None  # SEC needs no Alpaca creds


def test_unknown_ticker_in_sec_map(company_info, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    fake = FakeHTTP(ok_routes())

    result = company_info.get_company_info("ZZZZ", http_get=fake)

    assert result["sec"] is None
    assert any("ticker not in SEC map" in e for e in result["errors"])


def test_second_call_within_ttl_makes_no_new_http_calls(company_info, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    fake = FakeHTTP(ok_routes())
    clock = {"t": 1_000_000.0}

    first = company_info.get_company_info("AAPL", http_get=fake, now=lambda: clock["t"])
    calls_after_first = fake.count
    assert calls_after_first > 0

    clock["t"] += 60  # well inside both TTLs
    second = company_info.get_company_info("AAPL", http_get=fake, now=lambda: clock["t"])

    assert fake.count == calls_after_first
    assert second["alpaca"] == first["alpaca"]
    assert second["news"] == first["news"]


def test_news_cache_expires_independently(company_info, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    fake = FakeHTTP(ok_routes())
    clock = {"t": 1_000_000.0}

    company_info.get_company_info("AAPL", http_get=fake, now=lambda: clock["t"])
    calls_after_first = fake.count

    clock["t"] += company_info.NEWS_TTL + 1  # news stale, profile still fresh
    company_info.get_company_info("AAPL", http_get=fake, now=lambda: clock["t"])

    new_urls = fake.urls[calls_after_first:]
    assert len(new_urls) == 1
    assert new_urls[0].startswith(NEWS_URL)


# ─────────────────────────────────────────────────────────────────────────
# Endpoint tests (FastAPI TestClient, dashboard fixture pattern copied
# from tests/test_bot001_dashboard_status.py)
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import os
    import tempfile

    sys.path.insert(0, str(SRC_ROOT))
    import src.database.sqlite_db as sqlite_mod_src
    import database.sqlite_db as sqlite_mod

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    new_path = type(sqlite_mod.DB_PATH)(tmp.name)
    monkeypatch.setattr(sqlite_mod, "DB_PATH", new_path)
    monkeypatch.setattr(sqlite_mod_src, "DB_PATH", new_path)
    sqlite_mod.sqlite_db._init_schema()

    import dashboard as dash_module
    dash_module.trading_client = MagicMock()

    from fastapi.testclient import TestClient
    with TestClient(dash_module.app) as c:
        yield c, dash_module

    try:
        os.unlink(tmp.name)
    except OSError:
        pass


def test_endpoint_bad_symbol_returns_400(client):
    c, _ = client
    r = c.get("/api/company/AAPL1")
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid symbol"


def test_endpoint_ok_returns_all_keys(client, company_info, monkeypatch):
    c, dash_module = client
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    fake = FakeHTTP(ok_routes())
    # dashboard imports the module lazily, so patching the module reference is
    # what the endpoint's ``http_get=requests.get`` default sees.
    monkeypatch.setattr(company_info, "requests", MagicMock(get=fake))
    # Endpoint calls get_company_info(symbol) with no http_get; the module
    # falls back to its own ``requests`` attribute, so patch that.
    monkeypatch.setattr(company_info, "requests", MagicMock(get=fake))

    r = c.get("/api/company/aapl")
    print("DEBUG errors:", r.json().get("errors"))

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"symbol", "alpaca", "sec", "news", "errors"}
    assert body["symbol"] == "AAPL"          # upper-cased by the endpoint
    assert body["alpaca"]["exchange"] == "NASDAQ"
    assert set(body["sec"]) == set(company_info.SEC_FIELDS)
    assert body["news"][0]["source"] == "benzinga"
    assert body["errors"] == []
    assert all(kw.get("timeout") == 5 for _, kw in fake.calls)


def test_endpoint_source_failure_is_still_200(client, company_info, monkeypatch):
    c, dash_module = client
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    routes = ok_routes()
    routes[SEC_SUBMISSIONS_PREFIX] = FakeResponse({"x": 1}, status_code=502)
    fake = FakeHTTP(routes)
    monkeypatch.setattr(company_info, "requests", MagicMock(get=fake))
    # Endpoint calls get_company_info(symbol) with no http_get; the module
    # falls back to its own ``requests`` attribute, so patch that.
    monkeypatch.setattr(company_info, "requests", MagicMock(get=fake))

    r = c.get("/api/company/AAPL")

    assert r.status_code == 200
    body = r.json()
    assert body["alpaca"] is not None
    assert body["news"]
    assert body["sec"] is None
    assert body["errors"]
