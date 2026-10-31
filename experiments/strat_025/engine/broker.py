"""Broker abstraction: thin Alpaca PAPER REST wrapper + mock-friendly interface.

Only PAPER: base URL is hard-pinned to https://paper-api.alpaca.markets in
config.PAPER_TRADING_URL; guard.enforce_paper_only() re-checks it at startup.
Every order carries a client_order_id prefixed with the sl_short tag.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional, Protocol

import requests

from .config import ORDER_TAG, PAPER_DATA_URL, PAPER_TRADING_URL


def tagged_client_order_id(kind: str, symbol: str) -> str:
    """e.g. sl_short-entry-AAPL-3f2a1c — every order is tagged sl_short."""
    return f"{ORDER_TAG}-{kind}-{symbol}-{uuid.uuid4().hex[:8]}"


class Broker(Protocol):
    def get_account_equity(self) -> float: ...
    def is_market_open(self) -> bool: ...
    def get_clock(self) -> dict: ...
    def submit_market_buy(self, symbol: str, qty: float, client_order_id: str) -> dict: ...
    def submit_stop_sell(self, symbol: str, qty: float, stop_price: float,
                         client_order_id: str) -> dict: ...
    def submit_market_sell(self, symbol: str, qty: float, client_order_id: str) -> dict: ...
    def wait_for_fill(self, order_id: str, timeout_sec: float = 20.0) -> Optional[dict]: ...
    def get_positions(self) -> list[dict]: ...
    def cancel_order(self, order_id: str) -> None: ...


class AlpacaPaperBroker:
    """REST client for the Alpaca PAPER trading API. Never live."""

    def __init__(self, key: str, secret: str,
                 base_url: str = PAPER_TRADING_URL,
                 data_url: str = PAPER_DATA_URL):
        if "paper" not in base_url:
            raise ValueError("refusing non-paper base_url")
        self.base = base_url.rstrip("/")
        self.data = data_url.rstrip("/")
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    def _req(self, method: str, path: str, base: str | None = None, **kw):
        r = requests.request(method, f"{(base or self.base)}{path}",
                             headers=self.h, timeout=30, **kw)
        if r.status_code == 429:
            time.sleep(10)
            return self._req(method, path, base, **kw)
        r.raise_for_status()
        return r.json() if r.content else {}

    # --- account / clock ---
    def get_account_equity(self) -> float:
        acct = self._req("GET", "/v2/account")
        return float(acct["equity"])

    def get_clock(self) -> dict:
        return self._req("GET", "/v2/clock")

    def is_market_open(self) -> bool:
        return bool(self.get_clock().get("is_open"))

    # --- orders ---
    def _submit(self, body: dict) -> dict:
        body["client_order_id"] = body["client_order_id"][:48]
        return self._req("POST", "/v2/orders", json=body)

    def submit_market_buy(self, symbol: str, qty: float, client_order_id: str) -> dict:
        return self._submit({"symbol": symbol, "qty": str(qty), "side": "buy",
                             "type": "market", "time_in_force": "day",
                             "client_order_id": client_order_id})

    def submit_stop_sell(self, symbol: str, qty: float, stop_price: float,
                         client_order_id: str) -> dict:
        return self._submit({"symbol": symbol, "qty": str(qty), "side": "sell",
                             "type": "stop", "stop_price": f"{stop_price:.2f}",
                             "time_in_force": "gtc",
                             "client_order_id": client_order_id})

    def submit_market_sell(self, symbol: str, qty: float, client_order_id: str) -> dict:
        return self._submit({"symbol": symbol, "qty": str(qty), "side": "sell",
                             "type": "market", "time_in_force": "day",
                             "client_order_id": client_order_id})

    def get_order(self, order_id: str) -> dict:
        return self._req("GET", f"/v2/orders/{order_id}")

    def wait_for_fill(self, order_id: str, timeout_sec: float = 20.0) -> Optional[dict]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            o = self.get_order(order_id)
            if o.get("status") == "filled":
                return o
            if o.get("status") in ("canceled", "cancelled", "rejected", "expired"):
                return o
            time.sleep(1.0)
        return None

    def get_positions(self) -> list[dict]:
        return self._req("GET", "/v2/positions")

    def cancel_order(self, order_id: str) -> None:
        self._req("DELETE", f"/v2/orders/{order_id}")

    # --- data ---
    def latest_quote(self, symbol: str) -> Optional[dict]:
        try:
            j = self._req("GET", f"/v2/stocks/{symbol}/snapshots",
                          base=self.data)  # falls back below on plain envs
        except Exception:
            j = None
        if not j:
            return None
        snap = (j or {}).get(symbol) or {}
        q = snap.get("latestQuote") or {}
        if q.get("ap") is not None and q.get("bp") is not None:
            return {"ask": float(q["ap"]), "bid": float(q["bp"])}
        return None
