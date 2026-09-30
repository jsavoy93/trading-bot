"""P0 BUY→HOLD Dashboard — refresh-bug fix + URL state tests.

These tests verify the owner-reported "active view lost on refresh"
bug is fixed:

  PREVIOUS (BUG):
    templates/dashboard.html:3474 — `setInterval(refresh, 30000)`
    where `refresh()` was `window.location.reload()`. Every 30 seconds
    the browser did a full reload, which lost all in-memory state
    (active tab, scroll position, expanded sections). SmartBot
    completing a cycle reset the user's BUY Eligibility tab back
    to the Main Dashboard.

  FIX:
    1. The 30s auto-refresh now calls `refreshData()` (data-only,
       no URL/DOM mutation). `refresh()` is reserved for the manual
       Refresh button (still does `window.location.reload()` because
       that's the explicit user action).
    2. Hash-based URL state (`#tab=...&range=...`) preserves the
       active tab + analytics range across manual browser refresh
       (F5). A manual reload reads the hash and restores the same
       view.

Tests are read-only: they parse the template text and assert the
presence/absence of specific identifiers. No DOM execution required.
"""

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "templates" / "dashboard.html"


def _read_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _extract_function_body(text: str, func_name: str) -> str:
    """Extract the body of a top-level JS function definition.

    Uses a brace-counting approach so nested blocks (early returns,
    if/else, loops) do NOT truncate the extraction.
    """
    # Find `function NAME(...)` or `async function NAME(...)`.
    pat = re.compile(
        r"(?:async\s+)?function\s+" + re.escape(func_name) + r"\s*\([^)]*\)\s*\{",
    )
    m = pat.search(text)
    if not m:
        return ""
    start = m.end() - 1  # position of `{`
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return ""


# ─────────────────────────────────────────────────────────────────────────
# Test 8: refreshData exists, does NOT call location.reload or location.href
# ─────────────────────────────────────────────────────────────────────────
class TestRefreshDataAutoRefresh:
    """`refreshData()` is the new auto-refresh function. It MUST exist,
    be wired into setInterval, and MUST NOT call location.reload or
    location.href. `refresh()` is kept for the manual Refresh button
    and STILL calls location.reload() — that is the explicit user
    action we preserve.
    """

    def test_setInterval_refresh_is_gone(self):
        """The buggy `setInterval(refresh, 30000)` line MUST be absent."""
        text = _read_template()
        # Strip comments to avoid matching the "PREVIOUS (BUG)" comment.
        # Keep only single-line `//` comments removal; multiline is rare.
        stripped = re.sub(r"//[^\n]*", "", text)
        # The literal `setInterval(refresh, 30000)` (no `Data`) must
        # not appear in code (only in comments, which we already
        # stripped).
        assert "setInterval(refresh, 30000)" not in stripped, (
            "BUG REGRESSED: `setInterval(refresh, 30000)` is present. "
            "This was the original full-page reload bug. The fix "
            "calls `refreshData` instead."
        )

    def test_setInterval_refreshData_is_present(self):
        """The fixed `setInterval(refreshData, 30000)` MUST exist."""
        text = _read_template()
        # The actual statement, not in a comment.
        # Allow leading whitespace from template indentation.
        assert re.search(r"^\s*setInterval\(\s*refreshData\s*,\s*30000\s*\)\s*;", text, re.MULTILINE), (
            "FIX MISSING: `setInterval(refreshData, 30000)` not found. "
            "The data-only auto-refresh should replace the buggy "
            "full-page reload."
        )

    def test_refreshData_function_exists(self):
        text = _read_template()
        # Match the function declaration (allow any whitespace).
        assert re.search(r"async\s+function\s+refreshData\s*\(", text), (
            "`refreshData` function not found in template."
        )

    def test_refreshData_does_not_call_location_reload(self):
        """`refreshData()` MUST be data-only. It MUST NOT call
        `location.reload()` or `location.href = ...` because that
        would defeat the entire point of the fix.
        """
        text = _read_template()
        body = _extract_function_body(text, "refreshData")
        assert body, "refreshData function body not parseable"
        assert "location.reload" not in body, (
            "`refreshData()` calls location.reload() — this is the "
            "bug, not the fix. Auto-refresh must be data-only."
        )
        assert "location.href" not in body, (
            "`refreshData()` sets location.href — this also defeats "
            "the fix (would navigate away)."
        )

    def test_refresh_function_still_calls_location_reload(self):
        """The `refresh()` function MUST still call location.reload()
        because that is the manual Refresh button's behavior (the
        explicit user action). Removing this would silently break the
        Refresh button without anyone noticing.
        """
        text = _read_template()
        body = _extract_function_body(text, "refresh")
        assert body, "refresh() function body not parseable"
        assert "window.location.reload" in body, (
            "`refresh()` must still call `window.location.reload()` "
            "for the manual Refresh button. If you removed it, the "
            "manual button is broken."
        )


