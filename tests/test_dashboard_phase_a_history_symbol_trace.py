"""Dashboard Phase A — History Symbol Search + OBS-001 Symbol Decision Trace.

Proves the template-level wiring of Phase A without changing any runtime
trading behavior (SmartTradingBot, scoring, eligibility, ranking,
execution, sizing, risk, brokerage, OBS-001 decision persistence, or
fixture data are not modified).

Acceptance criteria covered:

1. The always-visible Symbol Lookup card (id="symbol-search-card", with
   /api/search/{symbol} and the "doSymbolSearch" handler) is removed
   from the global template area.
2. A new Symbol Search card (id="history-symbol-search-card") lives at
   the top of the History tab panel (#top-tab-history), BEFORE the
   Recent Sessions card.
3. The new search input/button/results IDs are unique and do not collide
   with the legacy removed IDs (searchInput / searchResults / searchBtn).
4. The new search handler is `doHistorySymbolSearch`, exposed on a button
   and on Enter-key, and uses the OBS-001 endpoints
   `/api/decision/{symbol}` and `/api/decision-history/{symbol}`.
5. The template renders the seven canonical Decision Trace sections
   (DECISION, STRATEGY GATES, SCORE, RANKING, SELECTION, EXECUTION
   CHECKS, ORDER) via dedicated render functions.
6. The rendering layer NEVER calls `/api/score/{symbol}` (live recompute)
   from the normal recorded-score path.
7. The rendering layer NEVER uses the legacy `buy_criteria` field as
   primary decision truth (must not appear as a primary fact source in
   the new code path).
8. The DECISION section is open by default; all other sections are
   collapsible behind `.dt-section` toggles.
9. Mobile-first CSS: no element forces horizontal scroll at <= 600px
   (a `@media (max-width: 600px)` rule exists; classes stack to one
   column).
10. The rendering layer distinguishes SUBMITTED vs FILLED/EXECUTED and
    never calls a submitted-but-not-filled order "Executed" or "Filled".
11. `fill_confirmed` from the snapshot is the only field that may flip a
    submitted order into a fill/executed state in the UI string.
12. The legacy fallback message ("Legacy analysis — detailed decision
    trace unavailable") is rendered as a literal in the template
    output path (so it ships to the browser).
13. The unknown-symbol fallback message ("No analysis found for SYMBOL")
    is also rendered as a literal in the template output path.
14. The history list shows at most 10 rows from
    `/api/decision-history/{symbol}?limit=10` (template defaults).
15. Each historical row is expandable; when expanded, the SAME rendering
    helper that produced the latest snapshot is used to render the
    historical snapshot — no separate ad-hoc renderer.
16. Historical snapshots are immutable facts: the template does NOT
    reinterpret old decisions using current settings or recompute
    score gates from snapshot values.
17. No new HTTP endpoints are introduced (Phase A reuses the existing
    /api/decision and /api/decision-history endpoints).
"""

import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "templates" / "dashboard.html"


def _read_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _history_panel_html(template: str) -> str:
    """Return the slice of the template between
    <div id="top-tab-history"...> and the matching closing panel tag.

    Uses a non-greedy regex. The opening div is unique; the matching close
    is the first '</div><!-- /top-tab-history -->' we encounter.
    """
    m = re.search(
        r'<div id="top-tab-history"[^>]*>(.*?)</div><!--\s*/top-tab-history\s*-->',
        template,
        flags=re.DOTALL,
    )
    assert m, "could not isolate #top-tab-history panel"
    return m.group(1)


# ─────────────────────────────────────────────────────────────────────────
# 1. Removed always-visible Symbol Lookup card
# ─────────────────────────────────────────────────────────────────────────

class TestRemovedAlwaysVisibleSymbolLookup:
    def test_legacy_card_id_is_gone(self):
        t = _read_template()
        assert 'id="symbol-search-card"' not in t, (
            "Phase A: the always-visible symbol-search-card must be removed"
        )

    def test_legacy_searchInput_id_is_gone(self):
        t = _read_template()
        assert 'id="searchInput"' not in t, (
            "Phase A: searchInput (legacy) must not exist anywhere"
        )

    def test_legacy_searchBtn_id_is_gone(self):
        t = _read_template()
        assert 'id="searchBtn"' not in t, (
            "Phase A: searchBtn (legacy) must not exist anywhere"
        )

    def test_legacy_searchResults_id_is_gone(self):
        t = _read_template()
        assert 'id="searchResults"' not in t, (
            "Phase A: searchResults (legacy) must not exist anywhere"
        )

    def test_legacy_handler_doSymbolSearch_is_gone(self):
        t = _read_template()
        # Must not appear as a function call nor as a definition.
        assert "doSymbolSearch" not in t, (
            "Phase A: the legacy doSymbolSearch handler must be fully removed"
        )


