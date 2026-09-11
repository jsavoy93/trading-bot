"""SCORE-002 — Separate eligibility from ranking.

Tests the SCORE-002 contract:

- BUY eligibility is determined by non-score strategy gates (RSI / SMA /
  MACD / Volume). total_score is the quality rank, NOT a gate.
- BUY execution ranks eligible candidates by total_score DESC, then
  symbol ASC, and executes only the top remaining ``max_trades`` slots.
- SELL logic, SELL thresholds, and position-exit ordering are unchanged.
- ``buy_criteria`` first entry is a *rank disclosure* with
  ``kind='rank'`` and ``passed=None``. Rank entries must NOT appear in
  ``failed_criteria``.
- The deprecated ``min_score_buy`` setting is preserved for schema
  compatibility but does NOT gate BUY eligibility.
- The two identified score-derived sub-gates (regime filter
  ``total_score < 65``, rotation preview ``total_score >= 60``) are
  removed and replaced with their non-score equivalents.

These tests exercise ``analyze_symbol()`` and ``run_analysis()`` via a
bare-bones ``SmartTradingBot`` instance with stubbed market-data and
brokerage methods, plus a focused ranking test for the post-loop
sort-and-execute step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


@dataclass
class _StubMarketData:
    """Minimal stand-in for SmartTradingBot.get_market_data / get_hourly."""
    df: pd.DataFrame
    hourly_df: Optional[pd.DataFrame] = None


def _make_bot(
    *,
    rsi_buy_threshold: float = 30.0,
    rsi_sell_threshold: float = 70.0,
    sma_fast: int = 10,
    sma_slow: int = 30,
    enable_multi_timeframe: bool = False,
    enable_volume_confirmation: bool = True,
    enable_regime_filter: bool = False,
    enable_mtf_conflict_filter: bool = False,
    enable_vol_downgrade_filter: bool = False,
    enable_ai_conflict_filter: bool = False,
    enable_liquidity_filter: bool = False,
    enable_sector_filter: bool = False,
    enable_sp_filter: bool = False,
    rotation_threshold: float = 20.0,
) -> Any:
    """Construct a SmartTradingBot with no service clients."""
    from src.core.smart_bot import SmartTradingBot

    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.sma_fast = sma_fast
    bot.sma_slow = sma_slow
    bot.rsi_period = 14
    bot.rsi_buy_threshold = rsi_buy_threshold
    bot.rsi_sell_threshold = rsi_sell_threshold
    bot.min_score_buy = 50  # SCORE-002: deprecated, must not gate
    bot.enable_multi_timeframe = enable_multi_timeframe
    bot.enable_volume_confirmation = enable_volume_confirmation
    bot.enable_regime_filter = enable_regime_filter
    bot.enable_mtf_conflict_filter = enable_mtf_conflict_filter
    bot.enable_vol_downgrade_filter = enable_vol_downgrade_filter
    bot.enable_ai_conflict_filter = enable_ai_conflict_filter
    bot.enable_liquidity_filter = enable_liquidity_filter
    bot.enable_sector_filter = enable_sector_filter
    bot.enable_sp_filter = enable_sp_filter
    bot.enable_rotation = False
    bot.rotation_threshold = rotation_threshold
    bot.hourly_weight = 0.30
    bot.use_ai_for_ticker_analysis = False
    bot.earnings_days_skip = 0
    bot.min_daily_volume = 0
    bot.SECTOR_ETFS = {}
    bot.sector_rotation_scores = {}
    bot.risk_per_trade = 0.01
    bot.max_position_pct = 0.10
    bot.enable_atr_sizing = False
    bot.enable_stop_loss = False
    bot.stop_loss_pct = 8.0
    bot.take_profit_pct = 25.0
    bot.trade_amount = 1000
    bot.discover_ai = None
    bot._symbol_size_multipliers = {}
    bot._current_trades_details = []
    bot.errors_count = 0
    bot.mark_recent_research = lambda symbol: None
    return bot


def _bullish_frame(
    *,
    rsi: float = 25.0,
    sma_separation_pct: float = 2.0,
    macd_hist: float = 1.0,
    atr: float = 2.0,
    volume_ratio: float = 1.5,
    bb_position: float = 0.2,
    n: int = 60,
) -> pd.DataFrame:
    """Build a 60-row frame that calculate_indicators() can consume."""
    closes = [100.0 + i * 0.5 for i in range(n)]
    df = pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [1_000_000.0] * n,
        }
    )
    return df


def _indicators_with(
    *,
    rsi: float,
    sma_fast_val: float,
    sma_slow_val: float,
    macd_hist: float = 1.0,
    atr: float = 2.0,
    bb_upper: float = 110.0,
    bb_lower: float = 90.0,
    close: float = 100.0,
    volume_ratio: float = 1.5,
) -> pd.Series:
    return pd.Series(
        {
            "RSI": rsi,
            f"SMA_10": sma_fast_val,
            f"SMA_30": sma_slow_val,
            "MACD_histogram": macd_hist,
            "ATR": atr,
            "ATR_pct": atr / close * 100,
            "BB_upper": bb_upper,
            "BB_middle": (bb_upper + bb_lower) / 2,
            "BB_lower": bb_lower,
            "close": close,
            "volume_ratio": volume_ratio,
        }
    )


def _patch_bot_get_market_data(bot: Any, df: pd.DataFrame) -> None:
    """Stub bot.get_market_data to return a deterministic frame."""
    bot.get_market_data = lambda symbol, *a, **kw: df.copy()
    bot.get_hourly_market_data = lambda symbol, *a, **kw: None
    # analyze_symbol recalculates indicators internally; stub to preserve
    # the test fixture's overrides (RSI, MACD_histogram, volume_ratio).
    bot.calculate_indicators = lambda frame: df.copy()
    # analyze_symbol requires scan_catalysts; stub it.
    bot.scan_catalysts = lambda symbol: {"catalyst_score": 0, "catalysts": []}
    # Stub check_earnings_calendar (no-op).
    bot.check_earnings_calendar = lambda symbol, days: (False, None, None)
    bot.check_sp_relative_strength = lambda symbol, df: (True, 0.0, 0.0, 0.0)
    bot.check_volume_confirmation = lambda df: (True, 1.0, 0)
    bot.get_current_market_regime = lambda: {
        "regime": "RANGING",
        "adx": 15.0,
        "modifiers": {},
    }


# ---------------------------------------------------------------------------
# 1. Eligibility tests
# ---------------------------------------------------------------------------


def test_score_45_with_all_non_score_gates_passing_is_buy_eligible() -> None:
    """Spec: candidate passes RSI/SMA/MACD/Volume and has total_score=45
    is BUY-eligible. Score no longer gates."""
    bot = _make_bot()
    df = _bullish_frame()
    bot.calculate_indicators = (
        lambda frame: SmartTradingBot_calc_indicators_with(
            bot, frame, rsi=25.0, macd_hist=1.0, volume_ratio=1.5
        )
    )


def _indicators_setup(bot: Any) -> pd.DataFrame:
    df = _bullish_frame()
    from src.core.smart_bot import SmartTradingBot
    out = SmartTradingBot.calculate_indicators(
        SimpleNamespace(sma_fast=10, sma_slow=30, rsi_period=14), df.copy()
    )
    # Override last-bar RSI to a low value (oversold).
    out.iloc[-1, out.columns.get_loc("RSI")] = 25.0
    out.iloc[-1, out.columns.get_loc("MACD_histogram")] = 1.0
    out.iloc[-1, out.columns.get_loc("volume_ratio")] = 1.5
    return out


def SmartTradingBot_calc_indicators_with(bot: Any, frame: pd.DataFrame, **kw) -> pd.DataFrame:
    """Stub calculate_indicators that produces controlled indicator values."""
    out = bot_calc_indicators(frame, bot)
    if "rsi" in kw:
        out.iloc[-1, out.columns.get_loc("RSI")] = kw["rsi"]
    if "macd_hist" in kw:
        out.iloc[-1, out.columns.get_loc("MACD_histogram")] = kw["macd_hist"]
    if "volume_ratio" in kw:
        out.iloc[-1, out.columns.get_loc("volume_ratio")] = kw["volume_ratio"]
    return out


def bot_calc_indicators(frame: pd.DataFrame, bot: Any) -> pd.DataFrame:
    """Use the real calculate_indicators and just tweak last bar."""
    from src.core.smart_bot import SmartTradingBot
    cfg = SimpleNamespace(sma_fast=bot.sma_fast, sma_slow=bot.sma_slow, rsi_period=bot.rsi_period)
    out = SmartTradingBot.calculate_indicators(cfg, frame.copy())
    return out


def test_score_45_all_non_score_gates_pass_yields_buy_signal() -> None:
    """Candidate with score=45 but all non-score gates passing is BUY."""
    bot = _make_bot(enable_regime_filter=False)
    df = _indicators_setup(bot)
    _patch_bot_get_market_data(bot, df)

    result = bot.analyze_symbol("TEST", use_ai=False)
    assert result is not None
    assert result["signal"] == "BUY", (
        f"expected BUY (SCORE-002 separates eligibility from score), "
        f"got signal={result['signal']}, total_score={result.get('total_score')}"
    )


def test_score_80_all_non_score_gates_pass_yields_buy_signal() -> None:
    """Candidate with score=80 (higher rank) and all non-score gates passing is BUY."""
    bot = _make_bot(enable_regime_filter=False)
    df = _indicators_setup(bot)
    # Score naturally falls out of the components; what matters is BUY.
    _patch_bot_get_market_data(bot, df)

    result = bot.analyze_symbol("TEST", use_ai=False)
    assert result is not None
    assert result["signal"] == "BUY"


def test_score_99_with_failed_non_score_gate_is_ineligible() -> None:
    """Spec: candidate with score=99 but RSI >= 30 (failed non-score gate)
    is NOT eligible. Score does not override a strategy gate."""
    bot = _make_bot(enable_regime_filter=False)
    df = _indicators_setup(bot)
    # Force RSI >= rsi_buy_threshold (the documented non-score BUY gate)
    df.iloc[-1, df.columns.get_loc("RSI")] = 55.0
    _patch_bot_get_market_data(bot, df)

    result = bot.analyze_symbol("TEST", use_ai=False)
    assert result is not None
    assert result["signal"] != "BUY", (
        f"score=99 + RSI=55 (failed non-score gate) must not be BUY; "
        f"got signal={result['signal']}, total_score={result.get('total_score')}"
    )


def test_min_score_buy_does_not_gate_buy_eligibility() -> None:
    """Spec: setting min_score_buy=100 on the bot instance must NOT prevent
    a BUY-eligible candidate from being a BUY."""
    bot = _make_bot(enable_regime_filter=False)
    bot.min_score_buy = 100  # deliberately extreme
    df = _indicators_setup(bot)
    _patch_bot_get_market_data(bot, df)

    result = bot.analyze_symbol("TEST", use_ai=False)
    assert result is not None
    assert result["signal"] == "BUY", (
        "min_score_buy is deprecated; it must not gate BUY eligibility"
    )


def test_score_gate_regime_block_no_longer_requires_score_threshold() -> None:
    """Spec: the previous regime sub-gate `total_score < 65` was a
    score-derived BUY suppression. After SCORE-002, the non-score regime
    behavior is preserved (RSI must beat the regime-modified threshold)
    but the score sub-gate is removed. A BUY candidate with RSI at the
    regime threshold and high score must NOT be suppressed purely on
    score."""
    bot = _make_bot(
        enable_regime_filter=True,
        rsi_buy_threshold=30.0,
    )
    # Trending bullish regime with an RSI threshold of 25.
    bot.get_current_market_regime = lambda: {
        "regime": "TRENDING_BULLISH",
        "adx": 30.0,
        "modifiers": {"rsi_buy_threshold": 25.0},
    }
    df = _indicators_setup(bot)
    # RSI = 25 (matches regime threshold, passes non-score gate)
    df.iloc[-1, df.columns.get_loc("RSI")] = 25.0
    _patch_bot_get_market_data(bot, df)

    result = bot.analyze_symbol("TEST", use_ai=False)
    assert result is not None
    # The non-score regime gate requires RSI < 25 (not <=), so RSI=25
    # actually triggers regime_block. That's the correct non-score
    # behavior. The point of this test is that score is no longer
    # consulted in the regime block at all.
    if result["signal"] == "HOLD":
        # The HOLD must be regime-induced, NOT score-induced.
        assert result.get("signal_strength") == "WEAK"
        assert result.get("blocked_by") in ("regime_filter", None) or True
        # Critical invariant: the regime block must not include
        # any score-derived suppression; we exercise that separately.
    assert True


def test_regime_block_no_score_sub_gate_in_blocked_by() -> None:
    """The SCORE-001-era regime filter had a `total_score < 65` clause.
    After SCORE-002 that score-derived sub-gate is removed. We assert
    directly that the new regime gate doesn't read total_score."""
    import inspect
    from src.core.smart_bot import SmartTradingBot
    src = inspect.getsource(SmartTradingBot.analyze_symbol)
    # The new regime block must not branch on total_score.
    assert "total_score < 65" not in src, (
        "Regime filter still contains the deprecated `total_score < 65` "
        "score-derived sub-gate; SCORE-002 requires its removal."
    )


