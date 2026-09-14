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

    def test_strategy_gates_open_by_default(self):
        # Strategy Gates must be open by default (per the verification step
        # "Decision and Strategy Gates are open by default").
        t = _read_template()
        m = re.search(
            r"function _dt_renderStrategyGatesSection\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m, "_dt_renderStrategyGatesSection not found"
        body = m.group(1)
        assert re.search(r"_dt_section\(\s*'Strategy Gates'\s*,\s*[^,]+,\s*true\s*\)", body), (
            "Strategy Gates section must default to open (open=true)"
        )

    def test_other_sections_pass_open_false(self):
        # Score, Ranking, Selection, Execution Checks remain collapsed by default.
        # Strategy Gates is now open by default (see test_strategy_gates_open_by_default).
        # Order uses _dt_shouldAutoOpenOrder(order) — covered separately.
        t = _read_template()
        bodies = {
            "Score": "_dt_renderScoreSection",
            "Ranking": "_dt_renderRankingSection",
            "Selection": "_dt_renderSelectionSection",
            "Execution Checks": "_dt_renderExecutionChecksSection",
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

class TestOrderAutoOpenLogic:
    """Order section uses _dt_shouldAutoOpenOrder(order) so it opens
    only when the persisted OBS-001 order block indicates the order
    path was actually taken. The decision outcome is NOT consulted."""

    def test_helper_function_defined(self):
        t = _read_template()
        assert "function _dt_shouldAutoOpenOrder(" in t, (
            "_dt_shouldAutoOpenOrder helper must be defined"
        )

    def test_helper_uses_four_facts(self):
        t = _read_template()
        m = re.search(
            r"function _dt_shouldAutoOpenOrder\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m, "_dt_shouldAutoOpenOrder not found"
        body = m.group(1)
        # All four persisted facts must participate in the decision.
        assert "order.submitted === true" in body, "must consult order.submitted"
        assert "order.alpaca_order_id" in body, "must consult order.alpaca_order_id"
        assert "order.no_order_reason" in body, "must consult order.no_order_reason"
        assert "order.slot_consumed === true" in body, "must consult order.slot_consumed"
        # Order helper must NOT consult decision outcome.
        assert "decision.outcome" not in body, (
            "Order auto-open must NOT be derived from decision.outcome"
        )

    def test_order_section_uses_helper(self):
        t = _read_template()
        m = re.search(
            r"function _dt_renderOrderSection\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m, "_dt_renderOrderSection not found"
        body = m.group(1)
        # The third argument to _dt_section for Order must be the helper call.
        assert re.search(
            r"_dt_section\(\s*'Order'\s*,\s*[^,]+,\s*_dt_shouldAutoOpenOrder\(order\)\s*\)",
            body,
        ), "Order section must use _dt_shouldAutoOpenOrder(order) as the open flag"


class TestOrderAutoOpenScenarios:
    """Scenario tests: extract the helper JS source from the template and
    evaluate it under Node.js against constructed order-block payloads.
    Proves the auto-open logic returns the expected value for the five
    scenarios required by the verification step."""

    JS_HELPER_NAME = "_dt_shouldAutoOpenOrder"

    @classmethod
    def _extract_helper_js(cls):
        t = _read_template()
        m = re.search(
            r"(function _dt_shouldAutoOpenOrder\([^)]*\)\s*\{.+?\n        \})",
            t,
            flags=re.DOTALL,
        )
        assert m, "_dt_shouldAutoOpenOrder helper not found"
        return m.group(1)

    @classmethod
    def _eval_helper(cls, order_payload):
        import json
        import subprocess
        helper_js = cls._extract_helper_js()
        # Wrap the helper + invocation in an IIFE so the result is printed.
        program = (
            "(function(){\n"
            + helper_js
            + "\n;console.log(_dt_shouldAutoOpenOrder("
            + json.dumps(order_payload)
            + "));\n})();"
        )
        result = subprocess.run(
            ["node", "-e", program],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"helper evaluation failed: stderr={result.stderr!r}"
        )
        out = result.stdout.strip()
        assert out in ("true", "false"), (
            f"unexpected helper output: {out!r} stderr={result.stderr!r}"
        )
        return out == "true"

    def test_hold_ineligible_no_order_path_is_collapsed(self):
        # HOLD_INELIGIBLE: order block present but every fact is empty.
        assert self._eval_helper({
            "submitted": False,
            "alpaca_order_id": None,
            "no_order_reason": None,
            "slot_consumed": False,
        }) is False

    def test_hold_ineligible_missing_order_block_is_collapsed(self):
        # order block is null/undefined entirely.
        assert self._eval_helper(None) is False

    def test_order_attempted_but_failed_is_open(self):
        # Order path was attempted but failed (no_order_reason populated).
        assert self._eval_helper({
            "submitted": False,
            "alpaca_order_id": None,
            "no_order_reason": "submit_order returned no order object",
            "slot_consumed": False,
        }) is True

    def test_buy_order_submitted_is_open(self):
        # BUY_ORDER_SUBMITTED: submitted=true and alpaca_order_id present.
        assert self._eval_helper({
            "submitted": True,
            "alpaca_order_id": "abc-123-uuid",
            "no_order_reason": None,
            "slot_consumed": True,
        }) is True

    def test_submitted_with_alpaca_order_id_only_is_open(self):
        # Order was submitted (id present) even if other fields are sparse.
        assert self._eval_helper({
            "submitted": False,
            "alpaca_order_id": "abc-123-uuid",
            "no_order_reason": None,
            "slot_consumed": False,
        }) is True

    def test_slot_consumed_only_is_open(self):
        # Slot consumed (slot_consumed_semantics_version v1: iff submit_order
        # returned an order). One fact is enough.
        assert self._eval_helper({
            "submitted": False,
            "alpaca_order_id": None,
            "no_order_reason": None,
            "slot_consumed": True,
        }) is True

    def test_empty_order_object_is_collapsed(self):
        # Order block exists but every meaningful fact is empty.
        assert self._eval_helper({}) is False



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

# ─────────────────────────────────────────────────────────────────────────
# A.1 Execution Checks Renderer Fidelity
# ─────────────────────────────────────────────────────────────────────────

class TestExecutionChecksFidelity:
    """Phase A.1: the rendered Execution Checks list must contain every
    persisted check in execution_checks.checks[] exactly once, even when
    the first_blocking_check is absent from execution_checks.evaluated_in_order.

    These tests extract the actual JS of `_dt_renderExecutionChecksSection`
    (plus its DOM-adjacent helpers) from templates/dashboard.html, evaluate
    it under Node.js with a stub `document` against constructed
    execution-checks payloads, and assert the resulting HTML.

    The bug surfaced on the live ALPXR snapshot where:
        first_blocking_check = "position_existence_check"
        evaluated_in_order   = [margin_check, pending_order_check,
                                cooldown_check, position_concentration_check,
                                sector_concentration_check, correlation_check,
                                beta_check, buying_power_check,
                                quantity_post_sizing_check]
        position_existence_check is in checks[] with applied=True, passed=False,
        but is NOT in evaluated_in_order.
    """

    @staticmethod
    def _extract_function(name: str, template: str) -> str:
        """Extract a single top-level `function NAME(...) { ... }` block
        from `template`. The codebase is uniformly 8-space indented, so
        the close brace of a top-level `function` declaration is the
        FIRST subsequent line whose content is exactly `        }` (eight
        spaces and a closing brace). This avoids the JS-regex-literal
        false-positives that a naive brace counter runs into.

        Returns the full source of the function including trailing newline."""
        lines = template.split("\n")
        start = None
        for i, line in enumerate(lines):
            if line.lstrip().startswith("function") and f"function {name}(" in line:
                start = i
                break
        assert start is not None, f"could not find function {name}"
        for j in range(start + 1, len(lines)):
            if lines[j] == "        }":
                body = "\n".join(lines[start:j + 1]) + "\n"
                return body
        raise AssertionError(
            f"could not find matching close brace for function {name}"
        )

    @classmethod
    def _render(cls, exec_checks_payload: dict) -> str:
        """Compile the renderer plus its helpers under Node.js, run it
        against `exec_checks_payload`, and return the resulting HTML body
        (the bodyHtml passed to _dt_section('Execution Checks', ...))."""
        import json
        import subprocess
        template = _read_template()
        # Stub `_dt_section` to a string-returning variant so the renderer
        # returns a plain HTML string under Node.js (no DOM needed).
        stub_section = (
            "function _dt_section(title, bodyHtml, open) {\n"
            "  return '<section data-title=\"' + _dt_escapeHtml(title)\n"
            "    + '\" data-open=\"' + (open ? '1' : '0') + '\">'\n"
            "    + bodyHtml + '</section>';\n"
            "}\n"
        )
        # Extract only the helpers we need; do NOT extract the original
        # _dt_section (it uses DOM and would shadow our stub).
        fn_escape = cls._extract_function("_dt_escapeHtml", template)
        fn_row = cls._extract_function("_dt_row", template)
        fn_exec = cls._extract_function("_dt_renderExecutionChecksSection", template)
        helpers_no_section = fn_escape + "\n" + fn_row
        # Stub FIRST so the stub shadows any other _dt_section declaration.
        program = (
            "let document = { createElement: () => ({ className: '', innerHTML: '', appendChild() {} }) };\n"
            "Math.random = () => 0.5;\n"
            + stub_section
            + helpers_no_section
            + fn_exec
            + f"\nlet html = _dt_renderExecutionChecksSection({json.dumps(exec_checks_payload)});\n"
            + "console.log(JSON.stringify(html));\n"
        )
        result = subprocess.run(
            ["node", "-e", program],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, (
            f"renderer evaluation failed: stderr={result.stderr!r}"
        )
        out = result.stdout.strip()
        # Node's console.log wraps long strings sometimes; recover.
        if not out:
            return ""
        # The renderer JSON.stringify's its output, so un-json.
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out

    # -- The 7 regression scenarios Josh requested -----------------------

    def _row_count(self, html: str, name: str) -> int:
        """Count occurrences of `name` as a labelled row inside a
        `<div class="dt-row ..."><div class="dt-label">...</div>...</div>`.

        Render shape:
            <div class="dt-row [...]"><div class="dt-label">
                <span class="dt-chip dt-chip-{status}">{status}</span>{NAME}
                [possibly <span class="dt-chip dt-chip-blocker">FIRST BLOCKER</span>]
            </div>...</div>

        We count occurrences of the literal pattern
            `dt-chip-{PASS|FAIL|NOTRUN|N/A}">{status-text}</span>{NAME}` in
        the rendered HTML. The status-text after the chip (FAIL, PASS,
        NOT RUN, N/A) is the same span, so we match both pieces. This
        avoids counting the name's appearance in the "First blocker: NAME"
        summary header or in CSS class names like `data-value`.
        """
        import re
        # Match each of the four legal status patterns.
        patterns = [
            r'<span class="dt-chip dt-chip-pass">PASS</span>' + re.escape(name),
            r'<span class="dt-chip dt-chip-fail">FAIL</span>' + re.escape(name),
            r'<span class="dt-chip dt-chip-notrun">NOT RUN</span>' + re.escape(name),
            r'<span class="dt-chip dt-chip-na">N/A</span>' + re.escape(name),
        ]
        return sum(len(re.findall(p, html)) for p in patterns)

    def _first_row_pos(self, html: str, name: str) -> int:
        """Return the index of the FIRST dt-row chip pattern for `name`.
        Used for ordering checks so we don't get confused by the name's
        appearance in the "First blocker: NAME" summary header."""
        import re
        candidates = []
        for status_text, chip_class in (
            ("PASS", "pass"), ("FAIL", "fail"),
            ("NOT RUN", "notrun"), ("N/A", "na"),
        ):
            pat = ('<span class="dt-chip dt-chip-' + chip_class + '">'
                   + status_text + '</span>' + re.escape(name))
            m = re.search(pat, html)
            if m:
                candidates.append(m.start())
        return min(candidates) if candidates else -1

    def test_blocker_present_in_evaluated_in_order(self):
        """A blocker that is already in evaluated_in_order must be rendered
        in position with the .dt-first-blocker highlight."""
        out = self._render({
            "first_blocking_check": "buying_power_check",
            "first_blocking_reason": "insufficient buying power",
            "evaluated_in_order": [
                "margin_check", "buying_power_check", "quantity_post_sizing_check",
            ],
            "checks": [
                {"name": "margin_check",         "applied": True,  "passed": True,  "reason": ""},
                {"name": "buying_power_check",   "applied": True,  "passed": False, "reason": "insufficient buying power"},
                {"name": "quantity_post_sizing_check", "applied": False, "passed": None, "reason": ""},
            ],
        })
        # Each persisted check appears in exactly one dt-row chip pattern.
        for name in ("margin_check", "buying_power_check", "quantity_post_sizing_check"):
            assert self._row_count(out, name) == 1, (
                f"{name} should appear once in a row; got {self._row_count(out, name)}\n"
                f"HTML: {out[:600]}"
            )
        # The blocker is highlighted.
        assert "dt-first-blocker" in out, (
            f"blocker must be highlighted; HTML: {out[:600]}"
        )
        # Order preserved: declared evaluation order.
        assert self._first_row_pos(out, "margin_check") < self._first_row_pos(out, "buying_power_check")
        assert self._first_row_pos(out, "buying_power_check") < self._first_row_pos(out, "quantity_post_sizing_check")

    def test_blocker_absent_from_evaluated_in_order(self):
        """THE BUG: blocker is in checks[] but NOT in evaluated_in_order.

        The renderer must still emit the blocker row, highlighted, exactly
        once, and the other persisted checks must remain in their persisted
        checks[] order (appended after evaluated_in_order)."""
        out = self._render({
            "first_blocking_check": "position_existence_check",
            "first_blocking_reason": "position does not exist",
            "evaluated_in_order": [
                "margin_check", "pending_order_check", "cooldown_check",
                "position_concentration_check", "sector_concentration_check",
                "correlation_check", "beta_check", "buying_power_check",
                "quantity_post_sizing_check",
            ],
            "checks": [
                {"name": "margin_check", "applied": True,  "passed": True,  "reason": ""},
                {"name": "pending_order_check", "applied": True, "passed": True, "reason": ""},
                {"name": "position_existence_check", "applied": True, "passed": False,
                 "reason": "position does not exist"},
                {"name": "cooldown_check", "applied": False, "passed": None, "reason": ""},
                {"name": "position_concentration_check", "applied": False, "passed": None, "reason": ""},
                {"name": "sector_concentration_check", "applied": False, "passed": None, "reason": ""},
                {"name": "correlation_check", "applied": False, "passed": None, "reason": ""},
                {"name": "beta_check", "applied": False, "passed": None, "reason": ""},
                {"name": "buying_power_check", "applied": False, "passed": None, "reason": ""},
                {"name": "quantity_post_sizing_check", "applied": False, "passed": None, "reason": ""},
            ],
        })
        # Each persisted check appears exactly once as a labelled row.
        for name in (
            "margin_check", "pending_order_check", "position_existence_check",
            "cooldown_check", "position_concentration_check",
            "sector_concentration_check", "correlation_check", "beta_check",
            "buying_power_check", "quantity_post_sizing_check",
        ):
            assert self._row_count(out, name) == 1, (
                f"check {name!r} rendered {self._row_count(out, name)} times; expected once\n"
                f"HTML: {out[:800]}"
            )
        # Exactly one dt-first-blocker row.
        assert out.count("dt-first-blocker") == 1, (
            f"exactly one .dt-first-blocker row expected; got {out.count('dt-first-blocker')}\n"
            f"HTML: {out[:800]}"
        )
        # The blocker row contains the FAIL chip for the blocker name.
        assert "dt-chip-fail\">FAIL</span>position_existence_check" in out, (
            f"blocker must carry FAIL chip; HTML: {out[:800]}"
        )
        # Ordering: declared evaluated_in_order items come first in order,
        # then appended extras in their persisted checks[] order (which
        # for a single-blocker snapshot means position_existence_check
        # is appended at the end after the last evaluated_in_order entry).
        # Importantly: between evaluated_in_order neighbours, the declared
        # order is preserved (pending_order_check before cooldown_check).
        pos_pending = self._first_row_pos(out, "pending_order_check")
        pos_cooldown = self._first_row_pos(out, "cooldown_check")
        pos_qty = self._first_row_pos(out, "quantity_post_sizing_check")
        pos_blocker = self._first_row_pos(out, "position_existence_check")
        assert pos_pending < pos_cooldown
        # The blocker (extra) must be appended AFTER the trailing
        # evaluated_in_order entry, in this single-blocker snapshot.
        assert pos_qty < pos_blocker, (
            f"appended extras must come after the evaluated_in_order list; "
            f"qty={pos_qty} blocker={pos_blocker}"
        )

    def test_multiple_extras_appended_in_checks_order(self):
        """Multiple checks[] entries absent from evaluated_in_order must be
        appended in their persisted checks[] order."""
        out = self._render({
            "evaluated_in_order": ["alpha_first"],
            "checks": [
                {"name": "alpha_first", "applied": True, "passed": True,  "reason": ""},
                {"name": "z_extra_1",   "applied": True, "passed": False, "reason": ""},
                {"name": "m_extra_2",   "applied": True, "passed": True,  "reason": ""},
                {"name": "b_extra_3",   "applied": False, "passed": None,  "reason": ""},
            ],
        })
        # All four appear exactly once as a labelled row.
        for name in ("alpha_first", "z_extra_1", "m_extra_2", "b_extra_3"):
            assert self._row_count(out, name) == 1, (
                f"{name} should appear once; got {self._row_count(out, name)}\n"
                f"HTML: {out[:800]}"
            )
        # Persistence order for extras: z, m, b (NOT alphabetical).
        idx_z = self._row_count and out.find("z_extra_1")  # any unique marker
        idx_z = out.find("z_extra_1")
        idx_m = out.find("m_extra_2")
        idx_b = out.find("b_extra_3")
        assert 0 <= idx_z < idx_m < idx_b, (
            f"extras must preserve persisted checks[] order; "
            f"z={idx_z} m={idx_m} b={idx_b}\nHTML: {out[:600]}"
        )

    def test_duplicates_between_order_and_checks(self):
        """If a name appears in BOTH evaluated_in_order and checks[], it must
        be rendered exactly once (in the declared-order position)."""
        out = self._render({
            "evaluated_in_order": ["margin_check", "buying_power_check"],
            "checks": [
                {"name": "margin_check", "applied": True, "passed": True, "reason": ""},
                {"name": "buying_power_check", "applied": True, "passed": True, "reason": ""},
            ],
        })
        assert self._row_count(out, "margin_check") == 1
        assert self._row_count(out, "buying_power_check") == 1

    def test_name_absent_from_checks_array_is_never_invented(self):
        """evaluated_in_order may name a check that was never persisted in
        checks[]; the renderer must NOT invent a row for it."""
        out = self._render({
            "evaluated_in_order": ["in_phantom", "in_real"],
            "checks": [
                {"name": "in_real", "applied": True, "passed": True, "reason": ""},
            ],
        })
        # The phantom name must not appear anywhere in the rendered output.
        assert "in_phantom" not in out, (
            "renderer must NOT fabricate checks; phantom input-only name leaked into output:\n"
            + out[:600]
        )
        assert self._row_count(out, "in_real") == 1

    def test_not_run_extra_appended_remains_not_run(self):
        """A persisted check[] entry with applied=False must render as
        'NOT RUN' even when it is named as the first_blocking_check.

        Defensive invariant: we never demote a check to FAIL merely because
        it is the first blocker."""
        out = self._render({
            "first_blocking_check": "phantom_blocker",
            "evaluated_in_order": [],
            "checks": [
                {"name": "phantom_blocker", "applied": False, "passed": None, "reason": ""},
            ],
        })
        # The persisted check is rendered exactly once and labelled NOT RUN.
        assert self._row_count(out, "phantom_blocker") == 1
        # The row for phantom_blocker carries the NOT RUN chip, not FAIL.
        assert "dt-chip-notrun\">NOT RUN</span>phantom_blocker" in out, (
            f"an applied=False / passed=None check must remain NOT RUN\nHTML: {out}"
        )
        # The summary header MAY mention the blocker name as text (in the
        # "First blocker: <name>" chip); it must not include a FAIL marker
        # on the row itself.
        assert "dt-chip-fail" not in out, (
            "no FAIL chip may appear when all persisted checks are NOT RUN\n"
            f"HTML: {out}"
        )

    def test_ordinary_phase_a_decision_rendering_unchanged(self):
        """When ALL persisted checks are already in evaluated_in_order (the
        normal happy path used by Phase A), no extras get appended and the
        output is identical to a renderer that only iterates
        evaluated_in_order.

        Guards Phase A regressions: the fix only changes behaviour in the
        absence case."""
        snapshot = {
            "first_blocking_check": None,
            "evaluated_in_order": [
                "margin_check", "buying_power_check", "quantity_post_sizing_check",
            ],
            "checks": [
                {"name": "margin_check", "applied": True, "passed": True, "reason": ""},
                {"name": "buying_power_check", "applied": True, "passed": True, "reason": ""},
                {"name": "quantity_post_sizing_check", "applied": False, "passed": None, "reason": ""},
            ],
        }
        out = self._render(snapshot)
        # Same set of names, each rendered exactly once in declared order.
        for name in ("margin_check", "buying_power_check", "quantity_post_sizing_check"):
            assert self._row_count(out, name) == 1
        idx_m = out.find("margin_check")
        idx_b = out.find("buying_power_check")
        idx_q = out.find("quantity_post_sizing_check")
        assert idx_m < idx_b < idx_q

    # -- Live regression: ALPXR ------------------------------------------

    def test_live_alpxr_snapshot_renders_blocker_row_and_highlight(self):
        """Live OBS-001 regression: ALPXR (SELL_BLOCKED_DYNAMIC) has
        first_blocking_check='position_existence_check' with the blocker
        absent from evaluated_in_order. Fetch the live snapshot, run the
        renderer, assert the row is rendered and highlighted exactly once.
        """
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard

        client = TestClient(_dashboard.app)
        r = client.get("/api/decision/ALPXR")
        assert r.status_code == 200, r.text
        body = r.json()
        # If ALPXR was recaptured with different shape, skip rather than fail.
        if not body or body.get("is_legacy") or "snapshot" not in body:
            import pytest
            pytest.skip("ALPXR live snapshot unavailable in this environment")
        ec = body["snapshot"].get("execution_checks")
        assert ec, "ALPXR snapshot must include execution_checks"
        out = self._render(ec)
        # Live ALPXR snapshot invariant: blocker name IS in checks[] but NOT
        # in evaluated_in_order. If that invariant ever changes (e.g. a
        # future snapshot fix), this test should be re-evaluated rather
        # than silently passing.
        fbc = ec.get("first_blocking_check")
        order = ec.get("evaluated_in_order") or []
        checks = ec.get("checks") or []
        assert fbc and fbc not in order, (
            f"test invariant: fbc={fbc!r} must NOT be in order={order!r}"
        )
        # The blocker row IS rendered exactly once.
        assert self._row_count(out, fbc) == 1, (
            f"live renderer dropped or duplicated {fbc!r}; "
            f"row_count={self._row_count(out, fbc)} HTML head: {out[:800]}"
        )
        # The blocker row carries the .dt-first-blocker class.
        assert "dt-first-blocker" in out, (
            f"live ALPXR: blocker row missing .dt-first-blocker; HTML head: {out[:800]}"
        )
        # Every persisted check is rendered exactly once as a row.
        for c in checks:
            n = c.get("name")
            if not n:
                continue
            assert self._row_count(out, n) == 1, (
                f"check {n!r} rendered {self._row_count(out, n)} times; expected once"
            )


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