# ─────────────────────────────────────────────────────────────────────────
# Test 9: URL state restoration helpers exist
# ─────────────────────────────────────────────────────────────────────────
class TestUrlStateHelpers:
    """`setTopTabFromHash`, `updateHash`, and the `showTopTab` wiring
    MUST exist. `updateHash` MUST use `history.replaceState` (not
    `pushState`) so back-button history is not polluted by every
    tab switch or auto-refresh cycle.
    """

    def test_setTopTabFromHash_exists(self):
        text = _read_template()
        assert re.search(r"function\s+setTopTabFromHash\s*\(", text), (
            "`setTopTabFromHash()` not found. The dashboard needs it "
            "to restore the active tab from `window.location.hash` "
            "after a manual browser refresh."
        )

    def test_setTopTabFromHash_reads_window_location_hash(self):
        text = _read_template()
        body = _extract_function_body(text, "setTopTabFromHash")
        assert body, "setTopTabFromHash body not parseable"
        assert "window.location.hash" in body, (
            "`setTopTabFromHash()` must read `window.location.hash`."
        )

    def test_updateHash_uses_replaceState_not_pushState(self):
        """`updateHash()` MUST use `history.replaceState`, NOT
        `pushState`. pushState would pollute browser back-history
        with every tab switch and auto-refresh cycle.
        """
        text = _read_template()
        body = _extract_function_body(text, "updateHash")
        assert body, "updateHash body not parseable"
        assert "history.replaceState" in body, (
            "`updateHash()` must call `history.replaceState` (NOT "
            "`pushState`) so the back button history is not "
            "polluted by every tab switch."
        )
        assert "history.pushState" not in body, (
            "`updateHash()` MUST NOT call `history.pushState`. "
            "Doing so would pollute browser history with every "
            "tab switch and auto-refresh cycle."
        )

    def test_showTopTab_calls_updateHash(self):
        """`showTopTab` MUST call `updateHash` after switching tabs so
        the URL hash stays in sync with the active tab.
        """
        text = _read_template()
        body = _extract_function_body(text, "showTopTab")
        assert body, "showTopTab body not parseable"
        assert "updateHash" in body, (
            "`showTopTab()` must call `updateHash()` after switching "
            "tabs to persist the active tab in the URL hash."
        )

    def test_loadBuyFunnel_calls_updateHash(self):
        """`loadBuyFunnel()` MUST call `updateHash()` after range
        change so the URL hash reflects the current range selection.
        """
        text = _read_template()
        body = _extract_function_body(text, "loadBuyFunnel")
        assert body, "loadBuyFunnel body not parseable"
        assert "updateHash" in body, (
            "`loadBuyFunnel()` must call `updateHash()` after range "
            "change so the URL hash reflects the new range."
        )

    def test_refreshData_does_not_call_updateHash(self):
        """`refreshData()` MUST NOT call `updateHash()`. It is a
        background auto-refresh; touching the URL would be a
        side-effect that confuses the user and pollutes history
        (defeating the replaceState vs pushState discipline).
        """
        text = _read_template()
        body = _extract_function_body(text, "refreshData")
        assert body, "refreshData body not parseable"
        assert "updateHash" not in body, (
            "`refreshData()` MUST NOT call `updateHash()`. Automatic "
            "data refresh must not change the URL."
        )

    def test_hashchange_listener_registered(self):
        """A `hashchange` listener MUST be registered for back/forward
        navigation to restore the active tab.
        """
        text = _read_template()
        assert re.search(
            r"addEventListener\(\s*['\"]hashchange['\"]", text,
        ), (
            "`window.addEventListener('hashchange', ...)` not "
            "registered. Back/forward navigation will not restore "
            "the active tab."
        )

    def test_domcontentloaded_calls_setTopTabFromHash(self):
        """`DOMContentLoaded` MUST call `setTopTabFromHash()` so the
        user's last active tab is restored on page load.
        """
        text = _read_template()
        # Find all DOMContentLoaded handlers and check that at least
        # one of them calls setTopTabFromHash.
        handlers = re.findall(
            r"addEventListener\(\s*['\"]DOMContentLoaded['\"][^}]*\}",
            text, re.DOTALL,
        )
        assert handlers, "no DOMContentLoaded listener found"
        any_restoring = any("setTopTabFromHash" in h for h in handlers)
        assert any_restoring, (
            "No DOMContentLoaded handler calls `setTopTabFromHash()`. "
            "Manual browser refresh (F5) will reset the active tab."
        )


