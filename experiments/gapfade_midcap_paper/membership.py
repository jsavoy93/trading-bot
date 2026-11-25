"""GAPFADE-MIDCAP-PAPER — S&P 400 membership (ONE documented source).

Source: SSGA's SPDR S&P MidCap 400 ETF (MDY) holdings CSV, the maintained
constituent list SSGA publishes for the index the ETF tracks:

    https://www.ssga.com/us/en/intermediary/library-content/products/
    fund-finder/etfs/productsx/export/holdings-daily-us-en-mdy.xlsx

The CSV export variant used here is:

    https://www.ssga.com/us/en/intermediary/library-content/products/
    fund-finder/etfs/productsx/export/holdings-daily-us-en-mdy.csv

Refresh: the runner re-downloads the file at most once per calendar day
(cached next to the state file) and only falls back to an older cached copy
when the download fails; a copy older than MAX_AGE_DAYS is refused (fail
closed) instead of silently trading a stale universe. Members that drop out
of the list simply stop being candidates on the next refresh; names with an
open position stay in state and are still exited by that position's rules.

The ticker list is NEVER hardcoded: `parse_holdings` only parses the
vendor payload, and `parse_holdings` is what the unit tests feed fixtures.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MDY_HOLDINGS_URL = (
    "https://www.ssga.com/us/en/intermediary/library-content/products/"
    "fund-finder/etfs/productsx/export/holdings-daily-us-en-mdy.csv")
MAX_AGE_DAYS = 10           # refuse a membership file older than this
_CACHE_FILE = "mdy_holdings.csv"
_STAMP_FILE = "mdy_holdings.downloaded"
_USER_AGENT = "Mozilla/5.0 (gapfade-midcap-paper; paper research)"


class MembershipError(RuntimeError):
    """Raised when the membership list is unavailable or unusably stale."""


def parse_holdings(text: str) -> list[str]:
    """Parse the SSGA MDY holdings CSV into a sorted ticker list.

    The file has a two-line preamble ("Fund Name,SPDR ..." / "Ticker,...")
    followed by the constituent rows; the 'Ticker' column holds the symbol.
    Rows whose ticker is not a plain equity symbol (cash/futures lines such
    as 'US Dollar' or '-') are dropped.
    """
    reader = csv.reader(io.StringIO(text))
    header = None
    out: list[str] = []
    for row in reader:
        if not row:
            continue
        cells = [c.strip() for c in row]
        if header is None:
            if cells and cells[0].lower() == "ticker":
                header = [c.lower() for c in cells]
            continue
        if not cells[0]:
            continue
        sym = cells[0].upper()
        if not sym.replace(".", "").replace("-", "").isalnum():
            continue
        out.append(sym)
    if header is None or not out:
        raise MembershipError("MDY holdings payload has no Ticker section")
    return sorted(set(out))


def _download(dest_dir: Path) -> str:
    """GET the MDY holdings CSV with the stdlib (no auth needed)."""
    import urllib.request
    req = urllib.request.Request(MDY_HOLDINGS_URL,
                                headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace")


def load_members(cache_dir: str | Path, fetch=None, today: date | None = None,
                 max_age_days: int = MAX_AGE_DAYS) -> list[str]:
    """Return the current S&P 400 members, refreshing the cache daily.

    `fetch()` is injectable (tests pass a fixture payload; never network).
    Raises MembershipError when no copy exists and the fetch fails, or when
    the only copy is older than `max_age_days`.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / _CACHE_FILE
    stamp = cache_dir / _STAMP_FILE
    today = today or datetime.now(timezone.utc).date()

    text = None
    if cache.exists() and stamp.exists():
        try:
            stamped = date.fromisoformat(stamp.read_text().strip())
            if stamped == today and cache.stat().st_size > 0:
                text = cache.read_text(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            text = None

    if text is None:
        fetcher = fetch or (lambda: _download(cache_dir))
        try:
            text = fetcher()
        except Exception as exc:      # network failure -> cached copy only
            if not cache.exists():
                raise MembershipError(
                    f"MDY holdings unavailable ({exc}) and no cached copy in "
                    f"{cache_dir}") from exc
            age = time.time() - cache.stat().st_mtime
            if age > max_age_days * 86400:
                raise MembershipError(
                    f"MDY holdings download failed ({exc}) and the cached "
                    f"copy is {age / 86400:.1f} days old (> "
                    f"{max_age_days})") from exc
            text = cache.read_text(encoding="utf-8", errors="replace")
        else:
            cache.write_text(text, encoding="utf-8")
            stamp.write_text(today.isoformat() + "\n", encoding="utf-8")

    return parse_holdings(text)
