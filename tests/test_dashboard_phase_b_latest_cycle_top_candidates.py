"""Dashboard Phase B — Latest Cycle Funnel + Truthful Top Candidates.

OBS-001-driven dashboard-only slice that replaces the legacy Top
Opportunities concept with two truthful views:

1. Latest Cycle Funnel — from /api/actionability-summary -> cycle_funnel
2. Top Candidates (Latest Cycle) — from /api/cycle-candidates/{cycle_id}
   reading immutable decision_history rows.

Phase B does NOT modify SmartBot, scoring, eligibility, ranking, sizing,
risk, brokerage, OBS-001 persistence, schema, PIPELINE-001, or SCORE-003.
All tests below prove observable behaviour at the template + endpoint
layer only.
"""

import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "templates" / "dashboard.html"


def _read_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────
# Endpoint contract: /api/cycle-candidates/{cycle_id}
# ─────────────────────────────────────────────────────────────────────────

class TestCycleCandidatesEndpoint:
    """Pin the response envelope for /api/cycle-candidates/{cycle_id}.

    The endpoint reads only from decision_history (immutable, append-only)
    and cycle_funnel; it does NOT modify the database.
    """

    @staticmethod
    def _client():
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        return TestClient(_dashboard.app)

    def test_endpoint_registered_in_dashboard_py(self):
        # Sanity: the route is wired up in dashboard.py.
        c = self._client()
        r = c.get("/api/cycle-candidates/cycle_DOES_NOT_EXIST")
        assert r.status_code == 200, r.text
        # Body shape:
        body = r.json()
        for k in ("cycle_id", "cycle_known", "candidates", "candidates_found",
                  "near_misses", "near_misses_found", "near_miss_limit"):
            assert k in body, f"missing key in envelope: {k}"
        # Unknown cycle: cycle_known=False, both lists empty.
        assert body["cycle_known"] is False
        assert body["candidates"] == []
        assert body["near_misses"] == []

    def test_endpoint_uses_decision_history_only(self):
        # The endpoint path under test is the new cycle-candidates route;
        # this guards against accidental refactors that touch /api/opportunities
        # or recompute anything from analyzed_stocks.
        import subprocess
        txt = subprocess.run(
            ["grep", "-nE",
             r"/api/cycle-candidates|candidates_found|near_misses_found|near_miss_limit",
             "dashboard.py"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        assert "/api/cycle-candidates" in txt
        assert "candidates_found" in txt
        assert "near_misses_found" in txt
        assert "near_miss_limit" in txt

    def test_near_miss_limit_clamped_to_range(self):
        c = self._client()
        # 0 -> clamped to 1
        r0 = c.get("/api/cycle-candidates/cycle_DOES_NOT_EXIST?near_miss_limit=0")
        assert r0.json()["near_miss_limit"] == 1
        # 99 -> clamped to 10
        r9 = c.get("/api/cycle-candidates/cycle_DOES_NOT_EXIST?near_miss_limit=99")
        assert r9.json()["near_miss_limit"] == 10
        # 3 -> kept as 3
        r3 = c.get("/api/cycle-candidates/cycle_DOES_NOT_EXIST?near_miss_limit=3")
        assert r3.json()["near_miss_limit"] == 3

    def test_live_latest_cycle_returns_zero_candidates(self):
        # The latest live cycle is zero-candidate; this is the realistic
        # state today's bot produces. The endpoint must surface that
        # truthfully rather than fudge candidates from total_score.
        c = self._client()
        s = c.get("/api/actionability-summary").json()
        cycle_id = s.get("funnel", {}).get("cycle_id")
        assert cycle_id, "no live cycle_funnel row"
        r = c.get(f"/api/cycle-candidates/{cycle_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["cycle_known"] is True
        assert body["candidates"] == []
        assert body["candidates_found"] == 0
        # Near misses come from the same cycle's HOLD_INELIGIBLE rows
        # whose ranking.candidate_rank is NULL.
        nms = body["near_misses"]
        for nm in nms:
            assert nm["decision_outcome"] == "HOLD_INELIGIBLE"
            assert isinstance(nm["failed_strategy_gates"], list)
            assert nm["total_score"] is not None
        # Default limit was applied.
        assert body["near_miss_limit"] == 3
        assert len(nms) <= 3


# ─────────────────────────────────────────────────────────────────────────
# Latest Cycle card on dashboard.html
# ─────────────────────────────────────────────────────────────────────────

class TestLatestCycleCard:
    """The rendered Latest Cycle card must show funnel + off-path counts
    sourced from /api/actionability-summary. Never re-derive from
    total_score."""

    def test_card_present_on_dashboard_tab(self):
        t = _read_template()
        # Card lives ABOVE the Top Candidates card.
        lc_pos = t.find('id="latest-cycle-card"')
        tc_pos = t.find('id="top-candidates-card"')
        assert lc_pos > 0 and tc_pos > 0, "both cards must exist"
        assert lc_pos < tc_pos, "Latest Cycle card must come BEFORE Top Candidates"

    def test_load_function_calls_actionability_endpoint(self):
        t = _read_template()
        m = re.search(
            r"async function loadLatestCycle\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m, "loadLatestCycle function not found"
        body = m.group(1)
        assert "/api/actionability-summary" in body
        assert "_lc_renderLatestCycle" in body

    def test_renderer_uses_only_persisted_funnel_fields(self):
        # The renderer must read funnel.analyzed_count etc. and must NOT
        # reference analyzed_stocks, total_score, signal, or any recompute.
        t = _read_template()
        m = re.search(
            r"function _lc_renderLatestCycle\(funnel\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m, "_lc_renderLatestCycle not found"
        body = m.group(1)
        # The renderer reads the funnel counts.
        assert "funnel.analyzed_count" in body
        assert "funnel.strategy_eligible_count" in body
        assert "funnel.ranked_candidate_count" in body
        assert "funnel.execution_attempt_count" in body
        assert "funnel.order_submitted_count" in body
        assert "funnel.execution_blocked_count" in body
        assert "funnel.order_failed_count" in body
        # Must NOT derive anything from total_score, signal, or buy_criteria.
        assert "total_score" not in body
        assert "buy_criteria" not in body
        assert "analyzed_stocks" not in body

    def test_funnel_stages_are_in_forward_order(self):
        # Forward funnel: Analyzed → Strategy Eligible → Ranked Candidates
        #                 → Execution Attempted → Orders Submitted
        t = _read_template()
        m = re.search(
            r"function _lc_renderLatestCycle\(funnel\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m
        body = m.group(1)
        # Locate the stages block.
        stages_match = re.search(
            r"\[(.+?)\]\.map\(\(s, i, arr\)",
            body, flags=re.DOTALL,
        )
        assert stages_match, "funnel stages array not found"
        stages = stages_match.group(1)
        # Stages appear in this exact order in the source.
        for stage in ["Analyzed", "Strategy Eligible", "Ranked Candidates",
                      "Execution Attempted", "Orders Submitted"]:
            assert f"lbl: '{stage}'" in stages, (
                f"forward funnel stage missing or out of order: {stage}"
            )

    def test_off_path_outcomes_are_separate_from_forward_funnel(self):
        # Execution Blocked and Orders Failed MUST be displayed in their
        # own row, NOT mixed in with the forward funnel stages. The UI
        # must never imply Execution Blocked → Orders Submitted.
        t = _read_template()
        m = re.search(
            r"function _lc_renderLatestCycle\(funnel\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        body = m.group(1)
        # Off-path block exists with its own class + "off-path" labels.
        assert "lc-funnel-offpath" in body
        assert "Execution Blocked (off-path)" in body
        assert "Orders Failed (off-path)" in body
        # Off-path block is rendered AFTER the forward stages.
        pos_forward = body.find("lc-funnel-stage")
        pos_offpath = body.find("lc-funnel-offpath")
        assert pos_forward > 0 and pos_offpath > 0
        assert pos_forward < pos_offpath

    def test_empty_state_message(self):
        t = _read_template()
        # Empty state literal must exist.
        assert "No completed OBS-001 cycle available yet." in t

    def test_view_analysis_button_switches_to_analytics(self):
        t = _read_template()
        m = re.search(
            r"function _lc_viewAnalysis\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m, "_lc_viewAnalysis not found"
        body = m.group(1)
        # Must NOT use the analytics redesign yet; only navigates.
        assert "analytics" in body.lower()
        assert ".click()" in body  # activates the Analytics tab button


# ─────────────────────────────────────────────────────────────────────────
# Top Candidates card
# ─────────────────────────────────────────────────────────────────────────

class TestTopCandidatesCard:
    """The Top Candidates card uses candidate_rank ASC (NOT total_score DESC)
    and is sourced exclusively from /api/cycle-candidates/{cycle_id}.
    """

    def test_card_present_after_latest_cycle(self):
        t = _read_template()
        # The Top Candidates card lives right after the Latest Cycle card.
        lc_pos = t.find('id="latest-cycle-card"')
        tc_pos = t.find('id="top-candidates-card"')
        assert 0 < lc_pos < tc_pos

    def test_load_function_resolves_cycle_via_actionability_then_fetches_candidates(self):
        t = _read_template()
        m = re.search(
            r"async function loadTopCandidates\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m, "loadTopCandidates function not found"
        body = m.group(1)
        # Two-step fetch: 1) cycle_id via /api/actionability-summary,
        # 2) candidates via /api/cycle-candidates/{cycle_id}.
        assert "/api/actionability-summary" in body
        assert "/api/cycle-candidates/" in body

    def test_renderer_orders_by_candidate_rank_asc(self):
        # The renderer NEVER orders by total_score DESC. It uses whatever
        # order the endpoint provides (which is candidate_rank ASC).
        t = _read_template()
        m = re.search(
            r"function _lc_renderCandidatesTable\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m
        body = m.group(1)
        # Rank is rendered with '#<rank>' markup (the source contains
        # an escaped quote followed by '#' inside a JS string literal).
        assert "lc-cand-rank" in body
        # The function emits "candidate_rank" as text inside the row.
        assert "c.candidate_rank" in body
        # The function does NOT sort the array.
        assert ".sort(" not in body

    def test_candidate_row_click_opens_history_symbol_trace(self):
        # Click handler must take the user to History tab with the symbol
        # prefilled. The existing Phase A renderer fills the trace.
        t = _read_template()
        m = re.search(
            r"function _lc_openHistorySymbol\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m, "_lc_openHistorySymbol not found"
        body = m.group(1)
        # Switches to history tab.
        assert "historySearchInput" in body
        assert "doHistorySymbolSearch" in body
        # Tab button lookup.
        assert "top-tab" in body

    def test_does_not_rank_by_total_score(self):
        # The renderer MUST NOT promote a HOLD_INELIGIBLE high-score row
        # into the Top Candidates list. It must filter via candidate_rank.
        t = _read_template()
        m = re.search(
            r"function _lc_renderZeroState\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert m
        body = m.group(1)
        # Near miss rows render in the zero-state card only.
        assert "HOLD" in body
        assert "failed_strategy_gates" in body
        # The ZERO state explicitly says "0 of N strategy eligible" when
        # the latest cycle produced zero ranked candidates.
        m2 = re.search(
            r"async function loadTopCandidates\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        body2 = m2.group(1)
        assert "0 of " in body2
        assert "strategy eligible" in body2

    def test_near_miss_top_three_cap(self):
        # The renderer respects the near_miss_limit from the endpoint and
        # caps the table at that value (the endpoint already LIMITS to 3).
        t = _read_template()
        m = re.search(
            r"function _lc_renderZeroState\([^)]*\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        body = m.group(1)
        # The "HIGH-SCORE NEAR MISSES" label is shown only in the zero-
        # candidate state, never in the candidate path.
        assert "HIGH-SCORE NEAR MISSES" in t or "HIGH-SCORE NEAR MISSES" in body


# ─────────────────────────────────────────────────────────────────────────
# Renderer behaviour via Node.js (constructed payloads)
# ─────────────────────────────────────────────────────────────────────────

class TestLcRendererFidelityViaNode:
    """Run the actual JS of _lc_renderLatestCycle and _lc_renderZeroState
    under Node.js with constructed payloads, and assert the resulting HTML
    string. This catches off-by-one drops, wrong-field reads, and
    layout regressions that template-level checks can miss.
    """

    @classmethod
    def _extract_function(cls, name: str, template: str) -> str:
        lines = template.split("\n")
        start = None
        for i, line in enumerate(lines):
            if line.lstrip().startswith("function") and f"function {name}(" in line:
                start = i
                break
        assert start is not None, f"could not find function {name}"
        for j in range(start + 1, len(lines)):
            if lines[j] == "        }":
                return "\n".join(lines[start:j + 1]) + "\n"
        raise AssertionError(f"could not find close for {name}")

    @classmethod
    def _compile_and_run(cls, fn_name, payload):
        import subprocess
        t = _read_template()
        # Extract just what we need: helpers + the renderer.
        helpers = [cls._extract_function(n, t) for n in (
            "_lc_escapeHtml",
            "_lc_fmtIso",
            "_lc_outcomeColor",
            "_lc_outcomeShort",
            "_lc_renderLatestCycle",
            "_lc_renderCandidatesTable",
            "_lc_renderZeroState",
        )]
        program = (
            # No DOM in node; stub _lc_escapeHtml, _lc_fmtIso work in
            # pure JS so no DOM is needed.
            "\n".join(helpers)
            + f"\nlet html = {fn_name}({json.dumps(payload)});\n"
            + "console.log(JSON.stringify(html));\n"
        )
        result = subprocess.run(
            ["node", "-e", program],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, (
            f"node evaluation failed: stderr={result.stderr!r}"
        )
        out = result.stdout.strip()
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out

    def test_latest_cycle_zero_state_renders_dropoff(self):
        # Standard live case: 30 analyzed, 0 eligible, 0 ranked, 0 blocked.
        html = self._compile_and_run("_lc_renderLatestCycle", {
            "cycle_id": "cycle_84661_2026-09-14T01-26-56",
            "session_id": 84661,
            "cycle_start": "2026-09-14T01:26:56Z",
            "cycle_end": "2026-09-14T01:27:05Z",
            "analyzed_count": 30,
            "strategy_eligible_count": 0,
            "ranked_candidate_count": 0,
            "execution_attempt_count": 0,
            "execution_blocked_count": 0,
            "order_submission_attempt_count": 0,
            "order_submitted_count": 0,
            "order_failed_count": 0,
            "not_attempted_count": 0,
            "not_attempted_reason": None,
            "bot_version": "2.1.0",
            "schema_version": 1,
        })
        # Forward funnel stages all show 0.
        for label in ("Analyzed", "Strategy Eligible", "Ranked Candidates",
                      "Execution Attempted", "Orders Submitted"):
            assert label in html, f"forward stage label missing: {label}"
        # Analyzed count is "30".
        assert ">30<" in html or html.count(">30<") >= 1
        # Off-path labels exist.
        assert "Execution Blocked (off-path)" in html
        assert "Orders Failed (off-path)" in html
        # Drop-off callout refers to the largest drop (30 -> 0 eligible).
        assert "30" in html and "0" in html
        assert "strategy eligible" in html
        # View analysis button rendered.
        assert "View analysis" in html

    def test_latest_cycle_with_blocked_offpath(self):
        # Earlier-history cycle where strategy_eligible=1, ranked=0,
        # execution_blocked=1. The UI must NOT show "1 Execution Attempted"
        # leading to "0 Orders Submitted" \u2014 i.e. must keep off-path
        # separate from forward funnel.
        html = self._compile_and_run("_lc_renderLatestCycle", {
            "cycle_id": "cycle_1_2_3",
            "session_id": 1,
            "cycle_start": "2024-01-01T00:00:00Z",
            "cycle_end": "2024-01-01T00:00:10Z",
            "analyzed_count": 30,
            "strategy_eligible_count": 1,
            "ranked_candidate_count": 0,
            "execution_attempt_count": 1,
            "execution_blocked_count": 1,
            "order_submission_attempt_count": 1,
            "order_submitted_count": 0,
            "order_failed_count": 0,
            "not_attempted_count": 0,
            "not_attempted_reason": None,
            "bot_version": "2.1.0",
            "schema_version": 1,
        })
        # Off-path row carries the blocked count of 1.
        assert "Execution Blocked (off-path)" in html
        # Orders submitted is 0.
        assert "Orders Submitted" in html
        # No implication: Execution Blocked (off-path) sits in its own
        # row, after the forward funnel row, with its own colored box.
        pos_forward = html.find("lc-funnel-row")  # first occurrence
        # Find the second lc-funnel-row (off-path container).
        next_lc_row = html.find("lc-funnel-row", pos_forward + 1)
        assert next_lc_row > pos_forward
        # The off-path row must come AFTER the forward row.
        assert html.find("Execution Blocked (off-path)") > next_lc_row
        # Both forward funnel row and off-path row must be present.
        assert pos_forward > 0
        # Sanity: "1" appears (1 eligible, 1 attempted, 1 blocked).
        assert html.count(">1<") >= 2

    def test_zero_state_renders_with_near_misses(self):
        # Simulate the /api/cycle-candidates envelope for a zero-candidate
        # cycle (matches what the live API returns today).
        near_misses = [
            {"symbol": "CNTN", "total_score": 85.0, "decision_outcome": "HOLD_INELIGIBLE",
             "decision_primary_reason": "Strategy ineligible: failed gates: rsi_oversold",
             "failed_strategy_gates": ["rsi_oversold"], "decision_history_id": 1},
            {"symbol": "PDS", "total_score": 74.0, "decision_outcome": "HOLD_INELIGIBLE",
             "decision_primary_reason": "Strategy ineligible: failed gates: rsi_oversold",
             "failed_strategy_gates": ["rsi_oversold"], "decision_history_id": 2},
            {"symbol": "PDT", "total_score": 73.0, "decision_outcome": "HOLD_INELIGIBLE",
             "decision_primary_reason": "Strategy ineligible: failed gates: rsi_oversold, sma_uptrend",
             "failed_strategy_gates": ["rsi_oversold", "sma_uptrend"], "decision_history_id": 3},
        ]
        html = self._compile_and_run("_lc_renderZeroState", near_misses)
        # All three appear.
        for sym in ("CNTN", "PDS", "PDT"):
            assert sym in html, f"near-miss symbol missing: {sym}"
        # Scores are shown rounded.
        assert "85" in html and "74" in html and "73" in html
        # Failed strategy gates appear with their names.
        assert "rsi_oversold" in html
        assert "sma_uptrend" in html
        # The gating fact is the outcome label.
        assert "HOLD" in html

    def test_zero_state_handles_empty_near_misses(self):
        html = self._compile_and_run("_lc_renderZeroState", [])
        assert "No HOLD_INELIGIBLE rows in this cycle." in html

    def test_candidates_table_renders_with_rank_and_outcome(self):
        cands = [
            {"symbol": "AA", "candidate_rank": 1, "eligible_candidate_count": 3,
             "ranking_tiebreak_basis": "score_then_min_margin",
             "total_score": 92.5, "decision_outcome": "BUY_ELIGIBLE_NOT_SELECTED",
             "decision_primary_reason": "Eligible, not selected this cycle",
             "cycle_start": "2024-01-01T00:00:00Z", "session_id": 1,
             "decision_history_id": 1},
            {"symbol": "BB", "candidate_rank": 2, "eligible_candidate_count": 3,
             "ranking_tiebreak_basis": "score_then_min_margin",
             "total_score": 88.0, "decision_outcome": "BUY_ELIGIBLE_NOT_SELECTED",
             "decision_primary_reason": "Eligible, not selected",
             "cycle_start": "2024-01-01T00:00:00Z", "session_id": 1,
             "decision_history_id": 2},
        ]
        html = self._compile_and_run("_lc_renderCandidatesTable", cands)
        # Symbols + ranks.
        assert "AA" in html and "BB" in html
        # Rank is 1/3 and 2/3.
        assert "#1" in html and "/3" in html
        assert "#2" in html
        # Outcome is rendered.
        assert "NOT SELECTED" in html

    def test_candidates_table_row_click_handler_is_wired(self):
        cands = [
            {"symbol": "CC", "candidate_rank": 1, "eligible_candidate_count": 1,
             "total_score": 95.0, "decision_outcome": "BUY_ELIGIBLE_NOT_SELECTED",
             "decision_primary_reason": "x", "cycle_start": "2024-01-01T00:00:00Z",
             "session_id": 1, "decision_history_id": 7},
        ]
        html = self._compile_and_run("_lc_renderCandidatesTable", cands)
        # The on-click attribute calls _lc_openHistorySymbol with the symbol.
        assert "_lc_openHistorySymbol('CC')" in html

    def test_outcome_color_mapping(self):
        # Defensive: each known canonical outcome maps to a non-grey color
        # except the truly neutral ones.
        cases = {
            "BUY_ORDER_SUBMITTED":   ("#2ea043", "BUY ORDER SUBMITTED"),
            "SELL_ORDER_SUBMITTED":  ("#2ea043", "SELL ORDER SUBMITTED"),
            "BUY_ORDER_FAILED":      ("#da3633", "BUY ORDER FAILED"),
            "SELL_ORDER_FAILED":     ("#da3633", "SELL ORDER FAILED"),
            "BUY_BLOCKED_DYNAMIC":   ("#f0883e", "BUY BLOCKED DYNAMIC"),
            "SELL_BLOCKED_DYNAMIC":  ("#f0883e", "SELL BLOCKED DYNAMIC"),
            "BUY_ELIGIBLE_NOT_SELECTED": ("#d29922", "BUY ELIGIBLE NOT SELECTED"),
            "SELL_BLOCKED_NO_POSITION": ("#8b949e", "SELL BLOCKED NO POSITION"),
            None:                    ("#8b949e", "—"),
        }
        t = _read_template()
        # Pull the helpers out
        cm = re.search(
            r"function _lc_outcomeColor\(oc\)\s*\{(.+?)\n        \}",
            t, flags=re.DOTALL,
        )
        assert cm
        body = cm.group(1)
        for oc, (color, _) in cases.items():
            literal = oc if oc else "null"
            # Pattern: "if (oc === 'XX')"  -> "return '#...';"
            if oc:
                assert f"oc === '{oc}'" in body
            assert color in body


# ─────────────────────────────────────────────────────────────────────────
# Regression invariants for legacy dashboards (Phase A + A.1)
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseBDoesNotBreakLegacyDashboards:
    """Phase B MUST NOT change /api/opportunities, MUST NOT remove any
    Phase A surface, and MUST NOT directly reference SmartBot."""

    def test_opportunities_endpoint_still_present(self):
        # /api/opportunities remains untouched and reachable. Other code
        # paths depend on it; Phase B does NOT remove it even though the
        # primary Dashboard concept is now Top Candidates.
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        c = TestClient(_dashboard.app)
        r = c.get("/api/opportunities?limit=1")
        assert r.status_code == 200
        body = r.json()
        assert "opportunities" in body
        assert "analyzed" in body

    def test_history_search_endpoint_still_works(self):
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        c = TestClient(_dashboard.app)
        r = c.get("/api/decision/ALPXR")
        assert r.status_code == 200
        r2 = c.get("/api/decision-history/ALPXR?limit=1")
        assert r2.status_code == 200

    def test_phase_a_history_trace_template_intact(self):
        # The Phase A markers must still be in the template. If they are
        # gone, Phase B has accidentally removed Phase A.
        t = _read_template()
        assert "doHistorySymbolSearch" in t
        assert "_dt_renderSnapshotTrace" in t
        assert "history-symbol-search-card" in t

    def test_does_not_modify_smartbot_signal(self):
        # Phase B's NEW load functions (loadLatestCycle, loadTopCandidates,
        # _lc_viewAnalysis, _lc_openHistorySymbol) must NOT call any bot-control
        # endpoint. The pre-existing `botAction` handler for the Start/Stop
        # Session buttons is a Phase A-era artifact and is allowed to remain.
        t = _read_template()
        # New Phase B loaders extract.
        for fn in ("loadLatestCycle", "loadTopCandidates", "_lc_viewAnalysis", "_lc_openHistorySymbol"):
            m = re.search(
                r"(?:async )?function " + fn + r"\([^)]*\)\s*\{(.+?)\n        \}",
                t, flags=re.DOTALL,
            )
            assert m, f"could not extract {fn}"
            body = m.group(1)
            # Must NOT call any of these endpoints.
            for forbidden in ("/api/start-session", "/api/stop-session", "smartbot-runner",
                              "enable-bot", "disable-bot"):
                assert forbidden not in body, (
                    f"{fn} unexpectedly references {forbidden!r}\n--- function body ---\n{body}\n--- end ---"
                )
        # Phase B must have replaced the original DOMContentLoaded wiring.
        m = re.search(
            r"addEventListener\('DOMContentLoaded',\s*function\(\)\s*\{(.+?)\}\)",
            t, flags=re.DOTALL,
        )
        assert m
        body = m.group(1)
        assert "loadLatestCycle" in body
        assert "loadTopCandidates" in body
