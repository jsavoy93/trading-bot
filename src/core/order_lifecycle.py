"""EXEC-003 SHOULD-FIX #1 — Order lifecycle canonicalization and reconciliation.

Pure functions only. No I/O, no broker calls, no DB access. Everything
here is deterministic and unit-testable in isolation.

Canonical vocabulary (persisted to ``trades.status`` / ``order_events``):

    SUBMITTED          broker accepted the order; fill not confirmed
                       (set by EXEC-003.1 at submit_order success)
    ACCEPTED           broker acknowledged / pending further state
    PARTIALLY_FILLED   broker reports a partial fill (0 < filled < qty)
    FILLED             broker CONFIRMED the order filled
    CANCELED           broker canceled the order (possibly after partial)
    REJECTED           broker rejected the order
    EXPIRED            order expired unfilled (where supported)
    UNRESOLVED         polling could not obtain a broker answer
                       (never fabricated; missing confirmation stays
                       explicitly unresolved)

Hard invariants (mirrors the EXEC-003.1 SUBMITTED != FILLED contract):

- ``FILLED`` is only ever derived from a broker-reported fill event
  (raw status ``filled`` or filled_qty >= ordered qty). It is NEVER
  inferred from submit_order success.
- State transitions never regress out of a terminal state.
- ``filled_qty`` is monotonic non-decreasing (cumulative per broker
  semantics); ``avg_fill_price`` only moves when ``filled_qty``
  advances (prevents double-counting repeated polls of the same
  partial fill).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Canonical tokens
# ---------------------------------------------------------------------------

CANONICAL_SUBMITTED = "SUBMITTED"
CANONICAL_ACCEPTED = "ACCEPTED"
CANONICAL_PARTIALLY_FILLED = "PARTIALLY_FILLED"
CANONICAL_FILLED = "FILLED"
CANONICAL_CANCELED = "CANCELED"
CANONICAL_REJECTED = "REJECTED"
CANONICAL_EXPIRED = "EXPIRED"
CANONICAL_UNRESOLVED = "UNRESOLVED"

CANONICAL_STATUSES = frozenset(
    {
        CANONICAL_SUBMITTED,
        CANONICAL_ACCEPTED,
        CANONICAL_PARTIALLY_FILLED,
        CANONICAL_FILLED,
        CANONICAL_CANCELED,
        CANONICAL_REJECTED,
        CANONICAL_EXPIRED,
        CANONICAL_UNRESOLVED,
    }
)

#: Statuses that end an order's lifecycle. No transition may leave a
#: terminal state (except a CANCELED/EXPIRED order that later reports a
#: fill — brokers can race cancel vs fill; we treat a confirmed fill on a
#: CANCELED row as a legitimate terminal upgrade because the broker is
#: authoritative, but we never downgrade FILLED).
_TERMINAL = frozenset(
    {CANONICAL_FILLED, CANONICAL_CANCELED, CANONICAL_REJECTED, CANONICAL_EXPIRED}
)

#: Monotonic progression rank used to reject stale / out-of-order broker
#: responses. Higher rank wins; terminal states have the highest ranks.
#:
# UNRESOLVED is deliberately NOT in the progression — it is a polling
# outcome, not an order state, and never overwrites a real state.
_STATUS_RANK = {
    CANONICAL_SUBMITTED: 10,
    CANONICAL_ACCEPTED: 15,
    CANONICAL_PARTIALLY_FILLED: 20,
    CANONICAL_EXPIRED: 80,
    CANONICAL_CANCELED: 90,
    CANONICAL_REJECTED: 91,
    CANONICAL_FILLED: 100,
}

# ---------------------------------------------------------------------------
# Alpaca raw-status → canonical mapping
# ---------------------------------------------------------------------------

_ALPACA_STATUS_MAP = {
    "new": CANONICAL_SUBMITTED,
    "accepted": CANONICAL_ACCEPTED,
    "pending_new": CANONICAL_ACCEPTED,
    "accepted_for_bidding": CANONICAL_ACCEPTED,
    "pending_replace": CANONICAL_ACCEPTED,
    "replaced": CANONICAL_ACCEPTED,
    "pending_cancel": CANONICAL_ACCEPTED,
    "calculated": CANONICAL_ACCEPTED,
    "done_for_day": CANONICAL_ACCEPTED,
    "partially_filled": CANONICAL_PARTIALLY_FILLED,
    "filled": CANONICAL_FILLED,
    "canceled": CANONICAL_CANCELED,
    "cancelled": CANONICAL_CANCELED,  # defensive alias
    "expired": CANONICAL_EXPIRED,
    "rejected": CANONICAL_REJECTED,
    "stopped": CANONICAL_CANCELED,
    "suspended": CANONICAL_ACCEPTED,
    "halted": CANONICAL_ACCEPTED,
    "pending_review": CANONICAL_ACCEPTED,
    "pending_liquidation": CANONICAL_ACCEPTED,
    "liquidated": CANONICAL_FILLED,
    "error": CANONICAL_REJECTED,
    "calculation_failed": CANONICAL_REJECTED,
}


def canonicalize_raw_status(raw_status: Optional[str]) -> str:
    """Map a broker raw status string to the canonical vocabulary.

    Unknown / missing raw statuses map to ``UNRESOLVED`` — we never
    guess a fill or a rejection from an unrecognized token.
    """
    if not raw_status or not isinstance(raw_status, str):
        return CANONICAL_UNRESOLVED
    return _ALPACA_STATUS_MAP.get(raw_status.strip().lower(), CANONICAL_UNRESOLVED)


# ---------------------------------------------------------------------------
# Broker order event extraction
# ---------------------------------------------------------------------------


def extract_broker_event(order: Any) -> Optional[Dict[str, Any]]:
    """Extract a normalized event dict from a broker order object.

    Accepts either an attribute-style object (Alpaca ``Order`` /
    ``SimpleNamespace``) or a mapping. Returns ``None`` when the input
    carries no usable information at all (caller keeps the row
    explicitly unresolved rather than fabricating state).

    Returned keys (always present when a dict is returned):
        raw_status, canonical_status, filled_qty, avg_fill_price,
        order_id, updated_at
    """
    if order is None:
        return None

    def _get(name: str, default: Any = None) -> Any:
        if isinstance(order, dict):
            return order.get(name, default)
        return getattr(order, name, default)

    raw_status = _get("status")
    filled_qty = _get("filled_qty")
    avg_fill_price = _get("filled_avg_price")
    if avg_fill_price is None:
        avg_fill_price = _get("avg_fill_price")
    order_id = _get("id")
    updated_at = _get("updated_at") or _get("timestamp")

    has_any = any(
        v is not None for v in (raw_status, filled_qty, avg_fill_price, order_id)
    )
    if not has_any:
        return None

    return {
        "raw_status": raw_status if isinstance(raw_status, str) else None,
        "canonical_status": canonicalize_raw_status(raw_status),
        "filled_qty": _coerce_qty(filled_qty),
        "avg_fill_price": _coerce_price(avg_fill_price),
        "order_id": str(order_id) if order_id is not None else None,
        "updated_at": str(updated_at) if updated_at is not None else None,
    }


def _coerce_qty(value: Any) -> Optional[float]:
    """Coerce a broker quantity to float; None/invalid → None."""
    if value is None:
        return None
    try:
        q = float(value)
    except (TypeError, ValueError):
        return None
    return q if q >= 0 else None


def _coerce_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    return p if p > 0 else None


# ---------------------------------------------------------------------------
# Reconciliation (the heart of SHOULD-FIX #1)
# ---------------------------------------------------------------------------


def reconcile(
    row: Dict[str, Any],
    broker_event: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute the next persisted state for a ``trades`` row.

    Parameters
    ----------
    row:
        Current persisted state. Recognized keys: ``status``,
        ``qty`` (ordered quantity), ``filled_qty``, ``avg_fill_price``.
    broker_event:
        Output of :func:`extract_broker_event`, or ``None`` when the
        broker could not be reached / returned nothing usable.

    Returns
    -------
    dict with keys:
        changed (bool)          — does anything need persisting?
        status (str)            — canonical status for the row
        filled_qty (float|None) — cumulative filled quantity
        avg_fill_price          — average fill price (moves only with
                                  a filled_qty advance)
        terminal (bool)         — row has reached a terminal state
        unresolved (bool)       — this poll produced no broker answer
        note (str)              — short human-readable reason

    This function NEVER mutates its inputs and performs no I/O.
    """
    current_status = row.get("status") or CANONICAL_SUBMITTED
    if current_status not in CANONICAL_STATUSES:
        current_status = CANONICAL_UNRESOLVED
    ordered_qty = _coerce_qty(row.get("qty"))
    current_filled = _coerce_qty(row.get("filled_qty")) or 0.0
    current_price = _coerce_price(row.get("avg_fill_price"))

    if broker_event is None:
        return {
            "changed": False,
            "status": current_status,
            "filled_qty": current_filled or None,
            "avg_fill_price": current_price,
            "terminal": current_status in _TERMINAL,
            "unresolved": True,
            "note": "No usable broker response; left explicitly unresolved.",
        }

    ev_status = broker_event["canonical_status"]
    ev_filled = broker_event["filled_qty"] or 0.0
    ev_price = broker_event["avg_fill_price"]

    # --- UNRESOLVED events never overwrite a real state -------------------
    if ev_status == CANONICAL_UNRESOLVED and ev_filled <= 0:
        return {
            "changed": False,
            "status": current_status,
            "filled_qty": current_filled or None,
            "avg_fill_price": current_price,
            "terminal": current_status in _TERMINAL,
            "unresolved": True,
            "note": "Broker status unrecognized; not fabricated.",
        }

    # --- Quantity reconciliation (monotonic, no double counting) ---------
    new_filled = max(current_filled, ev_filled)
    if ordered_qty is not None and ordered_qty > 0:
        new_filled = min(new_filled, ordered_qty)
    filled_advanced = new_filled > current_filled

    # A broker-reported cumulative fill implies at least partial fill even
    # if the raw status token was unrecognized.
    effective_status = ev_status
    if ev_status == CANONICAL_UNRESOLVED and new_filled > 0:
        effective_status = CANONICAL_PARTIALLY_FILLED
    if (
        effective_status == CANONICAL_PARTIALLY_FILLED
        and ordered_qty is not None
        and ordered_qty > 0
        and new_filled >= ordered_qty
    ):
        effective_status = CANONICAL_FILLED
    if effective_status == CANONICAL_FILLED and new_filled <= 0:
        # FILLED token without any quantity is not trusted as a confirmed
        # fill quantity-wise; treat as ACCEPTED pending quantity evidence.
        effective_status = CANONICAL_ACCEPTED

    # --- Stale / out-of-order protection ---------------------------------
    if current_status in _TERMINAL and current_status != CANONICAL_FILLED:
        # Terminal non-filled states may only be upgraded by a confirmed
        # fill (broker raced cancel vs fill). Anything else is stale.
        if effective_status != CANONICAL_FILLED:
            return {
                "changed": False,
                "status": current_status,
                "filled_qty": current_filled or None,
                "avg_fill_price": current_price,
                "terminal": True,
                "unresolved": False,
                "note": f"Stale event ({ev_status}) on terminal row "
                f"({current_status}); ignored.",
            }
    if current_status == CANONICAL_FILLED:
        # Already confirmed. Later events cannot downgrade or re-count.
        return {
            "changed": filled_advanced,
            "status": CANONICAL_FILLED,
            "filled_qty": new_filled or None,
            "avg_fill_price": ev_price if filled_advanced else current_price,
            "terminal": True,
            "unresolved": False,
            "note": "Row already FILLED; only cumulative qty/price may advance.",
        }
    if _STATUS_RANK.get(effective_status, 0) < _STATUS_RANK.get(current_status, 0) and not filled_advanced:
        return {
            "changed": False,
            "status": current_status,
            "filled_qty": current_filled or None,
            "avg_fill_price": current_price,
            "terminal": current_status in _TERMINAL,
            "unresolved": False,
            "note": f"Out-of-order event ({ev_status}) after {current_status}; ignored.",
        }

    # --- Compose the update ------------------------------------------------
    new_price = current_price
    if filled_advanced and ev_price is not None:
        new_price = ev_price

    changed = (
        effective_status != current_status
        or filled_advanced
        or (new_price != current_price)
    )
    return {
        "changed": changed,
        "status": effective_status,
        "filled_qty": new_filled or None,
        "avg_fill_price": new_price,
        "terminal": effective_status in _TERMINAL,
        "unresolved": False,
        "note": f"{current_status} → {effective_status}"
        + (f", filled_qty={new_filled}" if filled_advanced else ""),
    }