# ─────────────────────────────────────────────────────────────────────────
# Test 10: Manual browser refresh preserves URL state via hash
# ─────────────────────────────────────────────────────────────────────────
class TestManualRefreshPreservesState:
    """A manual `window.location.reload()` (browser F5) reloads the
    page. The hash-based restoration must bring the user back to the
    same tab + range. Verify end-to-end behavior:
    - showTopTab still works (no name collision)
    - setTopTabFromHash handles all five top tabs
    - The format of `#tab=...&range=...` is what setTopTabFromHash parses
    """

    def test_setTopTabFromHash_handles_all_five_top_tabs(self):
        text = _read_template()
        body = _extract_function_body(text, "setTopTabFromHash")
        assert body, "setTopTabFromHash body not parseable"
        # We don't require literal text — the implementation may
        # use a single selector. But we require that the
        # function's body references `top-tab-` (the panel id
        # prefix used by all five tabs).
        assert "top-tab-" in body, (
            "`setTopTabFromHash()` must look up the panel via the "
            "`top-tab-<name>` id convention used by the existing "
            "tab markup."
        )

    def test_getCurrentTopTab_returns_default_when_no_active_tab(self):
        """Defensive: when no `.top-tab.active` is present (initial
        page load before any tab interaction), `getCurrentTopTab`
        MUST return a valid default. Otherwise updateHash would emit
        `#tab=undefined` which is unparseable.
        """
        text = _read_template()
        body = _extract_function_body(text, "getCurrentTopTab")
        assert body, "getCurrentTopTab body not parseable"
        # Must return a string fallback (e.g., 'dashboard').
        assert re.search(r"return\s+['\"]dashboard['\"]", body), (
            "`getCurrentTopTab()` must have a 'dashboard' fallback."
        )

    def test_url_format_supported(self):
        """The hash format `#tab=...&range=...` MUST be parseable by
        setTopTabFromHash using URLSearchParams.
        """
        text = _read_template()
        body = _extract_function_body(text, "setTopTabFromHash")
        assert body, "setTopTabFromHash body not parseable"
        assert "URLSearchParams" in body, (
            "`setTopTabFromHash()` must use `URLSearchParams` to "
            "parse the hash."
        )
        # The actual tab lookup must use `params.get('tab')`.
        assert "params.get('tab')" in body or 'params.get("tab")' in body, (
            "`setTopTabFromHash()` must read the 'tab' param."
        )
        assert "params.get('range')" in body or 'params.get("range")' in body, (
            "`setTopTabFromHash()` must read the 'range' param."
        )


# ─────────────────────────────────────────────────────────────────────────
# Test 11 (bonus): buy-blockers card and JS loader wiring is present
# ─────────────────────────────────────────────────────────────────────────
class TestBuyBlockersCardWiring:
    """The BUY→HOLD Attribution card MUST exist in the buyfunnel tab
    panel and have all the supporting JS loaders wired up.
    """

    def test_buy_blockers_card_in_buyfunnel_panel(self):
        text = _read_template()
        # Card marker
        assert 'id="buy-blockers-card"' in text, (
            "BUY→HOLD Attribution card #buy-blockers-card not found."
        )
        # Must be inside the buyfunnel tab panel.
        # Find the buyfunnel panel and assert the card id appears
        # before its closing div.
        bf_panel_start = text.find('id="top-tab-buyfunnel"')
        bf_panel_end = text.find('<!-- /top-tab-buyfunnel -->', bf_panel_start)
        assert bf_panel_start != -1 and bf_panel_end != -1, (
            "buyfunnel tab panel boundaries not found"
        )
        bf_panel = text[bf_panel_start:bf_panel_end]
        assert 'id="buy-blockers-card"' in bf_panel, (
            "BUY→HOLD Attribution card is not inside the buyfunnel "
            "tab panel."
        )

    def test_buy_blockers_endpoints_called(self):
        text = _read_template()
        assert "/api/buy-blockers/summary" in text
        assert "/api/buy-blockers?" in text or "/api/buy-blockers" in text

    def test_loadBuyBlockers_function_defined(self):
        text = _read_template()
        assert re.search(
            r"async\s+function\s+loadBuyBlockers\s*\(", text,
        ), "`loadBuyBlockers()` function not defined"

    def test_loadBuyBlockers_called_from_refreshData(self):
        text = _read_template()
        body = _extract_function_body(text, "refreshData")
        assert body, "refreshData body not parseable"
        assert "loadBuyBlockers" in body, (
            "`refreshData()` must call `loadBuyBlockers()` so the "
            "BUY→HOLD Attribution card updates during auto-refresh."
        )

    def test_loadBuyBlockers_called_from_buyfunnel_observer(self):
        """The buyfunnel tab auto-load observer MUST call
        `loadBuyBlockers()` so the card populates the first time
        the user clicks the BUY Eligibility tab.
        """
        text = _read_template()
        m = re.search(
            r"\(function\s+_bb_setupAutoLoad\s*\(\)\s*\{(.*?)\}\s*\)\s*\(\)",
            text, re.DOTALL,
        )
        assert m, "buyfunnel auto-load observer not found"
        body = m.group(1)
        assert "loadBuyBlockers" in body, (
            "buyfunnel auto-load observer must call `loadBuyBlockers()`."
        )

    def test_xss_safe_text_interpolation(self):
        """All text rendered from API payloads MUST be escaped via
        `_lc_escapeHtml`. The dashboard template uses this helper
        consistently for Phase C, Phase B, and earlier cards.
        """
        text = _read_template()
        # Find the _bb_renderDetail body and assert it uses
        # _lc_escapeHtml on every API-derived string.
        body = _extract_function_body(text, "_bb_renderDetail")
        assert body, "_bb_renderDetail not found"
        # At least 8 distinct _lc_escapeHtml calls (one per column).
        assert body.count("_lc_escapeHtml") >= 8, (
            "Each API-derived field in the detail table must be "
            "XSS-escaped. Found fewer than 8 _lc_escapeHtml calls."
        )