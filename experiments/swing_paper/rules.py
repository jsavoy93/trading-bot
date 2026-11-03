"""STRAT-SWING-PAPER — per-strategy order tagging + allocation rules.

Pure, unit-testable logic for the paper swing executor (C4 dip-buy and
RSI(2) pro-swing). No network, no I/O. The runner (runner.py) injects
Alpaca paper fetchers/submitters; this module decides labels, overlap,
daily caps, and budget slices.

Strategy tokens (also the client_order_id prefixes):
  c4   — STRAT-022 C4 dip-buy  (RSI(2)<10, close>SMA200, 3.0xATR stop, 10d time)
  rsi2 — STRAT-026 rsi2_regime (RSI(2)<10, SPY>SMA200 regime, 2R partial)

Frozen research parameters are re-used EXACTLY; nothing here relaxes a
BUY/entry threshold.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- strategy tokens -------------------------------------------------------
STRATEGY_TOKENS = ("c4", "rsi2")
C4 = "c4"
RSI2 = "rsi2"

# --- label format ----------------------------------------------------------
# <strategy>-<SYMBOL>-<MMDD>-<n>            e.g. c4-AMD-1102-1
# stop orders: same prefix plus -stop       e.g. c4-AMD-1102-1-stop
# Alpaca client_order_id limit is 48 chars: 4(token)+1+5(sym)+1+4+1+5(seq)
# = 21 chars for entry labels and 26 for stop labels — always within limits
# for symbols up to 5 chars (equities). Longer symbols raise ValueError.
ALPACA_CLIENT_ORDER_ID_MAX = 48
STOP_SUFFIX = "-stop"


def make_client_order_id(strategy: str, symbol: str, mmdd: str, n: int) -> str:
    """Entry label: <strategy>-<SYMBOL>-<MMDD>-<n>. Raises on bad inputs."""
    _validate_token(strategy)
    sym = _validate_symbol(symbol)
    if not (isinstance(mmdd, str) and len(mmdd) == 4 and mmdd.isdigit()
            and 1 <= int(mmdd[:2]) <= 12 and 1 <= int(mmdd[2:]) <= 31):
        raise ValueError(f"mmdd must be a valid MMDD string, got {mmdd!r}")
    if not (isinstance(n, int) and n >= 1):
        raise ValueError(f"sequence n must be an int >= 1, got {n!r}")
    coid = f"{strategy}-{sym}-{mmdd}-{n}"
    _check_length(coid, include_stop=False)
    return coid


def make_stop_client_order_id(strategy: str, symbol: str, mmdd: str, n: int) -> str:
    """Stop-loss label: entry label + '-stop'."""
    coid = make_client_order_id(strategy, symbol, mmdd, n) + STOP_SUFFIX
    _check_length(coid, include_stop=True)
    return coid


def make_labels(strategy: str, symbol: str, mmdd: str, n: int) -> tuple[str, str]:
    """(entry_client_order_id, stop_client_order_id) for one order pair."""
    return (
        make_client_order_id(strategy, symbol, mmdd, n),
        make_stop_client_order_id(strategy, symbol, mmdd, n),
    )


def next_sequence(existing_labels, strategy: str, symbol: str, mmdd: str) -> int:
    """Next per-symbol-per-day sequence number given existing labels."""
    _validate_token(strategy)
    sym = _validate_symbol(symbol)
    prefix = f"{strategy}-{sym}-{mmdd}-"
    best = 0
    for label in existing_labels or []:
        s = str(label)
        if s.startswith(prefix):
            tail = s[len(prefix):]
            tail = tail[:-len(STOP_SUFFIX)] if tail.endswith(STOP_SUFFIX) else tail
            if tail.isdigit():
                best = max(best, int(tail))
    return best + 1


def parse_label(label: str) -> dict:
    """Parse a label into {strategy, symbol, mmdd, n, is_stop}. Raises ValueError."""
    s = str(label)
    is_stop = s.endswith(STOP_SUFFIX)
    if is_stop:
        s = s[: -len(STOP_SUFFIX)]
    parts = s.split("-")
    if len(parts) != 4:
        raise ValueError(f"malformed label: {label!r}")
    strategy, symbol, mmdd, n = parts
    if strategy not in STRATEGY_TOKENS:
        raise ValueError(f"unknown strategy token {strategy!r} in {label!r}")
    if not (len(mmdd) == 4 and mmdd.isdigit() and n.isdigit() and int(n) >= 1):
        raise ValueError(f"malformed label: {label!r}")
    return {"strategy": strategy, "symbol": symbol, "mmdd": mmdd,
            "n": int(n), "is_stop": is_stop}


def label_prefix_for_day(strategy: str, symbol: str, mmdd: str) -> str:
    """Prefix matching every label a strategy placed on a symbol/day
    (entry and stop): '<strategy>-<SYMBOL>-<MMDD>-'. Used by the overlap rule."""
    _validate_token(strategy)
    return f"{strategy}-{_validate_symbol(symbol)}-{mmdd}-"


def _validate_token(strategy: str) -> str:
    if strategy not in STRATEGY_TOKENS:
        raise ValueError(f"strategy must be one of {STRATEGY_TOKENS}, got {strategy!r}")
    return strategy


def _validate_symbol(symbol: str) -> str:
    sym = str(symbol).strip().upper()
    if not sym or not sym.isalnum() or len(sym) > 5:
        raise ValueError(f"symbol must be 1-5 alphanumeric chars, got {symbol!r}")
    return sym


def _check_length(coid: str, include_stop: bool) -> None:
    if len(coid) > ALPACA_CLIENT_ORDER_ID_MAX:
        raise ValueError(
            f"client_order_id too long ({len(coid)} > {ALPACA_CLIENT_ORDER_ID_MAX}): {coid}"
        )


# --- overlap rule ----------------------------------------------------------
def rsi2_overlap_blocked(c4_positions, c4_open_orders, symbol: str, mmdd: str) -> bool:
    """True when RSI(2) must SKIP `symbol` because C4 already holds it or
    has any order on it that day (labels checked by day prefix).

    c4_positions: iterable of position symbols (or dicts with 'symbol')
    c4_open_orders: iterable of dicts with client_order_id (and optionally
                    'symbol'); label prefix decides, per the task spec.
    """
    sym = str(symbol).strip().upper()
    for pos in c4_positions or []:
        p = pos["symbol"] if isinstance(pos, dict) else pos
        if str(p).strip().upper() == sym:
            return True
    prefix = label_prefix_for_day(C4, sym, mmdd)
    for order in c4_open_orders or []:
        coid = order.get("client_order_id") if isinstance(order, dict) else order
        if coid and str(coid).startswith(prefix):
            return True
    return False


# --- RSI(2) daily cap ------------------------------------------------------
RSI2_DAILY_CAP = 4


def apply_rsi2_daily_cap(candidates, cap: int = RSI2_DAILY_CAP) -> list:
    """Rank RSI(2) BUY candidates by lowest RSI(2) (ties: symbol ASC) and
    keep at most `cap`. Candidates: dicts with 'symbol' and 'rsi2'.
    Returns the selected candidates in ranked order."""
    if cap < 0:
        raise ValueError("cap must be >= 0")
    ranked = sorted(
        candidates or [],
        key=lambda c: (float(c["rsi2"]), str(c["symbol"]).upper()),
    )
    return ranked[:cap]


# --- budget slices ---------------------------------------------------------
SHORT_TERM_BUCKET_PCT = 0.30
C4_SHARE = 0.50
RSI2_SHARE = 0.50


@dataclass
class BudgetSlices:
    """Per-strategy dollar budgets inside the short-term bucket.

    bucket = portfolio_value * short_term_bucket_pct (default 30%).
    Each strategy sizes only from its own slice; open notional + pending
    order notional for that strategy counts against its slice.
    """
    portfolio_value: float
    short_term_bucket_pct: float = SHORT_TERM_BUCKET_PCT
    c4_share: float = C4_SHARE
    rsi2_share: float = RSI2_SHARE

    def __post_init__(self):
        if self.portfolio_value < 0:
            raise ValueError("portfolio_value must be >= 0")
        for name, v in (("short_term_bucket_pct", self.short_term_bucket_pct),
                        ("c4_share", self.c4_share),
                        ("rsi2_share", self.rsi2_share)):
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"{name} must be within [0, 1], got {v}")
        if abs(self.c4_share + self.rsi2_share - 1.0) > 1e-9:
            raise ValueError("c4_share + rsi2_share must equal 1.0")

    @property
    def bucket_dollars(self) -> float:
        return self.portfolio_value * self.short_term_bucket_pct

    def slice_dollars(self, strategy: str) -> float:
        _validate_token(strategy)
        share = self.c4_share if strategy == C4 else self.rsi2_share
        return self.bucket_dollars * share

    def remaining_dollars(self, strategy: str, open_notional: float = 0.0,
                          pending_notional: float = 0.0) -> float:
        """Slice minus that strategy's own open + pending notional (>= 0)."""
        used = max(0.0, float(open_notional)) + max(0.0, float(pending_notional))
        return max(0.0, self.slice_dollars(strategy) - used)

    def size_order(self, strategy: str, price: float, qty_or_none: float = None,
                   open_notional: float = 0.0, pending_notional: float = 0.0) -> int:
        """Whole-share quantity that fits the strategy's own remaining slice.
        qty_or_none: optional desired qty (cap applied on top)."""
        if price <= 0:
            raise ValueError("price must be > 0")
        remaining = self.remaining_dollars(strategy, open_notional, pending_notional)
        qty = int(remaining // price)
        if qty_or_none is not None:
            qty = min(qty, int(qty_or_none))
        return max(0, qty)
