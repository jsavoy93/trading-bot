"""GAPFADE-MIDCAP-PAPER — broker boundary (part 2 wires it).

`MarketData` / `Broker` stay the protocols the runner is written against.
Part 2 implements them over Alpaca PAPER:
  - `marketdata.AlpacaMarketData`  (snapshots + daily bars + membership)
  - `AlpacaPaperBroker` (below)    (OPG market orders, gf_mid- ids)
Both are built by `make_data()` / `make_broker()`. They refuse to run
unless `guards.check_paper_only()` passes; the broker re-runs that guard
before every single submit.
"""

from __future__ import annotations

from typing import Any, Protocol

from experiments.gapfade_midcap_paper import alpaca, guards
# re-exported so the runner catches data failures without importing the
# data module (which pulls nothing heavy, but keeps one import surface)
from experiments.gapfade_midcap_paper.marketdata import (  # noqa: F401
    MarketDataError)


class MarketData(Protocol):
    """Read-only market inputs the runner needs (implemented in part 2)."""

    def expected_open(self, day: str) -> dict[str, float]: ...
    def prior_close(self, day: str) -> dict[str, float]: ...
    def official_bar(self, symbol: str, day: str) -> dict[str, float]: ...
    def true_ranges(self, symbol: str, through_day: str,
                    n: int = 15) -> list[float]: ...
    def eligible_clean(self, day: str) -> list[str]: ...
    def vols(self, day: str, symbols: list[str]) -> dict[str, float]: ...


class Broker(Protocol):
    """Order-submitting interface; part 2 implements it for PAPER only."""

    def submit_opg_buy(self, symbol: str, qty: int,
                       client_order_id: str) -> dict: ...
    def submit_opg_sell(self, symbol: str, qty: int,
                        client_order_id: str) -> dict: ...
    def buying_power(self) -> float: ...
    def equity(self) -> float: ...


class NotWired(RuntimeError):
    """Raised by the part-1 stubs."""


class UnwiredMarketData:
    def __getattr__(self, name: str):
        raise NotWired(
            f"MarketData.{name} is not wired (no client supplied).")


class UnwiredBroker:
    def __getattr__(self, name: str):
        raise NotWired(
            f"Broker.{name} is not wired; part 2 provides PaperBroker "
            "behind check_paper_only().")


ORDER_TIF = alpaca.ORDER_TIF        # 'opg'
ORDER_TYPE = alpaca.ORDER_TYPE      # 'market'
COID_PREFIX = "gf_mid-"


class PaperBroker:
    """`Broker` over a PAPER Alpaca account. OPG market orders only.

    `submit_opg_buy` / `submit_opg_sell` return Alpaca's order dict
    ({id, client_order_id, status, ...}). `check_paper_only()` runs before
    every submit; a failure raises `guards.GuardError` (exit code 2 at the
    CLI). Orders are never cancelled or re-priced — a missed OPG order is
    reported as missed by the after-close pass.
    """

    def __init__(self, client, guard_kwargs: dict | None = None):
        self.client = client
        self._guard_kwargs = dict(guard_kwargs or {})

    def _submit(self, symbol: str, qty: int, side: str,
                client_order_id: str) -> dict:
        sym = str(symbol).upper().strip()
        if not sym:
            raise alpaca.AlpacaError("empty symbol")
        if not isinstance(qty, (int,)) or qty < 1:
            raise alpaca.AlpacaError(
                f"refusing {side} {sym}: qty must be a whole number >= 1")
        if not str(client_order_id).startswith(COID_PREFIX):
            raise alpaca.AlpacaError(
                f"refusing {side} {sym}: client_order_id must carry the "
                f"{COID_PREFIX} prefix")
        guards.check_paper_only(**self._guard_kwargs)
        body = {"symbol": sym, "qty": str(int(qty)), "side": side,
                "type": ORDER_TYPE, "time_in_force": ORDER_TIF,
                "client_order_id": client_order_id}
        return self.client.submit_order(body)

    def submit_opg_buy(self, symbol: str, qty: int,
                       client_order_id: str) -> dict:
        return self._submit(symbol, qty, "buy", client_order_id)

    def submit_opg_sell(self, symbol: str, qty: int,
                        client_order_id: str) -> dict:
        return self._submit(symbol, qty, "sell", client_order_id)

    def order_by_client_id(self, client_order_id: str) -> dict | None:
        return self.client.order_by_client_id(client_order_id)

    def buying_power(self) -> float:
        return float(self.client.account().get("buying_power") or 0.0)

    def equity(self) -> float:
        return float(self.client.account().get("equity") or 0.0)


# Part-1 contract kept: `make_broker()` / `make_data()` with no args return
# the not-wired stubs (they raise NotWired before any guard ever runs).


def make_client(args: Any = None):
    """PAPER client, so the GUARD always runs first on order paths."""
    guards.check_paper_only()
    key, secret = alpaca.load_paper_credentials()
    return alpaca.AlpacaPaperClient(key, secret)


def make_data(args: Any = None, client=None, cache_dir=None, members=None):
    """`marketdata.AlpacaMarketData`; `cache_dir` defaults next to state."""
    from experiments.gapfade_midcap_paper import marketdata
    from experiments.gapfade_midcap_paper.state import resolve_state_dir
    client = client or make_client(args)
    state_dir = getattr(args, "state_dir", None) if args is not None else None
    cache_dir = cache_dir or (resolve_state_dir(state_dir) / "cache")
    return marketdata.AlpacaMarketData(client, cache_dir, members=members)


def make_broker(args: Any = None, client=None) -> Broker:
    """Real PAPER broker. The part-1 stub is only returned when NO args are
    passed at all (`make_broker()`); the CLI always passes its namespace, so
    the CLI path is the real broker behind the guard."""
    if client is None and args is None:
        return UnwiredBroker()
    return PaperBroker(client or make_client(args))
