"""REBAL-60-30-10 — main-bot long-term allocation policy.

Approved split (Josh, 2026-11-09): 60% of account equity is the main
SmartBot's long-term budget, 30% belongs to the external short-term
swing runners (C4 at /root/strat-swing, RSI(2) at /root/deploy/rsi2-swing;
both share the SAME Alpaca paper account), and at least 10% of equity
must stay in cash.

Contracts (must NOT be weakened):

- **Pure math, no broker calls.** Every budget/cash function here is
  side-effect free and unit-testable. Broker reads happen in
  SmartTradingBot wrappers, never here.
- **Foreign positions are identified by evidence, never guessed.**
  Evidence sources: (1) broker order labels — any order whose
  client_order_id starts with a configured swing prefix
  (default "sl_short-" for C4, "rsi2-" for RSI(2)); (2) optional runner
  SQLite DBs exposing a positions table with status='open' (C4's
  live_paper.db). RSI(2)'s logs/state.json is deliberately NOT a
  source: its dry-run entries record unfilled shadow positions with
  synthetic order ids ("ord-1"), so only its labeled real orders are
  trustworthy.
- **Fail-closed ownership.** A broker position the main bot cannot
  prove it owns via its OWN trades ledger is NEVER sold, resized, or
  stop-lossed by the main bot. Unattributed (non-runner, non-ledger)
  positions are still counted toward the long-term budget — the bot
  under-deploys rather than over-deploys.
- **The cash floor is a hard BUY-side constraint.** cash - cost must
  stay >= cash_floor_pct * equity for every main-bot BUY. External
  runners are outside this bot's control.
- **No entry-rule changes.** This module sizes and gates ALLOCATION
  only. BUY eligibility (RSI / SMA / MACD / volume gates) is untouched.
- The ledger is authoritative for OWNERSHIP ("did this bot trade this
  symbol?"), the broker remains authoritative for real quantities.
  Nothing here sizes a SELL off ledger qty; ledger qty only CAPS a
  sell so the bot never sells more shares of a symbol than it can
  prove it bought.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Fallback label prefixes if the configured value cannot be parsed.
DEFAULT_SWING_ORDER_LABEL_PREFIXES: Tuple[str, ...] = ("sl_short-", "rsi2-")

#: Ledger row status that counts as a confirmed fill.
_FILLED_STATUS = "FILLED"


# ── Config parsing ────────────────────────────────────────────────────────────

def parse_json_list(raw: Any, fallback: Sequence[str]) -> Tuple[str, ...]:
    """Parse a JSON array-of-strings setting value into a tuple.

    Accepts an already-iterable sequence or a JSON string. Any
    malformed value logs a warning and returns the fallback (fail-closed
    to the approved defaults; never raises into a trading loop).
    """
    if raw is None:
        return tuple(fallback)
    candidates: Optional[List[Any]] = None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                candidates = parsed
        except (ValueError, TypeError):
            candidates = None
    elif isinstance(raw, (list, tuple)):
        candidates = list(raw)
    if not candidates:
        logger.warning(
            "allocation_policy.parse_json_list: unparseable value %r; "
            "using fallback %s", raw, tuple(fallback),
        )
        return tuple(fallback)
    cleaned = tuple(str(item).strip() for item in candidates if str(item).strip())
    return cleaned or tuple(fallback)


# ── Swing-runner attribution ─────────────────────────────────────────────────

def _order_field(order: Any, name: str) -> Any:
    """Read a field from an order object (attr) or mapping (item)."""
    if isinstance(order, Mapping):
        return order.get(name)
    return getattr(order, name, None)


def is_swing_labeled_order(order: Any, prefixes: Sequence[str]) -> bool:
    """True if the order's client_order_id starts with a swing prefix."""
    coid = _order_field(order, "client_order_id") or ""
    return isinstance(coid, str) and coid.startswith(tuple(prefixes))


def swing_symbols_from_orders(
    orders: Iterable[Any], prefixes: Sequence[str]
) -> set:
    """Symbols of orders labeled with a swing-runner prefix.

    Covers entries AND their resting exits (stops carry the same
    prefix plus a suffix, e.g. sl_short-AMD-s-20261102,
    rsi2-AMD-20261103-1-stop-r2).
    """
    symbols: set = set()
    for order in orders or []:
        try:
            if is_swing_labeled_order(order, prefixes):
                sym = _order_field(order, "symbol")
                if sym:
                    symbols.add(str(sym).upper())
        except Exception as e:  # never raise into a trading loop
            logger.debug("swing_symbols_from_orders: skipping malformed order: %s", e)
    return symbols


def swing_symbols_from_runner_db(db_path: str) -> set:
    """Symbols with status='open' in a runner SQLite positions table.

    Read-only URI open (mode=ro): the runner's DB is foreign state and
    must never be written or locked by this bot. Any failure returns an
    empty set with a warning — attribution then falls back to order
    labels only (sell-side safety is unaffected: ownership is proven by
    the main bot's OWN ledger, not by runner DBs).
    """
    symbols: set = set()
    if not db_path:
        return symbols
    try:
        uri = "file:{}?mode=ro".format(str(db_path).replace("?", "%3f").replace("#", "%23"))
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            rows = conn.execute(
                "SELECT symbol FROM positions WHERE UPPER(status) = 'OPEN'"
            ).fetchall()
            symbols = {str(r[0]).upper() for r in rows if r[0]}
        finally:
            conn.close()
    except Exception as e:
        logger.warning(
            "allocation_policy.swing_symbols_from_runner_db: cannot read %r (%s); "
            "falling back to order-label attribution only", db_path, e,
        )
        return set()
    return symbols


