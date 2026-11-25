"""GAPFADE-MIDCAP-PAPER — broker boundary (part 2 plugs in here).

Part 1 ships NO broker implementation: both classes are stubs that raise.
Part 2 will implement PaperBroker against https://paper-api.alpaca.markets
only (guards.check_paper_only must pass before every submit).

Part 2 must ALSO supply real data behind MarketData:
  - universe(): current S&P 400 members
  - prior_close(symbols, day) / expected_open(symbols, day) for ~09:20 ET
  - official_bar(symbol, day) -> {'open','close'}
  - true_ranges(symbols, through_day) for ATR14[t-1]
  - earnings dates (rules.EarningsCalendar)
"""

from __future__ import annotations

from typing import Any, Protocol


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
            f"MarketData.{name} is not wired in part 1 (dry run needs only "
            "an injected data source).")


class UnwiredBroker:
    def __getattr__(self, name: str):
        raise NotWired(
            f"Broker.{name} is not implemented in part 1; part 2 wires the "
            "Alpaca PAPER broker behind check_paper_only().")


def make_data(args: Any = None) -> MarketData:
    return UnwiredMarketData()


def make_broker(args: Any = None) -> Broker:
    return UnwiredBroker()
