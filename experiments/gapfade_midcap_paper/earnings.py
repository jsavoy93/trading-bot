"""GAPFADE-MIDCAP-PAPER — earnings calendar (ONE documented source).

Source: Yahoo Finance's public earnings-calendar pages via `yfinance`
(`Ticker.calendar['Earnings Date']`, a list of upcoming/past dates Yahoo
publishes for the symbol). yfinance is already a repo dependency
(requirements.txt).

Refresh: the runner builds the calendar once per run for the whole S&P 400
universe, caches it in the state directory and re-fetches every
EARNINGS_REFRESH_HOURS hours (default 20, i.e. roughly once per trading
day). If the fetch fails for a symbol, that symbol has NO coverage and the
frozen rule SKIPS it (fail closed — never trade a name whose earnings date
is unknown). Failures are counted and reported; a whole-fetch outage
therefore produces zero entries, never unchecked ones.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from experiments.gapfade_midcap_paper import rules

CACHE_FILE = "earnings_calendar.json"
EARNINGS_REFRESH_HOURS = 20
YAHOO_BATCH_PAUSE_S = 0.25     # be polite to the vendor between symbols
YAHOO_FETCH_ATTEMPTS = 1       # a failed fetch = no coverage (fail closed)


def _yf_earnings_dates(symbol: str) -> list[str]:
    """Upcoming/past earnings dates for one symbol (ISO strings)."""
    import yfinance  # local import: unit tests never need the package
    ticker = yfinance.Ticker(symbol)
    cal = getattr(ticker, "calendar", None) or {}
    raw = cal.get("Earnings Date") if isinstance(cal, dict) else None
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    out = []
    for item in raw:
        try:
            out.append(item.date().isoformat() if hasattr(item, "date")
                       else str(item)[:10])
        except Exception:
            continue
    return sorted(set(out))


def load_calendar(cache_dir: str | Path, symbols: list[str],
                  refresh_hours: int = EARNINGS_REFRESH_HOURS,
                  now: datetime | None = None, fetch_one=None,
                  ) -> tuple[rules.EarningsCalendar, list[str]]:
    """Cached earnings calendar for `symbols`.

    Names fetched successfully keep their dates for `refresh_hours`; names
    without coverage are retried on every load (they are skipped anyway, so
    a retry costs nothing but can restore coverage). Returns the
    `rules.EarningsCalendar` plus the list of uncovered symbols.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / CACHE_FILE
    now = now or datetime.now(timezone.utc)
    cached: dict = {}
    try:
        cached = json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        cached = {}

    fresh_cut = now.timestamp() - refresh_hours * 3600
    dates: dict[str, list[str]] = {}
    fresh: set[str] = set()
    stale: list[str] = []
    for sym in symbols:
        entry = cached.get(sym.upper()) if isinstance(cached, dict) else None
        if isinstance(entry, dict) and entry.get("fetched_at", 0) >= fresh_cut \
                and entry.get("dates"):
            dates[sym.upper()] = list(entry["dates"])
            fresh.add(sym.upper())
        else:
            stale.append(sym.upper())

    if stale:
        fetch_one = fetch_one or _yf_earnings_dates
        for i, sym in enumerate(sorted(set(stale))):
            if i:
                time.sleep(YAHOO_BATCH_PAUSE_S)
            try:
                got = fetch_one(sym)
            except Exception:
                continue
            if got:
                dates[sym] = sorted({str(d)[:10] for d in got})

    # persist the merged cache (dates + fetch stamps)
    merged = {}
    for sym, ds in dates.items():
        prev = cached.get(sym) if isinstance(cached, dict) else None
        stamp = now.timestamp()
        if sym in fresh and isinstance(prev, dict):
            stamp = float(prev.get("fetched_at", 0) or stamp)
        merged[sym] = {"dates": ds, "fetched_at": stamp}
    try:
        path.write_text(json.dumps(merged, sort_keys=True), encoding="utf-8")
    except OSError:
        pass

    missing = sorted(set(s.upper() for s in symbols) - set(dates))
    calendar = rules.EarningsCalendar(
        {sym: set(ds) for sym, ds in dates.items()})
    return calendar, missing
