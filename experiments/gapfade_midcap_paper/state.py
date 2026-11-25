"""GAPFADE-MIDCAP-PAPER — small local state store (JSON).

Default file: /root/trading-data/gapfade-midcap-paper/state.json
(outside the repo and outside /root/.openclaw); override with
GAPFADE_MIDCAP_STATE_DIR or --state-dir. NEVER the live trading_bot.db.

Tracks open positions, closed trades, shadow trades, per-day entry counts
and the idempotency keys already submitted, so reruns never double-enter.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_STATE_DIR = Path("/root/trading-data/gapfade-midcap-paper")
STATE_DIR_ENV = "GAPFADE_MIDCAP_STATE_DIR"
STATE_FILENAME = "state.json"
SCHEMA_VERSION = 1


def resolve_state_dir(state_dir: str | Path | None = None) -> Path:
    if state_dir is not None:
        return Path(state_dir)
    env = os.environ.get(STATE_DIR_ENV)
    return Path(env) if env else DEFAULT_STATE_DIR


def _empty() -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "positions": {},      # symbol -> position dict
        "closed": [],         # list of closed-trade dicts
        "shadows": [],        # list of shadow-trade dicts
        "entry_counts": {},   # ISO date -> int (real entries that day)
        "order_ids": [],      # client_order_ids already generated/submitted
        "intents": [],        # every intended entry/exit + fill (audit log)
        "passes": {},         # ISO date -> {'morning': ts, 'after_close': ts}
        "early_stops": [],    # trials that tripped the paper-trial early stop
    }


class StateStore:
    """Load/save a small JSON state file; atomic replace on save."""

    def __init__(self, state_dir: str | Path | None = None,
                 filename: str = STATE_FILENAME):
        self.dir = resolve_state_dir(state_dir)
        self.path = self.dir / filename
        self.data = _empty()

    # --- persistence ---
    def load(self) -> dict:
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            data = _empty()
            data.update(raw or {})
            self.data = data
        else:
            self.data = _empty()
        return self.data

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    # --- positions ---
    def positions(self) -> dict:
        return self.data["positions"]

    def is_held(self, symbol: str) -> bool:
        return symbol.upper() in self.data["positions"]

    def open_position(self, position: dict) -> None:
        self.data["positions"][position["symbol"].upper()] = position

    def close_position(self, symbol: str, closed: dict | None = None) -> dict | None:
        pos = self.data["positions"].pop(symbol.upper(), None)
        if pos is not None and closed:
            self.data["closed"].append(closed)
        return pos

    # --- counts / keys ---
    def entries_on(self, day: str) -> int:
        return int(self.data["entry_counts"].get(str(day), 0))

    def record_entry(self, day: str, n: int = 1) -> None:
        k = str(day)
        self.data["entry_counts"][k] = self.entries_on(k) + int(n)

    def has_order_id(self, coid: str) -> bool:
        return coid in self.data["order_ids"]

    def record_order_id(self, coid: str) -> None:
        if coid not in self.data["order_ids"]:
            self.data["order_ids"].append(coid)

    # --- intended orders / fills ---
    def add_intent(self, intent: dict) -> None:
        """Append one intended entry/exit record (part-2 audit log)."""
        self.data["intents"].append(intent)

    def intents(self) -> list[dict]:
        return self.data["intents"]

    def intent(self, client_order_id: str) -> dict | None:
        for rec in self.data["intents"]:
            if rec.get("client_order_id") == client_order_id:
                return rec
        return None

    def record_pass(self, day: str, pass_name: str, ts: str) -> None:
        self.data["passes"].setdefault(str(day), {})[pass_name] = ts

    def pass_done(self, day: str, pass_name: str) -> bool:
        return pass_name in (self.data["passes"].get(str(day)) or {})

    # --- shadows ---
    def add_shadow(self, shadow: dict) -> None:
        self.data["shadows"].append(shadow)

    def shadows(self) -> list[dict]:
        return self.data["shadows"]

    def shadow_for(self, real_client_order_id: str) -> list[dict]:
        return [s for s in self.data["shadows"]
                if s.get("real_client_order_id") == real_client_order_id]

    # --- early stop (paper-trial flag only) ---
    def add_early_stop(self, record: dict) -> None:
        """Append one early-stop flag record (never an order or a process)."""
        self.data["early_stops"].append(record)

    def early_stops(self) -> list[dict]:
        return self.data["early_stops"]

    def chosen_symbols(self) -> set[str]:
        """Symbols already used by a real entry or a shadow on any day."""
        out = {s.upper() for s in self.data["positions"]}
        out |= {str(c.get("symbol", "")).upper() for c in self.data["closed"]}
        out |= {str(s.get("symbol", "")).upper() for s in self.data["shadows"]}
        out.discard("")
        return out
