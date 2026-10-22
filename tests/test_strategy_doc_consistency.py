"""Strategy-documentation drift checks (docs/SMARTBOT_STRATEGY.md).

Machine-verifiable contract:
1. Every ``<!-- param: key=value source=... -->`` marker in the strategy document
   must match the live extraction:
     - source=constructor  -> value MUST equal the AST-extracted default from
       ``SmartTradingBot.__init__`` in src/core/smart_bot.py.
     - source=persisted    -> key MUST exist in STRATEGY_SETTINGS_SCHEMA (the
       persisted value itself lives in the production DB, which CI cannot see;
       those markers are human-maintained and flagged for reviewer re-check).
2. Critical parameters MUST be marked (an unmarked critical param fails).
3. Structural facts pinned in the document (SELL threshold, BUY gate terms,
   dead code, paper-only guard, ATR sizing primacy, dual position caps) must
   still hold in the source text.

What this test CANNOT prove: that all prose matches every execution path, and
that the production DB persisted values are still current. See document §18.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "SMARTBOT_STRATEGY.md"
SMART_BOT = REPO_ROOT / "src" / "core" / "smart_bot.py"

from src.core.settings_service import STRATEGY_SETTINGS_SCHEMA  # noqa: E402

MARKER_RE = re.compile(r"<!--\s*param:\s*([A-Za-z0-9_]+)=([^\s>]+)\s+source=(\w+)\s*-->")


# ---------------------------------------------------------------------------
# Extraction helpers (no second source of truth: values are extracted, never
# re-typed)
# ---------------------------------------------------------------------------

def _constructor_defaults() -> dict[str, object]:
    """AST-extract ``self.<name> = <literal>`` assignments from SmartTradingBot.__init__."""
    tree = ast.parse(SMART_BOT.read_text())
    defaults: dict[str, object] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SmartTradingBot":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    for stmt in ast.walk(item):
                        if (
                            isinstance(stmt, ast.Assign)
                            and len(stmt.targets) == 1
                            and isinstance(stmt.targets[0], ast.Attribute)
                            and isinstance(stmt.targets[0].value, ast.Name)
                            and stmt.targets[0].value.id == "self"
                            and isinstance(stmt.value, ast.Constant)
                        ):
                            defaults[stmt.targets[0].attr] = stmt.value.value
    return defaults


def _doc_markers() -> dict[str, tuple[str, str]]:
    """Parse markers -> {key: (value, source)}."""
    markers: dict[str, tuple[str, str]] = {}
    for key, value, source in MARKER_RE.findall(DOC.read_text()):
        assert key not in markers, f"duplicate param marker: {key}"
        markers[key] = (value, source)
    return markers


def _coerce(raw: str):
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


# ---------------------------------------------------------------------------
# 1. Marker consistency
# ---------------------------------------------------------------------------

def test_doc_exists_and_has_markers():
    assert DOC.exists(), "docs/SMARTBOT_STRATEGY.md is missing"
    assert _doc_markers(), "strategy doc contains no param markers"


def constructor_marker_errors(
    markers: dict[str, tuple[str, str]], ctor: dict[str, object]
) -> list[str]:
    """Shared drift-detection logic: documented constructor values vs AST extraction."""
    errors: list[str] = []
    for key, (value, source) in markers.items():
        if source not in ("constructor", "hardcoded"):
            continue
        if source == "hardcoded":
            continue  # verified structurally in test_hardcoded_markers
        if key not in ctor:
            errors.append(
                f"doc marker {key} (constructor) has no matching "
                f"self.{key} literal assignment in SmartTradingBot.__init__"
            )
        elif _coerce(value) != ctor[key]:
            errors.append(
                f"doc marker {key}: documented {value} != code default {ctor[key]!r}"
            )
    return errors


def missing_critical(markers: dict[str, tuple[str, str]]) -> list[str]:
    """Pure helper: critical params absent from the document's markers."""
    return [
        k for k in CRITICAL_SCHEMA_KEYS + CRITICAL_CONSTRUCTOR_KEYS if k not in markers
    ]


def test_constructor_markers_match_ast_defaults():
    ctor = _constructor_defaults()
    errors = constructor_marker_errors(_doc_markers(), ctor)
    assert not errors, "; ".join(errors)
    checked = sum(
        1 for _, (_, s) in _doc_markers().items() if s == "constructor"
    )
    assert checked >= 5, "too few constructor markers verified; extraction may be broken"


def test_persisted_markers_are_schema_keys():
    for key, (value, source) in _doc_markers().items():
        if source != "persisted":
            continue
        assert key in STRATEGY_SETTINGS_SCHEMA, (
            f"doc marker {key} (persisted) is not a STRATEGY_SETTINGS_SCHEMA key"
        )


def test_special_hardcoded_markers():
    # Documented dual-cap conflict: 10.0 hardcoded execution cap must exist in source.
    markers = _doc_markers()
    if "max_position_percentage_exec_cap" in markers:
        assert markers["max_position_percentage_exec_cap"][0] == "10.0"
        src = SMART_BOT.read_text()
        assert re.search(r"max_position_percentage\s*=\s*10\.0", src), (
            "documented hardcoded 10% execution cap not found; "
            "update docs/SMARTBOT_STRATEGY.md §7/§12"
        )