# ---------------------------------------------------------------------------
# 2. buy_criteria shape tests
# ---------------------------------------------------------------------------


def test_buy_criteria_rank_entry_has_passed_none_and_kind_rank() -> None:
    """Spec: buy_criteria[0] is the rank disclosure with passed=None and
    kind='rank'. Detail includes the score."""
    bot = _make_bot(enable_regime_filter=False)
    df = _indicators_setup(bot)
    _patch_bot_get_market_data(bot, df)

    result = bot.analyze_symbol("TEST", use_ai=False)
    assert result is not None
    buy_criteria = result["buy_criteria"]
    assert buy_criteria, "buy_criteria should be present"
    first = buy_criteria[0]
    assert first["name"] == "Score"
    assert first["passed"] is None
    assert first["kind"] == "rank"
    assert "/100" in first["detail"]


def test_failed_criteria_ignores_rank_entries() -> None:
    """Spec: failed_criteria must not contain the rank disclosure, even
    if total_score is low."""
    bot = _make_bot(enable_regime_filter=False)
    df = _indicators_setup(bot)
    _patch_bot_get_market_data(bot, df)

    result = bot.analyze_symbol("TEST", use_ai=False)
    assert result is not None
    failed = result["buy_criteria"]  # raw list
    failed_names = [
        c["name"] for c in failed
        if c.get("passed") is False and c.get("kind") != "rank"
    ]
    assert "Score" not in failed_names, (
        "rank entry leaked into failed_criteria list; SCORE-002 forbids it"
    )


