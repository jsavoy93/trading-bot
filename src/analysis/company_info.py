"""Company profile lookups for the dashboard ``/api/company/{symbol}`` endpoint.

Pure module: no FastAPI, no dashboard/DB imports. Three independent
sources are queried and every one of them is isolated behind its own
try/except, so a failing/slow/unauthorised source can never raise to the
caller — it only appends a short string to ``result["errors"]``.

Sources
-------
a) Alpaca asset   — ``/v2/assets/{symbol}`` (paper host)
b) Alpaca news    — ``/v1beta1/news?symbols={symbol}&limit=5``
c) SEC (free)     — ticker→CIK map (cached 24h) then submissions JSON

Caching
-------
Module-level dict keyed by symbol. Profile parts (Alpaca asset + SEC)
live 6h, news lives 15 min. Only successful parts are cached, so a
transient failure on the 6h profile is retried on the next call.

DI: ``http_get`` and ``now`` are injectable so tests never touch the
network or the wall clock.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

import requests

SYMBOL_RE = re.compile(r"^[A-Z.]{1,10}$")

ALPACA_DATA_BASE = "https://data.alpaca.markets"
ALPACA_PAPER_BASE = "https://paper-api.alpaca.markets"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_USER_AGENT = "SmartBot dashboard jpsavoy93@gmail.com"

REQUEST_TIMEOUT = 5

PROFILE_TTL = 6 * 60 * 60          # 6 hours
NEWS_TTL = 15 * 60                 # 15 minutes
TICKER_MAP_TTL = 24 * 60 * 60      # 24 hours

ASSET_FIELDS = (
    "name",
    "exchange",
    "class",
    "status",
    "tradable",
    "shortable",
    "easy_to_borrow",
    "fractionable",
)
NEWS_FIELDS = ("headline", "summary", "source", "created_at", "url")
SEC_FIELDS = ("sicDescription", "stateOfIncorporation", "website")

# {symbol: {"alpaca": dict|None, "sec": dict|None, "ts": float}}
_PROFILE_CACHE: Dict[str, Dict[str, Any]] = {}
# {symbol: {"news": list, "ts": float}}
_NEWS_CACHE: Dict[str, Dict[str, Any]] = {}
# {"data": {TICKER: cik}, "ts": float}
_TICKER_MAP_CACHE: Dict[str, Any] = {}


def _alpaca_headers() -> Dict[str, str]:
    """Alpaca auth headers. Raises KeyError when the env vars are missing."""
    return {
        "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
        "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET"],
    }


def _sec_headers() -> Dict[str, str]:
    return {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}


def _json_body(response: Any) -> Any:
    """Return the parsed body or raise (so callers can record an error)."""
    response.raise_for_status()
    return response.json()


def _lookup_asset(symbol: str, http_get: Callable, errors: List[str]) -> Optional[dict]:
    try:
        headers = _alpaca_headers()
        response = http_get(
            f"{ALPACA_PAPER_BASE}/v2/assets/{symbol}",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        body = _json_body(response)
    except KeyError:
        errors.append("alpaca_asset: ALPACA_API_KEY/ALPACA_API_SECRET not configured")
        return None
    except Exception as exc:  # noqa: BLE001 - one source must never raise
        errors.append(f"alpaca_asset: {type(exc).__name__}: {exc}"[:300])
        return None

    if not isinstance(body, dict):
        errors.append("alpaca_asset: unexpected response shape")
        return None
    return {key: body.get(key) for key in ASSET_FIELDS if key in body}


def _lookup_news(symbol: str, http_get: Callable, errors: List[str]) -> List[dict]:
    try:
        headers = _alpaca_headers()
        response = http_get(
            f"{ALPACA_DATA_BASE}/v1beta1/news",
            params={"symbols": symbol, "limit": 5},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        body = _json_body(response)
    except KeyError:
        errors.append("alpaca_news: ALPACA_API_KEY/ALPACA_API_SECRET not configured")
        return []
    except Exception as exc:  # noqa: BLE001
        errors.append(f"alpaca_news: {type(exc).__name__}: {exc}"[:300])
        return []

    items = body.get("news") if isinstance(body, dict) else None
    if not isinstance(items, list):
        errors.append("alpaca_news: unexpected response shape")
        return []
    return [
        {key: item.get(key) for key in NEWS_FIELDS}
        for item in items[:5]
        if isinstance(item, dict)
    ]


def _load_ticker_map(http_get: Callable, now: Callable[[], float]) -> Dict[str, int]:
    """Ticker→CIK map, cached 24h. Raises on failure (caller records it)."""
    cached = _TICKER_MAP_CACHE.get("data")
    cached_at = _TICKER_MAP_CACHE.get("ts", 0.0)
    if cached is not None and (now() - cached_at) < TICKER_MAP_TTL:
        return cached

    response = http_get(SEC_TICKERS_URL, headers=_sec_headers(), timeout=REQUEST_TIMEOUT)
    body = _json_body(response)

    mapping: Dict[str, int] = {}
    # Tolerant parse: the file is {"0": {...}, "1": {...}, ...} but we
    # accept a list of records too.
    records = body.values() if isinstance(body, dict) else (body if isinstance(body, list) else [])
    for record in records:
        if not isinstance(record, dict):
            continue
        ticker = record.get("ticker")
        cik = record.get("cik_str", record.get("cik"))
        try:
            cik_int = int(cik)
        except (TypeError, ValueError):
            continue
        if isinstance(ticker, str) and ticker:
            mapping[ticker.upper()] = cik_int

    if mapping:
        _TICKER_MAP_CACHE["data"] = mapping
        _TICKER_MAP_CACHE["ts"] = now()
    return mapping


def _lookup_sec(symbol: str, http_get: Callable, now: Callable[[], float], errors: List[str]) -> Optional[dict]:
    try:
        ticker_map = _load_ticker_map(http_get, now)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sec: ticker_map {type(exc).__name__}: {exc}"[:300])
        return None

    cik = ticker_map.get(symbol)
    if cik is None:
        errors.append("sec: ticker not in SEC map")
        return None

    try:
        response = http_get(
            SEC_SUBMISSIONS_URL.format(cik=cik),
            headers=_sec_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        body = _json_body(response)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sec: submissions {type(exc).__name__}: {exc}"[:300])
        return None

    if not isinstance(body, dict):
        errors.append("sec: unexpected response shape")
        return None
    profile = {key: body[key] for key in SEC_FIELDS if body.get(key)}
    return profile or None


def get_company_info(
    symbol: str,
    http_get: Optional[Callable[..., Any]] = None,
    now: Callable[[], float] = time.time,
) -> dict:
    """Return the dashboard company payload for ``symbol`` (never raises)."""
    if http_get is None:
        http_get = requests.get
    result: dict = {
        "symbol": symbol,
        "alpaca": None,
        "sec": None,
        "news": [],
        "errors": [],
    }
    current = now()

    profile = _PROFILE_CACHE.get(symbol)
    if profile is not None and (current - profile.get("ts", 0.0)) < PROFILE_TTL:
        result["alpaca"] = profile.get("alpaca")
        result["sec"] = profile.get("sec")
        profile_fresh = True
    else:
        profile_fresh = False

    if not profile_fresh:
        asset = _lookup_asset(symbol, http_get, result["errors"])
        sec = _lookup_sec(symbol, http_get, now, result["errors"])
        result["alpaca"] = asset
        result["sec"] = sec
        _PROFILE_CACHE[symbol] = {"alpaca": asset, "sec": sec, "ts": current}

    news_entry = _NEWS_CACHE.get(symbol)
    if news_entry is not None and (current - news_entry.get("ts", 0.0)) < NEWS_TTL:
        result["news"] = news_entry.get("news", [])
    else:
        news = _lookup_news(symbol, http_get, result["errors"])
        result["news"] = news
        _NEWS_CACHE[symbol] = {"news": news, "ts": current}

    return result


def clear_caches() -> None:
    """Reset every module-level cache (used by tests)."""
    _PROFILE_CACHE.clear()
    _NEWS_CACHE.clear()
    _TICKER_MAP_CACHE.clear()
