"""SLEEVE-P1 — derived per-sleeve position bookkeeping.

Two-sleeve portfolio design (§6.3): the broker reports ONE aggregate
position per symbol because both sleeves share a single paper account.
This module maintains a DERIVED, in-memory per-sleeve position map
(``symbol -> per-sleeve qty/entry``) built from ledger fills so that
sleeve-level intent questions ("how much AAPL does Sleeve S hold?")
can be answered without touching the broker.

Contract (must NOT be weakened):

- **DERIVED ONLY, NOT AUTHORITATIVE.** The broker remains authoritative
  for real quantities. Nothing in this module may be used to alter,
  gate, size, or exit a trading decision. It is a bookkeeping /
  observability projection of the ledger.
- **Fail-closed on read errors.** If the ledger cannot be read, the
  builder logs a warning and returns an empty book (never raises into
  a caller loop, never fabricates quantities). An empty/missing map
  must never be interpreted as "no position exists".
- **Only confirmed fills count.** Rows are consumed only when
  ``status == 'FILLED'`` and a confirmed ``filled_qty`` is present
  (EXEC-004 caveat: backfilled rows keep pnl NULL and never count).
  SUBMITTED / REJECTED / CANCELED / UNKNOWN rows are ignored.
- **Only tagged rows count.** Rows with ``sleeve`` NULL (legacy
  pre-sleeve rows) or invalid are skipped — never guessed (D7).
"""

import logging
from typing import Dict, List, Optional

#: The only sleeve tags the ledger validation accepts.
VALID_SLEEVES = ("L", "S")

#: Lifecycle status that counts as a confirmed fill for the map.
FILLED_STATUS = "FILLED"


class SleevePositionBook:
    """In-memory map of (symbol, sleeve) -> {qty, avg_entry, fills}.

    Maintained from ledger fill rows. Purely derived bookkeeping —
    see the module docstring for the broker-authoritative contract.
    """

    def __init__(self) -> None:
        # (symbol, sleeve) -> {"qty": float, "avg_entry": float, "fills": int}
        self._positions: Dict[tuple, Dict] = {}

    # ------------------------------------------------------------------
    # Mutation (from ledger fills only)
    # ------------------------------------------------------------------

    def apply_fill(
        self,
        symbol: str,
        sleeve: str,
        qty: float,
        price: Optional[float],
    ) -> None:
        """Apply one confirmed ledger fill to the map.

        ``qty`` is the signed broker-confirmed quantity (BUY positive,
        SELL negative). ``price`` is the broker-reported average fill
        price when known (``avg_fill_price``); None keeps the previous
        average. Invalid sleeve values are refused (fail-closed:
        skipped with a warning, never guessed).
        """
        if sleeve not in VALID_SLEEVES:
            logging.warning(
                "SleevePositionBook.apply_fill refusing invalid sleeve "
                "%r for %s (allowed: %s); fill skipped.",
                sleeve,
                symbol,
                VALID_SLEEVES,
            )
            return
        if qty is None:
            return
        key = (symbol, sleeve)
        pos = self._positions.get(
            key, {"qty": 0.0, "avg_entry": None, "fills": 0}
        )
        new_qty = pos["qty"] + float(qty)
        if price is not None:
            # Weighted average entry over BUY fills; on a full exit the
            # average resets (a later re-entry re-establishes it).
            if qty > 0 and new_qty > 0 and pos["avg_entry"] is not None:
                old_weight = max(pos["qty"], 0.0)
                pos["avg_entry"] = (
                    (pos["avg_entry"] * old_weight + float(price) * float(qty))
                    / (old_weight + float(qty))
                )
            elif qty > 0:
                pos["avg_entry"] = float(price)
        if new_qty == 0:
            pos["avg_entry"] = None
        pos["qty"] = new_qty
        pos["fills"] += 1
        self._positions[key] = pos

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, symbol: str, sleeve: str) -> Optional[Dict]:
        """Return the per-sleeve position dict for a symbol, or None.

        The returned dict (a copy) has keys ``qty``, ``avg_entry`` and
        ``fills``. ``None`` means no confirmed tagged fill has been
        observed for (symbol, sleeve) — it does NOT mean the broker has
        no position (the broker is authoritative).
        """
        pos = self._positions.get((symbol, sleeve))
        return dict(pos) if pos else None

    def symbols(self) -> List[str]:
        """Distinct symbols that have at least one tagged fill."""
        return sorted({symbol for (symbol, _sleeve) in self._positions})

    def as_dict(self) -> Dict[str, Dict]:
        """Serializable snapshot: symbol -> sleeve -> position dict."""
        out: Dict[str, Dict] = {}
        for (symbol, sleeve), pos in self._positions.items():
            out.setdefault(symbol, {})[sleeve] = dict(pos)
        return out

    def __len__(self) -> int:
        return len(self._positions)


def book_from_ledger_rows(rows: List[Dict]) -> SleevePositionBook:
    """Build a book from trades-table row dicts (e.g. get_all_trades).

    Applies only confirmed tagged fills (see module contract). Never
    raises; malformed rows are skipped with a debug log.
    """
    book = SleevePositionBook()
    for row in rows or []:
        try:
            if row.get("status") != FILLED_STATUS:
                continue
            sleeve = row.get("sleeve")
            if sleeve not in VALID_SLEEVES:
                # Legacy NULL-sleeve rows and anything malformed are
                # skipped — never guessed (D7 default).
                continue
            qty = row.get("filled_qty")
            if qty is None:
                continue
            side = (row.get("side") or "").upper()
            signed_qty = float(qty) * (1.0 if side != "SELL" else -1.0)
            book.apply_fill(
                symbol=row.get("symbol"),
                sleeve=sleeve,
                qty=signed_qty,
                price=row.get("avg_fill_price"),
            )
        except Exception as e:  # fail-closed per-row skip
            logging.debug(
                "SleevePositionBook: skipping malformed ledger row: %s", e
            )
    return book


def book_from_db(db) -> SleevePositionBook:
    """Build a book from a SQLiteDB-like object.

    Fail-closed: any read error logs a warning and returns an EMPTY
    book (never raises into the caller loop; an empty book carries no
    position information and must not be read as 'no positions exist').
    """
    try:
        rows = db.get_all_trades(limit=10_000_000)
        return book_from_ledger_rows(rows)
    except Exception as e:
        logging.warning(
            "SleevePositionBook: ledger read failed (%s); returning "
            "empty derived book. Broker remains authoritative; no "
            "trading decision may consume this map.",
            e,
        )
        return SleevePositionBook()
