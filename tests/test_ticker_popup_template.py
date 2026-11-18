"""TICKER-INFO-001B — dashboard ticker company-info popup (template checks).

Frontend-only: the clickable ticker cells, the modal markup/CSS, and the
popup script must live in templates/dashboard.html and fetch the existing
backend endpoint GET /api/company/{symbol} (PR #148).
"""
from __future__ import annotations

from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"


def _text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _popup_script(text: str) -> str:
    start = text.rindex('<script>\n    (function ()')
    end = text.index("</script>", start)
    return text[start:end]


def test_company_modal_exists():
    assert 'id="company-modal"' in _text()


def test_ticker_link_appears_at_least_four_times():
    assert _text().count("ticker-link") >= 4


def test_ticker_cells_carry_data_symbol():
    text = _text()
    assert text.count('class="symbol ticker-link" data-symbol="{{ pos.symbol }}"') == 2
    assert text.count('class="symbol ticker-link" data-symbol="{{ order.symbol }}"') == 1
    assert "td.dataset.symbol = p.symbol" in text


def test_popup_script_uses_company_endpoint():
    assert "/api/company/" in _popup_script(_text())


def test_popup_closes_on_escape_and_backdrop():
    script = _popup_script(_text())
    assert "Escape" in script
    assert "e.target === modal" in script


def test_popup_script_never_uses_html_assignment():
    script = _popup_script(_text())
    assert "innerHTML" not in script
    assert "outerHTML" not in script
    assert "insertAdjacentHTML" not in script


def test_company_modal_hidden_rule_present():
    # The [hidden] attribute must win over the backdrop's display:flex,
    # otherwise the empty modal is visible on page load and blocks the page.
    assert ".company-modal-backdrop[hidden]{display:none" in _text()