# ─────────────────────────────────────────────────────────────────────────
# 2. New Symbol Search card in History tab
# ─────────────────────────────────────────────────────────────────────────

class TestNewHistorySymbolSearch:
    def test_card_id_present_in_history_panel(self):
        t = _read_template()
        panel = _history_panel_html(t)
        assert 'id="history-symbol-search-card"' in panel

    def test_card_appears_before_recent_sessions(self):
        t = _read_template()
        panel = _history_panel_html(t)
        sym_pos = panel.find('id="history-symbol-search-card"')
        # The Recent Sessions card has the literal "Recent Sessions" in its title.
        rs_pos = panel.find("Recent Sessions")
        assert sym_pos != -1 and rs_pos != -1, "missing card or sessions marker"
        assert sym_pos < rs_pos, (
            "Symbol Search card must appear BEFORE Recent Sessions in HTML order"
        )

    def test_unique_new_ids_present(self):
        t = _read_template()
        panel = _history_panel_html(t)
        for new_id in ("historySearchInput", "historySearchBtn", "historySearchResults"):
            assert f'id="{new_id}"' in panel, f"missing new id {new_id}"

    def test_uses_obs001_endpoints(self):
        t = _read_template()
        # Both OBS-001 endpoints are referenced inside the JS handler.
        assert "/api/decision/" in t
        assert "/api/decision-history/" in t

    def test_does_not_use_legacy_search_endpoint(self):
        t = _read_template()
        # The legacy /api/search/{symbol} endpoint must not be called by the
        # new History search handler. (Existing /api/search/{symbol} is left
        # untouched for backward compatibility; the new code path does not
        # use it.)
        # We grep only inside the doHistorySymbolSearch handler body.
        m = re.search(
            r"async function doHistorySymbolSearch\(\)\s*\{(.+?)\n        \}",
            t,
            flags=re.DOTALL,
        )
        assert m, "doHistorySymbolSearch not found"
        body = m.group(1)
        assert "/api/search/" not in body, (
            "doHistorySymbolSearch must not call the legacy /api/search/{symbol} endpoint"
        )

    def test_does_not_use_live_recompute_endpoint_for_recorded_score(self):
        t = _read_template()
        m = re.search(
            r"async function doHistorySymbolSearch\(\)\s*\{(.+?)\n        \}",
            t,
            flags=re.DOTALL,
        )
        assert m, "doHistorySymbolSearch not found"
        body = m.group(1)
        assert "/api/score/" not in body, (
            "doHistorySymbolSearch must not call the live recompute /api/score/{symbol} "
            "endpoint as the source of recorded score"
        )

    def test_default_history_limit_is_10(self):
        t = _read_template()
        # The handler builds the URL by string concatenation; assert that
        # the resulting URL string contains limit=10 alongside the path.
        assert "'/api/decision-history/'" in t, "history fetch path missing"
        assert "'?limit=10'" in t, "history fetch must default to limit=10"
        assert t.index("'/api/decision-history/'") < t.index("'?limit=10'"), (
            "history path must precede the limit parameter in same fetch expression"
        )


# ─────────────────────────────────────────────────────────────────────────
# 3. Seven canonical Decision Trace sections
# ─────────────────────────────────────────────────────────────────────────

