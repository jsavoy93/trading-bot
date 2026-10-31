"""Universe = UNION of:
  - C4 universe (STRAT-006R POPULAR_LIQUID_US, same file strat_023 uses), and
  - the main bot's watchlist — read read-only from the MAIN workspace
    analysis_status.json (the symbols the main bot currently tracks).
The main workspace is NEVER written by this engine.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import C4_UNIVERSE_FILE, MAIN_WATCHLIST_JSON


def c4_universe(path: str = C4_UNIVERSE_FILE) -> list[str]:
    import importlib.util
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"C4 universe file missing: {path}")
    spec = importlib.util.spec_from_file_location("strat006r_cand", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return sorted({s for s in mod.POPULAR_LIQUID_US if s})


def main_watchlist(path: str = MAIN_WATCHLIST_JSON) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict):
        return sorted({str(s).upper() for s in data.keys() if isinstance(s, str)})
    return []


def universe_symbols(c4_path: str = C4_UNIVERSE_FILE,
                     wl_path: str = MAIN_WATCHLIST_JSON) -> list[str]:
    return sorted(set(c4_universe(c4_path)) | set(main_watchlist(wl_path)))


def sector_of(symbol: str, sector_map: dict[str, str] | None = None) -> str:
    """Sector lookup for the sector cap. sector_map is a configurable
    symbol->sector dict (env/JSON-provided). Unknown -> 'UNKNOWN'."""
    return (sector_map or {}).get(symbol.upper(), "UNKNOWN")