# ── Own-position attribution (main bot's trades ledger) ──────────────────────

def own_net_qty_from_ledger_rows(rows: Iterable[Mapping]) -> Dict[str, float]:
    """Net confirmed qty per symbol from this bot's OWN trades ledger.

    - Only status == 'FILLED' rows with a filled_qty count (EXEC-004:
      SUBMITTED / REJECTED / CANCELED rows prove nothing).
    - SELL subtracts; BUY adds. Sleeve tags are ignored — ownership is
      a per-symbol question for the aggregate broker position (the
      broker reports ONE position per symbol across sleeves).
    - Symbols whose net resolves to ~0 are excluded (fully exited).
    """
    net: Dict[str, float] = {}
    for row in rows or []:
        try:
            if (row or {}).get("status") != _FILLED_STATUS:
                continue
            qty = row.get("filled_qty")
            if qty is None:
                continue
            symbol = row.get("symbol")
            if not symbol:
                continue
            side = str(row.get("side") or "").upper()
            signed = float(qty) * (-1.0 if side == "SELL" else 1.0)
            net[str(symbol).upper()] = net.get(str(symbol).upper(), 0.0) + signed
        except Exception as e:  # fail-closed per-row skip
            logger.debug("own_net_qty_from_ledger_rows: malformed row skipped: %s", e)
    return {s: q for s, q in net.items() if abs(q) > 1e-9}


# ── Classification of broker positions ───────────────────────────────────────

#: Classification verdicts for one broker position.
OWN = "own"                    # ledger-proven main-bot position (manageable)
SWING = "swing"                # attributed to an external swing runner (untouchable)
UNATTRIBUTED = "unattributed"  # not runner-labeled, not ledger-proven (untouchable,
                               # but counted toward the long-term budget)


def classify_positions(
    positions: Iterable[Any],
    own_net: Mapping[str, float],
    swing_symbols: set,
) -> Dict[str, Any]:
    """Classify broker positions and aggregate market values.

    Returns {
      'by_symbol': {SYM: {'classification': ..., 'qty': float, 'market_value': float}},
      'own_mv', 'swing_mv', 'unattributed_mv',
      'long_term_book_mv'  # own + unattributed (budget-relevant),
    }
    """
    by_symbol: Dict[str, Dict[str, Any]] = {}
    own_mv = swing_mv = unattr_mv = 0.0
    for position in positions or []:
        try:
            symbol = str(_order_field(position, "symbol") or "").upper()
            if not symbol:
                continue
            qty = abs(float(_order_field(position, "qty") or 0.0))
            mv = abs(float(_order_field(position, "market_value") or 0.0))
            if symbol in swing_symbols:
                classification, swing_mv = SWING, swing_mv + mv
            elif symbol in own_net and own_net[symbol] > 0:
                classification, own_mv = OWN, own_mv + mv
            else:
                classification, unattr_mv = UNATTRIBUTED, unattr_mv + mv
            by_symbol[symbol] = {
                "classification": classification,
                "qty": qty,
                "market_value": mv,
            }
        except Exception as e:  # fail-closed per-position skip
            logger.debug("classify_positions: malformed position skipped: %s", e)
    return {
        "by_symbol": by_symbol,
        "own_mv": own_mv,
        "swing_mv": swing_mv,
        "unattributed_mv": unattr_mv,
        "long_term_book_mv": own_mv + unattr_mv,
    }


# ── Budget math (pure) ───────────────────────────────────────────────────────

def long_term_budget(equity: float, budget_pct: float) -> float:
    """Main bot's long-term budget in dollars (= budget_pct% of equity)."""
    return max(0.0, float(equity)) * (max(0.0, float(budget_pct)) / 100.0)


def cash_floor_amount(equity: float, floor_pct: float) -> float:
    """Minimum cash that must remain after any main-bot BUY."""
    return max(0.0, float(equity)) * (max(0.0, float(floor_pct)) / 100.0)


def budget_room(equity: float, budget_pct: float, long_term_book_mv: float) -> float:
    """Dollars the main bot may still deploy into its long-term book."""
    return max(0.0, long_term_budget(equity, budget_pct) - max(0.0, float(long_term_book_mv)))


def max_buy_cost(
    cash: float, equity: float, floor_pct: float, room: float
) -> float:
    """Max dollars a single new BUY may cost.

    min(long-term budget headroom, cash minus the cash floor). Both
    constraints are hard; the tighter one wins. Never negative.
    """
    floor = cash_floor_amount(equity, floor_pct)
    return max(0.0, min(float(room), float(cash) - floor))


def cap_quantity_for_cost(quantity: int, price: float, allowed_cost: float) -> int:
    """Largest qty <= quantity whose total cost fits allowed_cost."""
    if price <= 0 or quantity <= 0 or allowed_cost <= 0:
        return 0
    return int(max(0, min(int(quantity), int(allowed_cost / float(price)))))


def buy_respects_cash_floor(
    cash: float, cost: float, equity: float, floor_pct: float, eps: float = 1e-9
) -> bool:
    """True if cash - cost still satisfies the cash floor (exact fit allowed)."""
    return (float(cash) - float(cost)) >= cash_floor_amount(equity, floor_pct) - eps
