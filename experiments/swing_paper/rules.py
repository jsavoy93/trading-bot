"""STRAT-SWING-PAPER — per-strategy order tagging + allocation rules.

Pure, unit-testable logic for the paper swing executor. No network, no I/O.
The runner (runner.py) injects Alpaca paper fetchers/submitters; this module
decides labels, overlap/skip rules, daily caps, and budget sizing.

Strategy token:
  rsi2 — STRAT-026 rsi2_regime (RSI(2)<10, SPY>SMA200 regime, 2R partial,
         chandelier trail, 15-day time exit)

SCOPE DECISION (Josh, 2026-11-03): this runner trades RSI(2) ONLY. C4 order
placement was removed from this runner; C4 continues on the existing
strat023-c4-shadow timer (labels sl_short-..., state in live_paper.db).

Frozen research parameters are re-used EXACTLY; nothing here relaxes a
BUY/entry threshold.
"""
from __future__ import annotations

# --- strategy tokens -------------------------------------------------------
STRATEGY_TOKENS = ("rsi2",)
RSI2 = "rsi2"

# --- label format ----------------------------------------------------------
# <strategy>-<SYMBOL>-<YYYYMMDD>-<n>         e.g. rsi2-AMD-20261103-1
# stop orders:   same prefix plus -stop      e.g. rsi2-AMD-20261103-1-stop
# partial exits: stop label plus -p          e.g. rsi2-AMD-20261103-1-stop-p
# Alpaca client_order_id limit is 48 chars. The longest shape is
# 4(token)+1+8(symbol)+1+8(date)+1+5(seq)+7(-stop-p) = 35 chars.
ALPACA_CLIENT_ORDER_ID_MAX = 48
STOP_SUFFIX = "-stop"
PARTIAL_SUFFIX = "-p"


def _valid_ymd(ymd: str) -> str:
    ok = (isinstance(ymd, str) and len(ymd) == 8 and ymd.isdigit()
          and 1 <= int(ymd[:4]) <= 2100 and 1 <= int(ymd[4:6]) <= 12
          and 1 <= int(ymd[6:]) <= 31)
    if not ok:
        raise ValueError(f"ymd must be a valid YYYYMMDD string, got {ymd!r}")
    return ymd


def make_client_order_id(strategy: str, symbol: str, ymd: str, n: int) -> str:
    """Entry label: <strategy>-<SYMBOL>-<YYYYMMDD>-<n>. Raises on bad inputs."""
    _validate_token(strategy)
    sym = _validate_symbol(symbol)
    _valid_ymd(ymd)
    if not (isinstance(n, int) and n >= 1):
        raise ValueError(f"sequence n must be an int >= 1, got {n!r}")
    coid = f"{strategy}-{sym}-{ymd}-{n}"
    _check_length(coid)
    return coid


def make_stop_client_order_id(strategy: str, symbol: str, ymd: str, n: int) -> str:
    """Stop-loss label: entry label + '-stop'."""
    coid = make_client_order_id(strategy, symbol, ymd, n) + STOP_SUFFIX
    _check_length(coid)
    return coid


def make_partial_client_order_id(strategy: str, symbol: str, ymd: str, n: int) -> str:
    """Partial-exit limit label: stop label + '-p' (frozen shape: '-stop-p')."""
    coid = make_stop_client_order_id(strategy, symbol, ymd, n) + PARTIAL_SUFFIX
    _check_length(coid)
    return coid


def make_labels(strategy: str, symbol: str, ymd: str, n: int) -> tuple[str, str]:
    """(entry_client_order_id, stop_client_order_id) for one order pair."""
    return (
        make_client_order_id(strategy, symbol, ymd, n),
        make_stop_client_order_id(strategy, symbol, ymd, n),
    )


def next_sequence(existing_labels, strategy: str, symbol: str, ymd: str) -> int:
    """Next per-symbol-per-day sequence number given existing labels."""
    _validate_token(strategy)
    sym = _validate_symbol(symbol)
    prefix = f"{strategy}-{sym}-{ymd}-"
    best = 0
    for label in existing_labels or []:
        s = str(label)
        if s.startswith(prefix):
            tail = s[len(prefix):]
            for suf in (STOP_SUFFIX + PARTIAL_SUFFIX, STOP_SUFFIX):
                if tail.endswith(suf):
                    tail = tail[: -len(suf)]
            if tail.isdigit():
                best = max(best, int(tail))
    return best + 1


def parse_label(label: str) -> dict:
    """Parse a label into {strategy, symbol, ymd, n, kind} where kind is
    'entry' | 'stop' | 'partial'. Raises ValueError."""
    s = str(label)
    kind = "entry"
    if s.endswith(STOP_SUFFIX + PARTIAL_SUFFIX):
        s = s[: -len(STOP_SUFFIX + PARTIAL_SUFFIX)]
        kind = "partial"
    elif s.endswith(STOP_SUFFIX):
        s = s[: -len(STOP_SUFFIX)]
        kind = "stop"
    parts = s.split("-")
    if len(parts) != 4:
        raise ValueError(f"malformed label: {label!r}")
    strategy, symbol, ymd, n = parts
    if strategy not in STRATEGY_TOKENS:
        raise ValueError(f"unknown strategy token {strategy!r} in {label!r}")
    _validate_symbol(symbol)
    _valid_ymd(ymd)
    if not (n.isdigit() and int(n) >= 1):
        raise ValueError(f"malformed label: {label!r}")
    return {"strategy": strategy, "symbol": symbol, "ymd": ymd,
            "n": int(n), "kind": kind}