# ---------------------------------------------------------------------------
# 2. Critical parameters must be marked
# ---------------------------------------------------------------------------

CRITICAL_SCHEMA_KEYS = [
    "rsi_buy_threshold",
    "rsi_sell_threshold",
    "sma_fast",
    "sma_slow",
    "rsi_period",
    "hourly_weight",
    "loop_delay_seconds",
    "max_sector_concentration",
    "max_correlation",
    "max_portfolio_beta",
    "max_drawdown_pct",
    "daily_loss_limit_pct",
    "atr_position_size_pct",
    "rotation_threshold",
    "min_score_buy",
    "enable_multi_timeframe",
    "enable_liquidity_filter",
    "enable_volume_confirmation",
    "enable_regime_filter",
    "enable_sp_filter",
    "enable_sector_filter",
    "enable_ai_conflict_filter",
]

CRITICAL_CONSTRUCTOR_KEYS = [
    "stop_loss_pct",
    "take_profit_pct",
    "trailing_stop_pct",
    "atr_stop_multiplier",
    "min_daily_volume",
    "max_spread_pct",
    "max_position_pct",
    "trade_amount",
    "earnings_days_skip",
]


def test_critical_params_are_marked():
    missing = missing_critical(_doc_markers())
    assert not missing, f"unmarked critical strategy parameters: {missing}"


# ---------------------------------------------------------------------------
# 3. Structural facts pinned by the document
# ---------------------------------------------------------------------------

def _src() -> str:
    return SMART_BOT.read_text()


def test_sell_threshold_expression_exists():
    assert "blended_signed <= -50" in _src(), (
        "Path-2 SELL rule changed; update docs/SMARTBOT_STRATEGY.md §4"
    )


def test_buy_gate_terms_exist():
    src = _src()
    for term in (
        "rsi < self.rsi_buy_threshold",
        "sma_fast > sma_slow",
        "float(macd_hist_for_gate) > 0",
        "not self.enable_volume_confirmation",
        ">= 1.0",
    ):
        assert term in src, f"BUY gate term missing from source: {term!r}"


def test_trailing_stop_is_dead_code():
    """Documented claim: trailing_stop_pct is assigned once and never read."""
    hits = [
        line
        for line in _src().splitlines()
        if "trailing_stop_pct" in line and "=" in line and not line.strip().startswith("#")
    ]
    assert len(hits) == 1, (
        "trailing_stop_pct gained read sites (or lost its assignment); "
        "docs/SMARTBOT_STRATEGY.md §9 dead-code claim is stale"
    )


def test_exit_tranches_never_called():
    assert "calculate_exit_tranches" not in _src(), (
        "calculate_exit_tranches is now referenced by smart_bot.py; "
        "docs/SMARTBOT_STRATEGY.md §9 'defined but never called' is stale"
    )


def test_atr_stop_multiplier_is_live_in_late_stop_path():
    """§9 (corrected): atr_stop_multiplier IS used to price the >-10% stop."""
    assert re.search(
        r"atr_stop_multiplier\s*\*\s*atr", _src()
    ), (
        "atr_stop_multiplier no longer prices the late ATR stop; "
        "update docs/SMARTBOT_STRATEGY.md §9"
    )


def test_bool_numeric_marker_never_false_verifies():
    """Type guard: documented 1/0 must not verify against a boolean runtime value."""
    from scripts.verify_strategy_doc_runtime import _values_match, coerce

    assert _values_match(coerce("1"), True) is False
    assert _values_match(coerce("0"), False) is False
    assert _values_match(coerce("true"), True) is True
    assert _values_match(coerce("35"), 35) is True


def test_min_score_sell_does_not_exist():
    src = _src()
    assert "min_score_sell" not in src, (
        "min_score_sell appeared in smart_bot.py; "
        "docs/SMARTBOT_STRATEGY.md §4 non-existence claim is stale"
    )
    assert "min_score_sell" not in STRATEGY_SETTINGS_SCHEMA


def test_paper_only_guard_present():
    src = _src()
    assert "trading_bot_paper_only_guard" in src
    assert 'TRADING_BOT_PAPER_ONLY' in src


def test_atr_sizing_is_primary():
    src = _src()
    assert "risk_amount = portfolio_value * self.risk_per_trade" in src, (
        "ATR primary sizing expression changed; update §7"
    )


def test_no_position_aging_exists():
    src = _src()
    for term in ("age_days", "days_held", "hold_days"):
        assert term not in src, (
            f"{term} found in source: a position-aging rule may now exist; "
            "update §9/§15"
        )


def test_doc_records_source_revision():
    # PROVENANCE ONLY: the recorded revision states which commit the document
    # was verified against. Its presence is NOT proof that the document tracks
    # every future commit — CI cannot diff prose against arbitrary revisions.
    # Runtime persisted-value verification (when run in the deployed env) uses
    # scripts/verify_strategy_doc_runtime.py. See doc §18.
    text = DOC.read_text()
    assert "d54752c" in text, (
        "source revision not recorded; when rebasing the doc, update the header"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