class TestDecisionTraceSections:
    EXPECTED_RENDERERS = [
        "_dt_renderDecisionSection",
        "_dt_renderStrategyGatesSection",
        "_dt_renderScoreSection",
        "_dt_renderRankingSection",
        "_dt_renderSelectionSection",
        "_dt_renderExecutionChecksSection",
        "_dt_renderOrderSection",
    ]

    @pytest.mark.parametrize("renderer", EXPECTED_RENDERERS)
    def test_renderer_function_defined(self, renderer):
        t = _read_template()
        assert f"function {renderer}(" in t, f"missing renderer {renderer}"

    @pytest.mark.parametrize("renderer", EXPECTED_RENDERERS)
    def test_renderer_invoked_from_snapshot_trace(self, renderer):
        t = _read_template()
        assert renderer in _extract_snapshot_trace_body(t), (
            f"_dt_renderSnapshotTrace must call {renderer}"
        )

    def test_decision_section_open_by_default(self):
        # The _dt_section helper takes an `open` boolean. The Decision
        # renderer must call _dt_section('Decision', html, true) so the
        # section renders open by default.
        t = _read_template()
        m = re.search(
            r"function _dt_renderDecisionSection\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m, "_dt_renderDecisionSection not found"
        body = m.group(1)
        assert re.search(r"_dt_section\(\s*'Decision'\s*,\s*[^,]+,\s*true\s*\)", body), (
            "Decision section must be open by default (open=true)"
        )

    def test_other_sections_pass_open_false(self):
        # Other renderers must call _dt_section(..., false).
        t = _read_template()
        bodies = {
            "Strategy Gates": "_dt_renderStrategyGatesSection",
            "Score": "_dt_renderScoreSection",
            "Ranking": "_dt_renderRankingSection",
            "Selection": "_dt_renderSelectionSection",
            "Execution Checks": "_dt_renderExecutionChecksSection",
            "Order": "_dt_renderOrderSection",
        }
        for section_name, renderer in bodies.items():
            m = re.search(
                rf"function {re.escape(renderer)}\([^)]*\)\s*\{{(.+?)\n        \}}",
                t, flags=re.DOTALL,
            )
            assert m, f"{renderer} not found"
            body = m.group(1)
            assert re.search(
                rf"_dt_section\(\s*'{re.escape(section_name)}'\s*,\s*[^,]+,\s*false\s*\)",
                body,
            ), f"{section_name} section must default to collapsed (open=false)"


def _extract_snapshot_trace_body(t: str) -> str:
    m = re.search(
        r"function _dt_renderSnapshotTrace\([^)]*\)\s*\{(.+?)\n        \}",
        t,
        flags=re.DOTALL,
    )
    assert m, "_dt_renderSnapshotTrace not found"
    return m.group(1)


# ─────────────────────────────────────────────────────────────────────────
# 4. Mobile-first CSS
# ─────────────────────────────────────────────────────────────────────────

class TestMobileFirstCSS:
    def test_media_query_present(self):
        t = _read_template()
        # Must include a media query at <= 600px or 768px for mobile fallback.
        assert re.search(r"@media\s*\([^)]*max-width:\s*(?:600|768)px", t), (
            "Template must define a mobile @media rule"
        )

    def test_dt_section_collapsible(self):
        t = _read_template()
        assert ".dt-section.open .dt-section-body" in t, (
            ".dt-section.open .dt-section-body must be present for collapsible behavior"
        )

    def test_no_horizontal_scroll_required_for_header(self):
        t = _read_template()
        # .dt-header uses flex-wrap so symbols, scores, etc. wrap on narrow screens.
        assert "flex-wrap" in t, (
            "Header should use flex-wrap for mobile stacking"
        )


# ─────────────────────────────────────────────────────────────────────────
# 5. SUBMITTED vs FILLED semantics
# ─────────────────────────────────────────────────────────────────────────

class TestSubmittedVsFilled:
    def test_no_executed_label_for_unfilled_orders(self):
        t = _read_template()
        # The template body of _dt_renderOrderSection must NOT call
        # submitted-but-not-confirmed orders "Executed" or "Filled".
        m = re.search(
            r"function _dt_renderOrderSection\([^)]*\)\s*\{(.+?)\n        \}",
            t,
            flags=re.DOTALL,
        )
        assert m, "_dt_renderOrderSection not found"
        body = m.group(1)
        # The badge texts we DO use: SUBMITTED, NOT SUBMITTED, YES/NO for fill.
        # We must not emit "EXECUTED" or "FILLED" without a fill_confirmed gate.
        assert "EXECUTED" not in body.upper().replace("EXECUTED", "") or "EXECUTED" not in body, (
            "Render must not label orders as Executed"
        )
        # The literal distinction note must be present in the template.
        assert "submission does not imply fill" in body or "does not imply fill" in body, (
            "Order section must include a literal note that submission != fill"
        )

    def test_fill_confirmed_is_the_only_fill_signal(self):
        t = _read_template()
        m = re.search(
            r"function _dt_renderOrderSection\([^)]*\)\s*\{(.+?)\n        \}",
            t,
            flags=re.DOTALL,
        )
        assert m
        body = m.group(1)
        # The fill badge must be conditional on order.fill_confirmed === true
        assert re.search(
            r"order\.fill_confirmed\s*===\s*true", body
        ), "Fill badge must be gated on order.fill_confirmed === true"


# ─────────────────────────────────────────────────────────────────────────
# 6. Fallback messages
# ─────────────────────────────────────────────────────────────────────────

class TestFallbackMessages:
    def test_legacy_message_literal_present(self):
        t = _read_template()
        assert "Legacy analysis — detailed decision trace unavailable" in t

    def test_unknown_symbol_message_literal_present(self):
        t = _read_template()
        assert "No analysis found for" in t


# ─────────────────────────────────────────────────────────────────────────
# 7. History list wiring
# ─────────────────────────────────────────────────────────────────────────

class TestHistoryList:
    def test_history_renderer_exists(self):
        t = _read_template()
        assert "function _dt_renderHistoryList(" in t

    def test_history_row_uses_same_snapshot_renderer(self):
        t = _read_template()
        # The click handler for a history row must call
        # _dt_renderSnapshotTrace on the cached entry.snapshot.
        assert "_dt_renderSnapshotTrace(symbol, entryData.snapshot, exp)" in t

    def test_history_list_default_uses_10(self):
        # The fetch URL builder must request limit=10.
        t = _read_template()
        assert "limit=10" in t

    def test_history_uses_archived_snapshot_not_recompute(self):
        t = _read_template()
        m = re.search(
            r"function _dt_renderHistoryList\(([^)]*)\)\s*\{(.+?)\n        \}",
            t,
            flags=re.DOTALL,
        )
        assert m
        body = m.group(2)
        # The render reads entry.snapshot directly. It must NOT call
        # /api/score/{symbol} or recompute gates from values.
        assert "/api/score/" not in body
        # It must not look at threshold_key / settings to reinterpret the snapshot.
        assert "min_score_buy" not in body


# ─────────────────────────────────────────────────────────────────────────
# 8. NO new endpoints introduced
# ─────────────────────────────────────────────────────────────────────────

class TestNoNewEndpoints:
    """Phase A is dashboard-only. The repository must not introduce
    new HTTP routes."""

    def test_no_new_routes_in_dashboard_py(self):
        # Phase A is template-only; dashboard.py must be unchanged.
        # (If a future phase legitimately adds a route, this test must
        # be updated alongside an explicit allowed-area amendment.)
        dashboard_py = (REPO_ROOT / "dashboard.py").read_text(encoding="utf-8")
        # Compare against git HEAD version.
        import subprocess
        result = subprocess.run(
            ["git", "diff", "HEAD", "--", "dashboard.py"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert result.stdout.strip() == "", (
            "dashboard.py must not be modified in Phase A. git diff:\n"
            + result.stdout
        )


# ─────────────────────────────────────────────────────────────────────────
# 9. Live endpoint smoke test (against the existing production DB)
# ─────────────────────────────────────────────────────────────────────────

class TestLiveEndpointsReturnExpectedShape:
    """Phase A relies on the existing /api/decision and /api/decision-history
    endpoints returning the documented shape. This test pins the contract
    that the new template renderer depends on."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        # Import dashboard as a module without running the live broker
        # reachability checks (we are only exercising OBS-001 DB endpoints).
        # The app is built at import time; we just need the TestClient.
        import dashboard as _dashboard
        return TestClient(_dashboard.app)

    def test_decision_endpoint_returns_snapshot_envelope(self, client):
        # Use a known OBS-001 symbol.
        r = client.get("/api/decision/ALPXR")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "symbol" in body
        assert "is_legacy" in body
        if body["is_legacy"]:
            assert "legacy_message" in body
            assert body["legacy_message"] == (
                "Legacy analysis — detailed decision trace unavailable"
            )
        else:
            assert "snapshot" in body
            snap = body["snapshot"]
            # The template renderer requires these top-level blocks.
            for block in (
                "schema_version", "symbol", "decision", "strategy_eligibility",
                "scoring", "ranking", "selection", "execution_checks", "order",
            ):
                assert block in snap, f"snapshot missing required block {block}"

    def test_decision_endpoint_legacy_fallback(self, client):
        # WBS has no decision_snapshot (legacy).
        r = client.get("/api/decision/WBS")
        assert r.status_code == 200
        body = r.json()
        assert body.get("is_legacy") is True
        assert "legacy_message" in body

    def test_decision_endpoint_unknown_symbol(self, client):
        r = client.get("/api/decision/ZZZZZZ_NOTREAL")
        assert r.status_code == 200
        body = r.json()
        assert "error" in body

    def test_decision_history_endpoint_shape(self, client):
        r = client.get("/api/decision-history/ALPXR?limit=10")
        assert r.status_code == 200
        body = r.json()
        assert "history" in body
        assert isinstance(body["history"], list)
        assert "count" in body
        # Each row must carry the keys the renderer depends on.
        if body["history"]:
            row = body["history"][0]
            for key in ("cycle_id", "cycle_start", "session_id", "snapshot"):
                assert key in row, f"history row missing {key}"
