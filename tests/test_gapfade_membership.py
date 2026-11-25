"""GAPFADE-MIDCAP-PAPER — S&P 400 membership sourcing (offline).

Every test stubs the fetch layer (`load_members(fetch=...)` or
`membership._download`); NO test touches the network. Fixtures are shaped
like the real payloads: a Wikipedia constituents table page and a
Slickcharts S&P 400 table page.
"""

import os
import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from experiments.gapfade_midcap_paper import membership  # noqa: E402

TODAY = date(2026, 11, 25)


def _symbols(n, prefix="W"):
    return [f"{prefix}{i:03d}" for i in range(n)]


def wikipedia_page(symbols) -> str:
    """Wikipedia-table-shaped payload (Symbol/Security/... columns)."""
    rows = [
        "<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th>"
        "<th>GICS Sub-Industry</th></tr>"
    ]
    for i, sym in enumerate(symbols):
        rows.append(
            f'<tr><td><a href="/wiki/X" title="X">{sym}</a></td>'
            f"<td>Company {i}</td><td>Industrials</td>"
            f"<td>Sub {i}</td></tr>")
    return (
        "<html><body><table class='wikitable sortable'>"
        + "".join(rows)
        + "</table></body></html>")


def slickcharts_page(symbols) -> str:
    """Slickcharts-shaped payload (# / Company / Symbol / Weight ...)."""
    rows = [
        "<tr><th>#</th><th>Company</th><th>Symbol</th><th>Weight</th>"
        "<th>Price</th></tr>"
    ]
    for i, sym in enumerate(symbols):
        rows.append(
            f"<tr><td>{i + 1}</td><td><a href=\"/symbol/{sym}\">Co {i}</a>"
            f"</td><td><a href=\"/symbol/{sym}\">{sym}</a></td>"
            f"<td>0.9%</td><td>10.00</td></tr>")
    return "<html><body><table>" + "".join(rows) + "</table></body></html>"


GOOD = _symbols(400)
assert len(GOOD) == 400


def test_parses_wikipedia_table():
    got = membership.parse_members(wikipedia_page(GOOD), "wikipedia")
    assert got == sorted(GOOD)


def test_parses_slickcharts_table():
    got = membership.parse_members(slickcharts_page(GOOD[:396]),
                                   "slickcharts")
    assert len(got) == 396
    assert got == sorted(GOOD[:396])


def test_parse_drops_non_symbol_cells():
    page = wikipedia_page(["AAA", "US Dollar", "-", "BBB"])
    got = membership.parse_members(page, "wikipedia")
    assert got == ["AAA", "BBB"]


def test_parse_rejects_payload_without_symbol_column():
    with pytest.raises(membership.MembershipError):
        membership.parse_members("<table><tr><td>nope</td></tr></table>",
                                 "wikipedia")


def test_validate_range():
    assert membership.validate(GOOD) == sorted(GOOD)
    for bad in (GOOD[:379], _symbols(421), []):
        with pytest.raises(membership.MembershipError):
            membership.validate(bad, "wikipedia")


def test_primary_success_in_range(tmp_path):
    calls = []

    def fetch(name, url):
        calls.append(name)
        return wikipedia_page(GOOD)

    assert membership.load_members(tmp_path, fetch=fetch, today=TODAY) == (
        sorted(GOOD))
    assert calls == ["wikipedia"]                    # fallback untouched
    cache = tmp_path / "sp400_members.txt"
    assert cache.exists() and len(cache.read_text().split()) == 400


def test_primary_out_of_range_uses_fallback(tmp_path):
    calls = []

    def fetch(name, url):
        calls.append(name)
        if name == "wikipedia":
            return wikipedia_page(_symbols(120, "S"))  # import/partial page
        return slickcharts_page(_symbols(400, "F"))

    got = membership.load_members(tmp_path, fetch=fetch, today=TODAY)
    assert calls == ["wikipedia", "slickcharts"]
    assert got == sorted(_symbols(400, "F"))
    assert (tmp_path / "sp400_members.txt").read_text().split()[0] == "F000"


def test_out_of_range_fallback_never_passes(tmp_path):
    def fetch(name, url):
        return wikipedia_page(_symbols(500, "X"))    # both lists too long

    with pytest.raises(membership.MembershipError):
        membership.load_members(tmp_path, fetch=fetch, today=TODAY)
    assert not (tmp_path / "sp400_members.txt").exists()


def test_all_sources_fail_fresh_cache_warns_loudly(tmp_path, capsys):
    cache = tmp_path / "sp400_members.txt"
    cache.write_text("\n".join(GOOD) + "\n")
    age = 3 * 86400
    os.utime(cache, (cache.stat().st_mtime - age, cache.stat().st_mtime - age))

    def boom(name, url):
        raise OSError("network down")

    got = membership.load_members(tmp_path, fetch=boom, today=TODAY)
    assert got == sorted(GOOD)
    out = capsys.readouterr().out
    assert "WARNING" in out and "STALE" in out
    assert "3.0 days old" in out and "400" in out


def test_all_sources_fail_cache_too_stale_raises(tmp_path):
    cache = tmp_path / "sp400_members.txt"
    cache.write_text("\n".join(GOOD) + "\n")
    age = 11 * 86400
    os.utime(cache, (cache.stat().st_mtime - age, cache.stat().st_mtime - age))

    def boom(name, url):
        raise OSError("network down")

    with pytest.raises(membership.MembershipError):
        membership.load_members(tmp_path, fetch=boom, today=TODAY)


def test_all_sources_fail_no_cache_raises(tmp_path):
    def boom(name, url):
        raise OSError("network down")

    with pytest.raises(membership.MembershipError):
        membership.load_members(tmp_path, fetch=boom, today=TODAY)


def test_same_day_cache_skips_network(tmp_path):
    def fetch(name, url):
        return wikipedia_page(GOOD)

    membership.load_members(tmp_path, fetch=fetch, today=TODAY)

    def boom(name, url):
        raise AssertionError("network must not be touched today")

    assert membership.load_members(tmp_path, fetch=boom, today=TODAY) == (
        sorted(GOOD))


def test_default_fetch_uses_stdlib_download(monkeypatch, tmp_path):
    """The default fetch layer is `_download` (no injectable needed)."""
    seen = []

    def fake_download(name, url):
        seen.append(url)
        return wiki if url == membership.WIKIPEDIA_URL else ""
    wiki = wikipedia_page(GOOD)

    monkeypatch.setattr(membership, "_download", fake_download)
    assert membership.load_members(tmp_path, today=TODAY) == sorted(GOOD)
    assert seen == [membership.WIKIPEDIA_URL]


def test_dead_sources_are_gone():
    """The dead MDY/iShares URLs must not be referenced anywhere."""
    src = Path(membership.__file__).read_text()
    body = src.split('"""', 2)[2]          # ignore the module docstring
    assert "ssga.com" not in body
    assert "ishares.com" not in body
    assert "mdy_holdings" not in body
