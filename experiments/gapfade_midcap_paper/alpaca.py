"""GAPFADE-MIDCAP-PAPER — Alpaca PAPER REST client (orders + market data).

PAPER ONLY. The trading base URL is the hard-locked constant from
guards.PAPER_API_URL ("https://paper-api.alpaca.markets"); this module has no
way to point it anywhere else. Market data uses Alpaca's public data API
(https://data.alpaca.markets) with the same paper key pair.

Client style mirrors /root/strat-swing/experiments/strat_023/live_paper_runner.py:
  * plain `requests` + REST (same interpreter compatibility, no SDK magic),
  * credentials from the repo's existing PAPER source, in this order:
      1. ALPACA_API_KEY / ALPACA_API_SECRET in the environment,
      2. the existing read-only workspace paper .env
         (/root/.openclaw/workspace/trading-bot/.env),
    neither of which is ever copied, printed or committed,
  * order lookup by client_order_id only via
    GET /v2/orders:by_client_order_id (GET /v2/orders/{id} takes Alpaca's
    order id only),
  * 429 -> back off and retry; 401/403 -> fail loudly.

Orders are always OPG (market-on-open): {type: market, time_in_force: opg}.
They are in before 09:28 ET and are NEVER cancelled/re-priced after the open
(an unfilled OPG order is simply reported as missed).

Usage evidence (part 2 deliverable 1): every payload the runner consumed can
be dumped for the README/diagnostics with `--dump-json`, which prints only
symbols and prices — never account numbers or order ids.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from experiments.gapfade_midcap_paper import guards

PAPER_CRED_ENV = ("ALPACA_API_KEY", "ALPACA_API_SECRET")
WORKSPACE_PAPER_ENV = Path("/root/.openclaw/workspace/trading-bot/.env")
DATA_API_URL = "https://data.alpaca.markets"
COID_PREFIX = "gf_mid-"
ORDER_TYPE = "market"
ORDER_TIF = "opg"
SNAPSHOT_BATCH_SIZE = 200      # symbols per /v2/stocks/snapshots request


class AlpacaError(RuntimeError):
    """Transport/API failure; the caller decides whether to skip or stop."""


class AuthError(AlpacaError):
    """401/403 from Alpaca — never retried, never masked as 'pending'."""


# --- credentials ---------------------------------------------------------
def load_paper_credentials(env: dict | None = None,
                           env_file: str | Path | None = None,
                           ) -> tuple[str, str]:
    """(key, secret) from the environment, else the read-only paper .env.

    Values are returned to the caller only; nothing here ever logs them.
    """
    env = os.environ if env is None else env
    key, sec = env.get(PAPER_CRED_ENV[0]), env.get(PAPER_CRED_ENV[1])
    if key and sec:
        return key, sec
    path = Path(env_file) if env_file else WORKSPACE_PAPER_ENV
    if not path.exists():
        raise AuthError(
            "paper credentials unavailable: set ALPACA_API_KEY / "
            "ALPACA_API_SECRET or provide the workspace paper .env")
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            name, _, value = line.partition("=")
            name, value = name.strip(), value.strip().strip('"').strip("'")
            if name == PAPER_CRED_ENV[0] and not key:
                key = value
            elif name == PAPER_CRED_ENV[1] and not sec:
                sec = value
    if not (key and sec):
        raise AuthError("paper credentials missing from the workspace .env")
    return key, sec


# --- REST client ---------------------------------------------------------
class AlpacaPaperClient:
    """Minimal REST client; `session` is injectable so tests never touch
    the network (a fake with get/post returning canned JSON suffices)."""

    def __init__(self, key: str, secret: str, session=None,
                 base_url: str | None = None,
                 data_url: str = DATA_API_URL):
        url = guards.PAPER_API_URL if base_url is None else str(base_url)
        if url != guards.PAPER_API_URL:
            raise AlpacaError(
                f"trading base URL must be exactly {guards.PAPER_API_URL} "
                "(paper account only)")
        self.base = url.rstrip("/")
        self.data_url = data_url.rstrip("/")
        self.headers = {"APCA-API-KEY-ID": key,
                        "APCA-API-SECRET-KEY": secret}
        if session is None:
            import requests
            session = requests.Session()
        self.sess = session

    # -- plumbing --
    def _request(self, method: str, url: str, params=None, body=None,
                 retries: int = 2):
        delay = 5
        for attempt in range(retries + 1):
            resp = self.sess.request(method, url, headers=self.headers,
                                     params=params, json=body, timeout=60)
            if resp.status_code == 429 and attempt < retries:
                time.sleep(delay)
                delay *= 3
                continue
            if resp.status_code in (401, 403):
                raise AuthError(
                    f"paper API auth rejected ({resp.status_code})")
            return resp
        return resp

    def get(self, url: str, params=None):
        resp = self._request("GET", url, params=params)
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def post(self, url: str, body: dict):
        return self._request("POST", url, body=body)

    # -- account / positions --
    def account(self) -> dict:
        return self.get(f"{self.base}/v2/account")

    def position(self, symbol: str) -> dict | None:
        resp = self._request("GET", f"{self.base}/v2/positions/{symbol}")
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise AlpacaError(
                f"GET position {symbol} failed ({resp.status_code})")
        return resp.json() if resp.text else None

    # -- orders --
    def submit_order(self, body: dict) -> dict:
        """POST /v2/orders. The caller has already passed check_paper_only."""
        if not str(body.get("client_order_id", "")).startswith(COID_PREFIX):
            raise AlpacaError(
                f"refusing to submit an order without the {COID_PREFIX} "
                "client_order_id prefix")
        # Every submit goes through the one guard path, again, right here:
        guards.check_paper_only()
        resp = self.post(f"{self.base}/v2/orders", body)
        if resp.status_code == 422 and "client_order_id" in (resp.text or ""):
            # replayed identical key (Alpaca's own idempotency) -> look it up
            existing = self.order_by_client_id(body["client_order_id"])
            if existing is not None:
                return existing
        if resp.status_code >= 400:
            raise AlpacaError(
                f"order rejected ({resp.status_code}): {(resp.text or '')[:300]}")
        return resp.json()

    def order_by_client_id(self, client_order_id: str) -> dict | None:
        resp = self._request(
            "GET", f"{self.base}/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id})
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise AlpacaError(
                f"lookup {client_order_id} failed ({resp.status_code})")
        return resp.json() if resp.text else None

    # -- market data --
    def snapshot_prices(self, symbols: list[str]) -> dict:
        """GET /v2/stocks/snapshots -> {SYMBOL: {latestTrade, minuteBar, ...}}.

        The former beta snapshot endpoint is dead (404). The v2
        response has the snapshot objects at the TOP level (no "snapshots"
        wrapper); the per-symbol fields the runner reads (latestTrade.p,
        minuteBar.c) are unchanged. Requests are batched:
        SNAPSHOT_BATCH_SIZE symbols per call.
        """
        syms = [str(s).upper() for s in symbols if s]
        out: dict = {}
        for i in range(0, len(syms), SNAPSHOT_BATCH_SIZE):
            batch = syms[i:i + SNAPSHOT_BATCH_SIZE]
            params = {"symbols": ",".join(batch), "feed": "iex"}
            page = self.get(f"{self.data_url}/v2/stocks/snapshots",
                            params=params)
            if not isinstance(page, dict):
                continue
            for sym, snap in page.items():
                if isinstance(snap, dict):
                    out[str(sym).upper()] = snap
        return out

    def daily_bars(self, symbols: list[str], start: str, end: str,
                   ) -> dict:
        """GET /v2/stocks/bars (daily, split-adjusted) ->
        {symbol: [bar, ...]} with bars ascending by date."""
        out: dict[str, list] = {}
        symbols = list(symbols)
        token = None
        for _ in range(50):                      # bounded pagination
            params = {"symbols": ",".join(symbols), "timeframe": "1Day",
                      "start": start, "end": end, "adjustment": "split",
                      "feed": "iex", "limit": 10000}
            if token:
                params["page_token"] = token
            page = self.get(f"{self.data_url}/v2/stocks/bars", params=params)
            for sym, bars in (page.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars or [])
            token = page.get("next_page_token")
            if not token:
                break
        for sym in out:
            out[sym].sort(key=lambda b: b.get("t", ""))
        return out
