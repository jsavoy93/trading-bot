"""GAPFADE-MIDCAP-PAPER — S&P 400 membership (PRIMARY + FALLBACK sources).

The old source (SSGA's SPDR MDY holdings CSV) is dead: it answers HTTP 404.
iShares' IJH holdings CSV is also unusable from this box (Akamai HTTP 403),
so the two working, free, public sources are:

  PRIMARY  Wikipedia "List of S&P 400 companies" — the Symbol column of the
           constituents table:
             https://en.wikipedia.org/wiki/List_of_S%26P_400_companies
  FALLBACK Slickcharts S&P 400 list — the Symbol column of the table:
             https://www.slickcharts.com/sp400

Both are parsed with the stdlib only (`html.parser`); no third-party deps.

Validation: a source's list is accepted only when it has
MIN_MEMBERS..MAX_MEMBERS unique symbols. An out-of-range list counts as a
FAILED source (never traded) and the next source is tried.

Order of resolution in `load_members`:
  1. same-day cache (written by an earlier successful fetch today)
  2. primary -> fallback, each validated; a success is written to the cache
  3. the cache, only if younger than MAX_AGE_DAYS, with a LOUD stale warning
  4. MembershipError (fail closed: no universe, no entries)

Members that drop out of the list simply stop being candidates on the next
refresh; names with an open position stay in state and are still exited by
that position's rules.

The ticker list is NEVER hardcoded: the parsers only read the vendor payload,
and those parsers are what the unit tests feed fixtures.
"""

from __future__ import annotations

import io
import re
import time
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"
SLICKCHARTS_URL = "https://www.slickcharts.com/sp400"

#: (name, url) in resolution order: primary first, fallback second.
SOURCES = (("wikipedia", WIKIPEDIA_URL), ("slickcharts", SLICKCHARTS_URL))

MIN_MEMBERS = 380           # an index list outside this range is rejected
MAX_MEMBERS = 420
MAX_AGE_DAYS = 10           # refuse a cached membership file older than this
_CACHE_FILE = "sp400_members.txt"      # one symbol per line
_STAMP_FILE = "sp400_members.downloaded"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0 Safari/537.36")
_SYM_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,6}")


class MembershipError(RuntimeError):
    """Raised when the membership list is unavailable or unusably stale."""


def _clean_symbol(cell: str) -> str | None:
    """Normalise a table cell to an equity symbol, or None."""
    sym = cell.replace("\u00a0", " ").strip().upper()
    sym = re.sub(r"\s+", "", sym)
    if not sym or not _SYM_RE.fullmatch(sym):
        return None
    return sym


class _TableParser(HTMLParser):
    """Collect every table's rows of cells so a header row can be located."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[str]] = []   # per-table cells, row-major
        self._cur_table: list[str] | None = None
        self._cur_row: list[str] | None = None
        self._cur_cell: list[str] | None = None
        self._skip = 0                      # inside <script>/<style>

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "table":
            self._flush_row()
            self._flush_table()
            self._cur_table = []
        elif tag == "tr" and self._cur_table is not None:
            self._flush_row()
            self._cur_row = []
        elif tag in ("td", "th") and self._cur_row is not None:
            self._flush_cell()
            self._cur_cell = []
        if tag in ("br",):
            if self._cur_cell is not None:
                self._cur_cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in ("td", "th"):
            self._flush_cell()
        elif tag == "tr":
            self._flush_row()
        elif tag == "table":
            self._flush_row()
            self._flush_table()

    def handle_data(self, data):
        if self._skip:
            return
        if self._cur_cell is not None:
            self._cur_cell.append(data)

    def _flush_cell(self):
        if self._cur_cell is not None and self._cur_row is not None:
            self._cur_row.append("".join(self._cur_cell).strip())
        self._cur_cell = None

    def _flush_row(self):
        self._flush_cell()
        if self._cur_row and self._cur_table is not None:
            self._cur_table.append(self._cur_row)
        self._cur_row = None

    def _flush_table(self):
        if self._cur_table:
            self.tables.append(self._cur_table)
        self._cur_table = None


def _symbols_from_html(text: str, header: str) -> list[str]:
    """Parse an HTML page and return the symbol column of the table whose
    header row contains a cell equal to `header` (case-insensitive)."""
    parser = _TableParser()
    parser.feed(text)
    parser.close()
    best: list[str] = []
    for rows in parser.tables:
        if not rows:
            continue
        head = [c.strip().lower() for c in rows[0]]
        if header not in head:
            continue
        idx = head.index(header)
        out: list[str] = []
        for row in rows[1:]:
            if idx >= len(row):
                continue
            sym = _clean_symbol(row[idx])
            if sym:
                out.append(sym)
        if len(out) > len(best):
            best = out
    if not best:
        raise MembershipError(f"no {header!r} column found in payload")
    return sorted(set(best))


def parse_wikipedia(text: str) -> list[str]:
    """Wikipedia 'List of S&P 400 companies' -> sorted symbols."""
    return _symbols_from_html(text, "symbol")


def parse_slickcharts(text: str) -> list[str]:
    """Slickcharts S&P 400 table -> sorted symbols."""
    return _symbols_from_html(text, "symbol")


def parse_members(text: str, source: str) -> list[str]:
    """Dispatch a payload to the parser for `source` (never a hardcoded list)."""
    if source == "wikipedia":
        return parse_wikipedia(text)
    if source == "slickcharts":
        return parse_slickcharts(text)
    if source == "cache":
        return _parse_cache(text)
    raise MembershipError(f"unknown membership source {source!r}")


def _parse_cache(text: str) -> list[str]:
    out = []
    for line in io.StringIO(text):
        sym = _clean_symbol(line)
        if sym:
            out.append(sym)
    if not out:
        raise MembershipError("cached membership list is empty")
    return sorted(set(out))


def validate(symbols: list[str], source: str = "") -> list[str]:
    """Accept a list only when its unique-symbol count is in range."""
    n = len(set(symbols))
    if not (MIN_MEMBERS <= n <= MAX_MEMBERS):
        raise MembershipError(
            f"{source or 'membership'} list has {n} unique symbols, outside "
            f"{MIN_MEMBERS}-{MAX_MEMBERS}; refusing it")
    return sorted(set(symbols))


def _download(_source: str, url: str) -> str:
    """GET a source page with the stdlib (no auth needed)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace")