def label_prefix_for_day(strategy: str, symbol: str, ymd: str) -> str:
    """Prefix matching every label a strategy placed on a symbol/day."""
    _validate_token(strategy)
    return f"{strategy}-{_validate_symbol(symbol)}-{ymd}-"


def _validate_token(strategy: str) -> str:
    if strategy not in STRATEGY_TOKENS:
        raise ValueError(f"strategy must be one of {STRATEGY_TOKENS}, got {strategy!r}")
    return strategy


def _validate_symbol(symbol: str) -> str:
    sym = str(symbol).strip().upper()
    # '.' is required for class shares (BRK.B, BF.B); '-' is not allowed
    # because '-' is the label field separator.
    ok = sym and len(sym) <= 8 and all(ch.isalnum() or ch == "." for ch in sym)
    if not ok:
        raise ValueError(
            f"symbol must be 1-8 chars of A-Z/0-9/'.' (no '-'), got {symbol!r}")
    return sym


def _check_length(coid: str) -> None:
    if len(coid) > ALPACA_CLIENT_ORDER_ID_MAX:
        raise ValueError(
            f"client_order_id too long ({len(coid)} > {ALPACA_CLIENT_ORDER_ID_MAX}): {coid}"
        )


# --- skip / overlap rule ---------------------------------------------------
def symbol_in_positions(positions, symbol: str) -> bool:
    """True when `symbol` is held in the account (any strategy, any label)."""
    sym = str(symbol).strip().upper()
    for pos in positions or []:
        p = pos["symbol"] if isinstance(pos, dict) else pos
        if str(p).strip().upper() == sym:
            return True
    return False


def symbol_has_open_order(open_orders, symbol: str) -> bool:
    """True when `symbol` has any open/pending order (any label, including
    C4's sl_short-*). Matches on the order's symbol field OR any
    client_order_id embedding '-<SYMBOL>-' (defends against fake label-only
    order feeds)."""
    sym = str(symbol).strip().upper()
    for order in open_orders or []:
        if isinstance(order, dict):
            osym = str(order.get("symbol") or "").strip().upper()
            coid = str(order.get("client_order_id") or "")
        else:
            osym, coid = "", str(order)
        if osym == sym:
            return True
        if coid.startswith(("c4-", "rsi2-", "sl_short-")) and f"-{sym}-" in coid:
            return True
    return False


def rsi2_skip_blocked(positions, open_orders, symbol: str,
                      c4_signaled_symbols=None) -> bool:
    """True when RSI(2) must SKIP `symbol` because (a) it is held in the
    account, (b) it has any open/pending order (any label, including
    sl_short-* C4 shadow orders), or (c) C4 signaled it the same day
    (best-effort read from live_paper.db)."""
    if symbol_in_positions(positions, symbol):
        return True
    if symbol_has_open_order(open_orders, symbol):
        return True
    sym = str(symbol).strip().upper()
    for s in c4_signaled_symbols or []:
        if str(s).strip().upper() == sym:
            return True
    return False


# --- RSI(2) selection caps ---------------------------------------------------
RSI2_DAILY_CAP = 4
RSI2_MAX_OPEN_POSITIONS = 4
RSI2_PER_POSITION_CAP_PCT = 0.10


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


# --- budget sizing -----------------------------------------------------------
SHORT_TERM_BUCKET_PCT = 0.30


def rsi2_slice_dollars(portfolio_value: float,
                       bucket_pct: float = SHORT_TERM_BUCKET_PCT) -> float:
    if portfolio_value < 0:
        raise ValueError("portfolio_value must be >= 0")
    if not (0.0 <= bucket_pct <= 1.0):
        raise ValueError("bucket_pct must be within [0, 1]")
    return portfolio_value * bucket_pct


def size_rsi2_position(price: float, slice_dollars: float,
                       open_notional: float = 0.0,
                       pending_notional: float = 0.0,
                       per_position_cap_pct: float = RSI2_PER_POSITION_CAP_PCT,
                       open_positions: int = 0,
                       max_open_positions: int = RSI2_MAX_OPEN_POSITIONS) -> int:
    """Whole-share qty for ONE new RSI(2) position.

    Each position is capped at `per_position_cap_pct` (default 10%) of the
    slice-scale budget, so the first pick can never take the whole slice.
    Remaining slice (slice - open - pending notional) is the other cap.
    Returns 0 when max_open_positions is already reached.
    """
    if price <= 0:
        raise ValueError("price must be > 0")
    if not (0.0 < per_position_cap_pct <= 1.0):
        raise ValueError("per_position_cap_pct must be within (0, 1]")
    if open_positions >= max_open_positions:
        return 0
    cap_dollars = slice_dollars * per_position_cap_pct
    remaining = max(0.0, slice_dollars
                    - max(0.0, float(open_notional))
                    - max(0.0, float(pending_notional)))
    budget = min(cap_dollars, remaining)
    return max(0, int(budget // price))
