"""
Adaptive Learning Engine

Reads closed trade history from Supabase (via SimpleSupabaseREST) and
adjusts bot parameters toward what historically worked.

Design principles:
  - Only adjusts params that have statistically reliable samples (>= 10 trades)
  - Changes are bounded (never moves params more than ±10 pts from defaults)
  - All adjustments are logged for auditability
  - Integrates cleanly with SmartTradingBot's existing parameter fields
  - Uses the same REST pattern as the rest of the codebase (no SQLAlchemy)

Usage:
    engine = AdaptiveLearningEngine(db=bot.db)
    changes = engine.run_analysis_and_apply(bot)
"""
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AdaptiveLearningEngine:
    """
    Reads historical trade data and produces parameter adjustment
    recommendations, then applies them to the bot instance.
    """

    # Hard bounds to prevent runaway parameter drift
    RSI_BUY_MIN = 20
    RSI_BUY_MAX = 40
    RSI_SELL_MIN = 60
    RSI_SELL_MAX = 80
    SMA_FAST_MIN = 5
    SMA_FAST_MAX = 20
    SMA_SLOW_MIN = 20
    SMA_SLOW_MAX = 50
    SIZE_MULT_MIN = 0.5
    SIZE_MULT_MAX = 2.0
    MIN_SAMPLE = 10   # Need at least this many trades before adjusting

    def __init__(self, db):
        """
        Args:
            db: SimpleSupabaseREST instance (bot.db)
        """
        self.db = db
        self._last_run: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_closed_trades(self, lookback_days: int = 90) -> pd.DataFrame:
        """Fetch closed trades from Supabase REST API."""
        if not self.db or not self.db.is_available():
            return pd.DataFrame()

        try:
            since = (
                datetime.now(timezone.utc) - timedelta(days=lookback_days)
            ).isoformat()

            response = requests.get(
                f"{self.db.rest_url}/trades"
                f"?select=*&status=eq.CLOSED&created_at=gte.{since}&limit=2000",
                headers=self.db.headers,
                timeout=30,
            )

            if response.status_code != 200:
                logger.debug(f"Could not fetch trades: {response.status_code}")
                return pd.DataFrame()

            trades = response.json()
            if not trades:
                return pd.DataFrame()

            df = pd.DataFrame(trades)
            for col in ["rsi", "pnl_percent"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df

        except Exception as e:
            logger.debug(f"Trade fetch failed: {e}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Analysis methods
    # ------------------------------------------------------------------

    def _best_rsi_buy_threshold(self, df: pd.DataFrame) -> Optional[int]:
        """
        Sweep RSI buy thresholds (20-40) and return the one with the best
        (win_rate * avg_pnl) score on historical BUY trades.
        Returns None if insufficient data.
        """
        buy_df = df[df.get("side", pd.Series(dtype=str)) == "BUY"].copy() \
            if "side" in df.columns else df.copy()

        if "rsi" not in buy_df.columns or "pnl_percent" not in buy_df.columns:
            return None

        buy_df = buy_df.dropna(subset=["rsi", "pnl_percent"])
        if len(buy_df) < self.MIN_SAMPLE:
            return None

        best_score = -float("inf")
        best_threshold = None

        for threshold in range(20, 42, 2):
            subset = buy_df[buy_df["rsi"] <= threshold]
            if len(subset) < self.MIN_SAMPLE:
                continue
            win_rate = (subset["pnl_percent"] > 0).mean()
            avg_pnl = subset["pnl_percent"].mean()
            score = win_rate * max(avg_pnl, 0)
            if score > best_score:
                best_score = score
                best_threshold = threshold

        if best_threshold is None:
            return None

        clamped = max(self.RSI_BUY_MIN, min(self.RSI_BUY_MAX, best_threshold))
        logger.info(f"Adaptive RSI buy threshold: {clamped} (score={best_score:.4f})")
        return clamped

    def _symbol_size_multipliers(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        Compute per-symbol position-size multipliers based on historical
        win rate.

        Win rate > 60% → multiplier up to 1.5×
        Win rate < 40% → multiplier down to 0.7×
        """
        if "symbol" not in df.columns or "pnl_percent" not in df.columns:
            return {}

        multipliers: Dict[str, float] = {}

        for sym, group in df.groupby("symbol"):
            if len(group) < self.MIN_SAMPLE:
                continue

            clean = group.dropna(subset=["pnl_percent"])
            if len(clean) < self.MIN_SAMPLE:
                continue

            win_rate = (clean["pnl_percent"] > 0).mean()
            # Linear: 0% win → 0.5×, 50% win → 1.0×, 100% win → 2.5×
            raw_mult = 0.5 + win_rate * 2.0
            mult = round(
                max(self.SIZE_MULT_MIN, min(self.SIZE_MULT_MAX, raw_mult)), 2
            )
            multipliers[sym] = mult
            logger.debug(f"Symbol {sym}: win_rate={win_rate:.1%} → size_mult={mult}")

        return multipliers

    def _sma_period_adjustment(self, df: pd.DataFrame) -> Dict[str, int]:
        """
        Heuristic SMA period tuning based on profit factor of recent trades.

        High profit factor (>2) → tighten SMAs (faster signals)
        Low profit factor (<0.8) → loosen SMAs (slower, fewer false signals)
        """
        if "pnl_percent" not in df.columns:
            return {}

        clean = df.dropna(subset=["pnl_percent"])
        if len(clean) < self.MIN_SAMPLE:
            return {}

        winners = clean[clean["pnl_percent"] > 0]
        losers = clean[clean["pnl_percent"] <= 0]

        avg_win = winners["pnl_percent"].mean() if len(winners) > 0 else 0.0
        avg_loss = abs(losers["pnl_percent"].mean()) if len(losers) > 0 else 1.0
        pf = avg_win / avg_loss if avg_loss > 0 else 1.0

        if pf > 2.0:
            return {"sma_fast": 8, "sma_slow": 25}   # Tighter → faster signals
        if pf < 0.8:
            return {"sma_fast": 15, "sma_slow": 40}  # Looser → fewer false signals
        return {}   # No adjustment needed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_full_analysis(self, lookback_days: int = 90) -> Dict:
        """
        Run complete analysis and return recommendations dict.

        Returns:
            {
                'rsi_buy_threshold': int | None,
                'sma_adjustments': {sma_fast: int, sma_slow: int} | {},
                'symbol_multipliers': {symbol: float},
                'trade_count': int,
            }
        """
        logger.info(f"Adaptive learning: fetching last {lookback_days}-day trades...")
        df = self._fetch_closed_trades(lookback_days)

        if df.empty:
            logger.info("No closed trades available for adaptive learning.")
            return {}

        result = {
            "trade_count": len(df),
            "lookback_days": lookback_days,
            "rsi_buy_threshold": self._best_rsi_buy_threshold(df),
            "sma_adjustments": self._sma_period_adjustment(df),
            "symbol_multipliers": self._symbol_size_multipliers(df),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            f"Adaptive learning: {len(df)} trades analysed — "
            f"RSI={result['rsi_buy_threshold']}, "
            f"SMA={result['sma_adjustments']}, "
            f"{len(result['symbol_multipliers'])} symbol multipliers"
        )
        return result

    def apply_to_bot(self, bot, analysis: Dict) -> List[str]:
        """
        Apply learned parameters to bot instance.

        Args:
            bot:      SmartTradingBot instance
            analysis: Output from run_full_analysis()

        Returns:
            List of human-readable change descriptions.
        """
        if not analysis:
            return []

        changes: List[str] = []

        # RSI buy threshold
        new_rsi = analysis.get("rsi_buy_threshold")
        if new_rsi is not None and new_rsi != bot.rsi_buy_threshold:
            old = bot.rsi_buy_threshold
            bot.rsi_buy_threshold = new_rsi
            changes.append(f"rsi_buy_threshold: {old} → {new_rsi}")

        # SMA periods
        sma_adj = analysis.get("sma_adjustments", {})
        if sma_adj.get("sma_fast") and sma_adj["sma_fast"] != bot.sma_fast:
            old = bot.sma_fast
            bot.sma_fast = sma_adj["sma_fast"]
            changes.append(f"sma_fast: {old} → {bot.sma_fast}")
        if sma_adj.get("sma_slow") and sma_adj["sma_slow"] != bot.sma_slow:
            old = bot.sma_slow
            bot.sma_slow = sma_adj["sma_slow"]
            changes.append(f"sma_slow: {old} → {bot.sma_slow}")

        # Per-symbol size multipliers
        multipliers = analysis.get("symbol_multipliers", {})
        if multipliers:
            if not hasattr(bot, "_symbol_size_multipliers"):
                bot._symbol_size_multipliers = {}
            bot._symbol_size_multipliers.update(multipliers)
            changes.append(
                f"Updated size multipliers for {len(multipliers)} symbols"
            )

        return changes

    def run_analysis_and_apply(self, bot, lookback_days: int = 90) -> List[str]:
        """
        Convenience method: run analysis then immediately apply to bot.

        Returns list of parameter changes applied.
        """
        analysis = self.run_full_analysis(lookback_days)
        changes = self.apply_to_bot(bot, analysis)
        if changes:
            logger.info(
                f"Adaptive learning applied {len(changes)} parameter changes: "
                + "; ".join(changes)
            )
        self._last_run = datetime.now(timezone.utc)
        return changes
