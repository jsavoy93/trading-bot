"""REBAL-40-50-10 — main-bot long-term allocation policy.

Approved split (Josh, 2026-11-25; supersedes the 2026-11-09 60/30/10 split):
40% of account equity is the main SmartBot's long-term budget, 50% belongs
to the external short-term swing runners (C4 at /root/strat-swing, RSI(2)
at /root/deploy/rsi2-swing, and the GAPFADE midcap paper trial at
/root/strat-gapfade; all share the SAME Alpaca paper account), and at least
10% of equity must stay in cash.

Contracts (must NOT be weakened):

- **Pure math, no broker calls.** Every budget/cash function here is
  side-effect free and unit-testable. Broker reads happen in
  SmartTradingBot wrappers, never here.
- **Foreign positions are identified by evidence, never guessed.**
  Evidence sources: (1) broker order labels — any order whose
  client_order_id starts with a configured swing prefix
  (default "sl_short-" for C4, "rsi2-" for RSI(2), "gf_mid-" for the
  GAPFADE midcap paper runner); (2) optional runner
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
import math
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Fallback label prefixes if the configured value cannot be parsed.
DEFAULT_SWING_ORDER_LABEL_PREFIXES: Tuple[str, ...] = (
    "sl_short-",  # C4 runner (/root/strat-swing)
    "rsi2-",      # RSI(2) runner (/root/deploy/rsi2-swing)
    "gf_mid-",    # GAPFADE midcap paper runner (/root/strat-gapfade)
)

#: Prefixes that were once shipped as the default and are now superseded by
#: DEFAULT_SWING_ORDER_LABEL_PREFIXES. A persisted setting that exactly equals
#: the legacy JSON list is treated as "never customized": it is widened to the
#: current defaults so existing deployments pick up newly added runners
#: (GAPFADE midcap gf_mid-). Any other value — including malformed JSON — is
#: left untouched; malformed values still fail closed via parse_json_list.
LEGACY_SWING_ORDER_LABEL_PREFIXES: Tuple[Tuple[str, ...], ...] = (
    ("sl_short-", "rsi2-"),
)

#: REBAL-40-50-10 (Josh, 2026-11-25): shipped default of the main bot's
#: long-term allocation budget before the 60/30/10 -> 40/50/10 rebalance.
#: A persisted value that EXACTLY equals this "never customized" value is a
#: one-time candidate for the normalization below; anything else is respected.
LEGACY_LONG_TERM_BUDGET_PCT: float = 60.0
LONG_TERM_BUDGET_PCT_KEY: str = "long_term_budget_pct"

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


def normalize_swing_order_label_prefixes(
    raw: Any, defaults: Sequence[str] = DEFAULT_SWING_ORDER_LABEL_PREFIXES
) -> Tuple[str, ...]:
    """Effective swing label prefixes from a possibly-persisted setting.

    Parses ``raw`` fail-closed (same rules as ``parse_json_list``) and then
    upgrades a value that exactly matches a superseded shipped default to the
    current ``defaults``. This keeps existing deployments on the authoritative
    runner list when a new runner prefix is added, while an explicitly
    customized list is never widened.
    """
    prefixes = parse_json_list(raw, defaults)
    legacy_sets = {tuple(item) for item in LEGACY_SWING_ORDER_LABEL_PREFIXES}
    if prefixes in legacy_sets and tuple(prefixes) != tuple(defaults):
        logger.info(
            "allocation_policy: upgrading legacy swing label prefixes %s -> %s",
            prefixes, tuple(defaults),
        )
        return tuple(defaults)
    return prefixes


# ── Long-term budget migration (REBAL-40-50-10) ─────────────────────────────

def is_legacy_long_term_budget_pct(raw: Any) -> bool:
    """True only for a persisted value that EXACTLY equals the old default.

    ``60`` and ``"60.0"`` are the legacy shipped default (never customized).
    Custom values (e.g. 50.0), out-of-range values and malformed values are
    NOT legacy: they are left exactly as they are (fail closed — the caller
    keeps whatever the normal settings load produced).
    """
    if raw is None or isinstance(raw, bool):
        return False
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return False
    return value == float(LEGACY_LONG_TERM_BUDGET_PCT)


def normalize_long_term_budget_pct_rows(
    rows: Iterable[Any], new_default: Any
) -> Tuple[List[Any], Optional[float]]:
    """Pure decision helper: which settings rows to rewrite (no side effects).

    ``rows`` are ``(key, value)`` or ``(key, value, ...)`` sequences from the
    settings table limited to ``LONG_TERM_BUDGET_PCT_KEY``. Returns
    ``(rows_to_rewrite, first_legacy_value)``; only rows whose value is the
    legacy default are returned, so a second run over already-normalized
    rows rewrites nothing (idempotent).
    """
    to_rewrite: List[Any] = []
    first: Optional[float] = None
    for row in rows:
        if not row or str(row[0]) != LONG_TERM_BUDGET_PCT_KEY:
            continue
        value = row[1] if len(row) > 1 else None
        if is_legacy_long_term_budget_pct(value):
            to_rewrite.append(row)
            if first is None:
                first = float(value)
    return to_rewrite, first


def normalize_persisted_long_term_budget_pct(
    settings_service_module: Any,
    new_default: Any,
    db_path: Any = None,
) -> bool:
    """One-time, explicit, idempotent rewrite of the legacy 60.0 -> ``new_default``.

    Runs ONLY when the persisted value exactly equals the legacy shipped
    default (:data:`LEGACY_LONG_TERM_BUDGET_PCT`). The rewrite goes through
    the app's normal runtime DB-write path (``settings_service.save_typed``),
    so the new value is schema-validated and serialized like any other
    setting write. Custom values and malformed values are never touched.

    Returns True when a row was rewritten this call, False when there was
    nothing to do (no row, custom value, or a second run — no-op). Never
    raises: any DB/validation error logs a warning and leaves the DB alone.
    """
    try:
        conn = settings_service_module._get_conn()
        try:
            rows = conn.execute(
                "SELECT key, value FROM settings WHERE key = ?",
                (LONG_TERM_BUDGET_PCT_KEY,),
            ).fetchall()
        finally:
            conn.close()
        to_rewrite, legacy_value = normalize_long_term_budget_pct_rows(
            [tuple(row) for row in rows], new_default
        )
        if not to_rewrite:
            return False
        logger.info(
            "allocation_policy: REBAL-40-50-10 normalizing persisted %s "
            "from legacy default %s to %s",
            LONG_TERM_BUDGET_PCT_KEY, legacy_value, new_default,
        )
        settings_service_module.save_typed(LONG_TERM_BUDGET_PCT_KEY, new_default)
        return True
    except Exception as exc:  # fail closed: never break a trading loop
        logger.warning(
            "allocation_policy: %s normalization skipped (%s)",
            LONG_TERM_BUDGET_PCT_KEY, exc.__class__.__name__,
        )
        return False


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
    rsi2-AMD-20261103-1-stop-r2, gf_mid-AAA-sell-20261103-1).
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


# ── REBAL-40-50-10 per-runner sleeve caps ───────────────────────────────────
# The 50% short-term sleeve is split C4 10% / RSI(2) 10% / GAPFADE 30%. Each
# runner gets its OWN hard total-exposure cap and a fail-closed check. The
# main bot itself never sizes these orders; the constants live here only so
# the numbers are declared once in the same module as the 40/50/10 split.
SWING_SLEEVE_TOTAL_PCT: float = 0.50
C4_SLEEVE_PCT: float = 0.10          # 3 positions, <= 10% in total
RSI2_SLEEVE_PCT: float = 0.10        # 4 positions, <= 10% in total
GAPFADE_TOTAL_CAP_PCT: float = 0.30  # 10 open x 3% per entry = 30%
CASH_FLOOR_PCT: float = 0.10
MAIN_BUDGET_PCT: float = 0.40


def swing_total_cap_exceeded(open_exposure: Any, equity: Any,
                             sleeve_pct: Any) -> bool:
    """FAIL-CLOSED check: True (=> refuse the entry) when `open_exposure`
    already meets or exceeds `sleeve_pct * equity`.

    Unknown/unusable inputs (None, non-numeric, negative equity, errors while
    converting) count as EXCEEDED — the caller must skip the entry rather
    than risk over-deploying. A non-negative exposure that is still below the
    cap returns False.
    """
    try:
        equity_f = float(equity)
        exposure_f = float(open_exposure)
        pct_f = float(sleeve_pct)
    except (TypeError, ValueError):
        return True
    if not all(math.isfinite(v) for v in (equity_f, exposure_f, pct_f)):
        return True             # NaN / inf exposure or equity => refuse
    if equity_f <= 0.0 or exposure_f < 0.0:
        return True
    if not (0.0 < pct_f <= 1.0):
        return True
    return exposure_f >= pct_f * equity_f


def swing_entry_budget(open_exposure: Any, equity: Any,
                       sleeve_pct: Any) -> float:
    """Dollar budget still available inside a runner's sleeve, or 0.0 when
    the hard cap is reached or exposure is unknown (fail closed)."""
    if swing_total_cap_exceeded(open_exposure, equity, sleeve_pct):
        return 0.0
    return float(equity) * float(sleeve_pct) - float(open_exposure)