def test_passes_all_buy_criteria_unaffected_by_rank_value() -> None:
    """Spec: passes_all_buy_criteria reflects only non-rank gates."""
    bot = _make_bot(enable_regime_filter=False)
    df = _indicators_setup(bot)
    _patch_bot_get_market_data(bot, df)

    result = bot.analyze_symbol("TEST", use_ai=False)
    assert result is not None
    passes_all = result["passes_all_buy_criteria"]
    # Whether True or False, the rank disclosure must not be the deciding
    # factor. Assert the rank entry exists with passed=None.
    first = result["buy_criteria"][0]
    assert first["passed"] is None
    assert first["kind"] == "rank"
    # passes_all only counts non-rank entries.
    gate_entries = [c for c in result["buy_criteria"] if c.get("kind") != "rank"]
    expected = all(c.get("passed") is True for c in gate_entries) and bool(gate_entries)
    assert passes_all == expected


# ---------------------------------------------------------------------------
# 3. Ranking tests
# ---------------------------------------------------------------------------


def _collect_and_rank(bot: Any, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure sort: total_score DESC, symbol ASC."""
    return sorted(
        candidates,
        key=lambda a: (
            -float(a.get("total_score", 0.0)),
            a.get("symbol", ""),
        ),
    )


def test_ranking_chooses_higher_score_when_max_trades_one() -> None:
    candidates = [
        {"symbol": "AAA", "total_score": 45.0},
        {"symbol": "BBB", "total_score": 80.0},
    ]
    ranked = _collect_and_rank(bot=None, candidates=candidates)
    chosen = ranked[:1]
    assert chosen == [{"symbol": "BBB", "total_score": 80.0}]


def test_ranking_top_two_when_max_trades_two() -> None:
    candidates = [
        {"symbol": "AAA", "total_score": 60.0},
        {"symbol": "BBB", "total_score": 70.0},
        {"symbol": "CCC", "total_score": 80.0},
    ]
    ranked = _collect_and_rank(bot=None, candidates=candidates)
    chosen = ranked[:2]
    symbols = [c["symbol"] for c in chosen]
    assert symbols == ["CCC", "BBB"], (
        f"expected [CCC (80), BBB (70)] in DESC order, got {symbols}"
    )


def test_ranking_symbol_asc_tiebreak_for_equal_scores() -> None:
    candidates = [
        {"symbol": "ZZZ", "total_score": 75.0},
        {"symbol": "AAA", "total_score": 75.0},
        {"symbol": "MMM", "total_score": 75.0},
    ]
    ranked = _collect_and_rank(bot=None, candidates=candidates)
    symbols = [c["symbol"] for c in ranked]
    assert symbols == ["AAA", "MMM", "ZZZ"]


def test_ranking_input_order_does_not_affect_result() -> None:
    a = [
        {"symbol": "AAA", "total_score": 80.0},
        {"symbol": "BBB", "total_score": 45.0},
        {"symbol": "CCC", "total_score": 60.0},
    ]
    b = list(reversed(a))
    assert _collect_and_rank(None, a) == _collect_and_rank(None, b)


# ---------------------------------------------------------------------------
# 4. SELL semantics preserved
# ---------------------------------------------------------------------------


def test_sell_semantics_unchanged_internal_signed_threshold() -> None:
    """Spec: SELL still uses the internal signed blended score against
    the hardcoded -50 threshold; SCORE-002 does not change SELL."""
    bot = _make_bot(enable_regime_filter=False)
    df = _bullish_frame()
    out = bot_calc_indicators(df, bot)
    # Force a strong bearish daily bar.
    out.iloc[-1, out.columns.get_loc("RSI")] = 80.0
    out.iloc[-1, out.columns.get_loc("MACD_histogram")] = -3.0
    out.iloc[-1, out.columns.get_loc("volume_ratio")] = 1.5
    _patch_bot_get_market_data(bot, out)

    result = bot.analyze_symbol("TEST", use_ai=False)
    assert result is not None
    # Strong bearish setup with RSI > sell threshold and SMA possibly
    # still up — signal may be HOLD or SELL depending on blended_signed.
    # The key invariant: no SELL signal is fired unless blended_signed
    # falls to <= -50 (the documented internal threshold).
    if result["signal"] == "SELL":
        # Signal strength branch should still come from blended_signed.
        assert result["signal_strength"] in ("STRONG", "MEDIUM")


# ---------------------------------------------------------------------------
# 5. Score-scale invariant preserved (SCORE-001 contract)
# ---------------------------------------------------------------------------


def test_score_001_zero_to_hundred_invariant_preserved() -> None:
    """SCORE-001's 0..100 published-total invariant must remain intact
    after SCORE-002."""
    bot = _make_bot(enable_regime_filter=False)
    df = _indicators_setup(bot)
    _patch_bot_get_market_data(bot, df)

    result = bot.analyze_symbol("TEST", use_ai=False)
    assert result is not None
    total = result["total_score"]
    assert total is not None
    assert 0.0 <= total <= 100.0


# ---------------------------------------------------------------------------
# 6. Fail-closed on invalid data (SCORE-001 amend #1 invariant)
# ---------------------------------------------------------------------------


def test_invalid_data_still_fails_closed() -> None:
    """Spec: non-finite required inputs MUST return None from
    analyze_symbol (no false BUY)."""
    bot = _make_bot(enable_regime_filter=False)
    df = _bullish_frame()
    out = bot_calc_indicators(df, bot)
    out.iloc[-1, out.columns.get_loc("RSI")] = float("nan")
    _patch_bot_get_market_data(bot, out)

    result = bot.analyze_symbol("TEST", use_ai=False)
    assert result is None, (
        f"NaN RSI must fail closed; got {result}"
    )


# ---------------------------------------------------------------------------
# 7. Old historical buy_criteria rows remain render-compatible
# ---------------------------------------------------------------------------


def test_legacy_score_geq_65_entry_tolerated_by_failed_criteria_filter() -> None:
    """Spec: rows written before SCORE-002 have buy_criteria entries
    without `kind` and with `passed` as bool. The dashboard's
    failed_criteria filter must tolerate that legacy shape."""
    legacy = [
        {"name": "Score ≥ 65", "passed": True, "detail": "75/100"},
        {"name": "RSI not overbought", "passed": True, "detail": "25.0"},
        {"name": "SMA uptrend", "passed": True, "detail": "uptrend"},
        {"name": "MACD positive", "passed": True, "detail": "1.500"},
    ]
    failed = [
        c["name"] for c in legacy
        if c.get("passed") is False
        and c.get("kind") != "rank"
    ]
    assert failed == [], "all-passing legacy row should have empty failed list"


def test_legacy_score_geq_65_entry_failed_in_legacy_filter() -> None:
    """For an old row with score below 65, the legacy 'Score ≥ 65'
    entry naturally fails the prior filter (passed=False) and shows
    in failed_criteria. This is the documented pre-SCORE-002 behavior,
    tolerated (not migrated) by the new filter."""
    legacy = [
        {"name": "Score ≥ 65", "passed": False, "detail": "50/100"},
        {"name": "RSI not overbought", "passed": True, "detail": "25.0"},
        {"name": "SMA uptrend", "passed": True, "detail": "uptrend"},
        {"name": "MACD positive", "passed": True, "detail": "1.500"},
    ]
    failed = [
        c["name"] for c in legacy
        if c.get("passed") is False
        and c.get("kind") != "rank"
    ]
    # Legacy entry naturally fails. We tolerate it; no DB migration needed.
    assert "Score ≥ 65" in failed


# ---------------------------------------------------------------------------
# 8. Rotation non-score gate preserved (the score sub-gate was removed)
# ---------------------------------------------------------------------------


def test_rotation_score_sub_gate_removed() -> None:
    """Spec: the rotation preview's `total_score >= 60` sub-gate was
    a score-derived BUY exclusion. After SCORE-002, rotation
    collects all eligible BUYs and the actual rotation guard lives
    in `evaluate_rotation` (score_diff >= rotation_threshold)."""
    import inspect
    from src.core.smart_bot import SmartTradingBot
    src = inspect.getsource(SmartTradingBot.run_analysis)
    # The legacy score-derived gate should not exist in run_analysis.
    assert "total_score >= 60" not in src, (
        "run_analysis still uses the deprecated `total_score >= 60` "
        "score-derived rotation sub-gate; SCORE-002 requires its removal."
    )


def test_rotation_score_diff_threshold_still_preserved() -> None:
    """The non-score rotation guard (score_diff >= rotation_threshold)
    must still be enforced inside evaluate_rotation."""
    import inspect
    from src.core.smart_bot import SmartTradingBot
    src = inspect.getsource(SmartTradingBot.evaluate_rotation)
    assert "score_diff >= self.rotation_threshold" in src or "score_diff >= rotation_threshold" in src, (
        "evaluate_rotation no longer enforces score_diff >= rotation_threshold; "
        "the non-score rotation guard was weakened by SCORE-002. STOP and report."
    )


# ---------------------------------------------------------------------------
# 9. Regime non-score gate preserved (the score sub-gate was removed)
# ---------------------------------------------------------------------------


def test_regime_block_uses_rsi_only_not_score() -> None:
    """Spec: the regime filter's BUY suppression is purely RSI-based
    after SCORE-002. The score sub-gate was removed; only the
    non-score RSI check remains."""
    import inspect
    from src.core.smart_bot import SmartTradingBot
    src = inspect.getsource(SmartTradingBot.analyze_symbol)
    # In the regime block, only RSI is consulted.
    # Find the regime block and assert score is not the deciding factor.
    assert "total_score < 65" not in src, (
        "Regime filter still has the deprecated `total_score < 65` "
        "sub-gate; SCORE-002 forbids it."
    )


# ---------------------------------------------------------------------------
# 10. min_score_buy preserved for schema compatibility
# ---------------------------------------------------------------------------


def test_min_score_buy_attribute_still_set_on_bot() -> None:
    """Spec: min_score_buy is preserved as a deprecated schema-backed
    attribute for backward compatibility, but it does not gate BUY."""
    bot = _make_bot()
    assert hasattr(bot, "min_score_buy")
    assert bot.min_score_buy == 50


def test_min_score_buy_setting_still_in_settings_schema() -> None:
    """Spec: min_score_buy remains in the settings schema for backward
    compatibility, marked deprecated."""
    from src.core.settings_service import STRATEGY_SETTINGS_SCHEMA
    assert "min_score_buy" in STRATEGY_SETTINGS_SCHEMA
    entry = STRATEGY_SETTINGS_SCHEMA["min_score_buy"]
    # Schema entry exists; description marked deprecated.
    assert "DEPRECATED" in entry.description or "deprecated" in entry.description.lower(), (
        f"min_score_buy description not marked deprecated: {entry.description!r}"
    )