def _cache_age_days(cache: Path) -> float:
    return (time.time() - cache.stat().st_mtime) / 86400.0


def load_members(cache_dir: str | Path, fetch=None, today: date | None = None,
                 max_age_days: int = MAX_AGE_DAYS) -> list[str]:
    """Return the current S&P 400 members (validated, cached, fail-closed).

    `fetch(source_name, url) -> str` is injectable (tests pass fixtures;
    never network). Default order: same-day cache, primary, fallback,
    then a cached copy within `max_age_days` (LOUD warning), else
    MembershipError. A source whose list is out of range is treated as
    failed and the next source is tried; an out-of-range list never passes.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / _CACHE_FILE
    stamp = cache_dir / _STAMP_FILE
    today = today or datetime.now(timezone.utc).date()
    fetcher = fetch or _download
    errors: list[str] = []

    # 1. same-day cache written by an earlier successful fetch today
    if cache.exists() and stamp.exists() and cache.stat().st_size > 0:
        try:
            stamped = date.fromisoformat(stamp.read_text().strip())
        except (ValueError, OSError):
            stamped = None
        if stamped == today:
            try:
                return validate(
                    _parse_cache(cache.read_text(encoding="utf-8",
                                                 errors="replace")),
                    "cache")
            except MembershipError as exc:
                errors.append(f"cache: {exc}")

    # 2. live sources, primary then fallback
    for name, url in SOURCES:
        try:
            text = fetcher(name, url)
            members = validate(parse_members(text, name), name)
        except Exception as exc:      # network/parse/range failure -> next
            errors.append(f"{name}: {exc}")
            continue
        cache.write_text("\n".join(members) + "\n", encoding="utf-8")
        stamp.write_text(today.isoformat() + "\n", encoding="utf-8")
        return members

    # 3. cached copy within the staleness limit (loudly)
    if cache.exists() and cache.stat().st_size > 0:
        age = _cache_age_days(cache)
        try:
            members = validate(
                _parse_cache(cache.read_text(encoding="utf-8",
                                             errors="replace")), "cache")
        except MembershipError as exc:
            errors.append(f"cache: {exc}")
        else:
            if age > max_age_days:
                errors.append(
                    f"cache is {age:.1f} days old (> {max_age_days})")
            else:
                print(
                    "WARNING: STALE S&P 400 MEMBERSHIP CACHE IN USE — every "
                    f"live source failed ({'; '.join(errors)}); trading the "
                    f"cached list of {len(members)} symbols that is {age:.1f} "
                    "days old (limit "
                    f"{max_age_days}). Check membership.py sources NOW.")
                return members

    # 4. fail closed
    raise MembershipError(
        "S&P 400 membership unavailable from every source "
        f"({'; '.join(errors)}) and no usable cached copy in {cache_dir}")
