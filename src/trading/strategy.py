"""
Trading strategy configuration and logic

This module contains the core trading strategy logic, including:
- Technical indicator thresholds (RSI, SMA, MACD, ATR)
- Signal generation rules
- Strategy parameters and risk management helpers
"""

from dataclasses import dataclass
from typing import Optional, Tuple, List
import numpy as np
import pandas as pd


@dataclass
class StrategyConfig:
    """Configuration for technical trading strategy"""

    # Moving averages
    sma_fast: int = 20
    sma_slow: int = 50
    sma_trend: int = 200  # long-term trend filter

    # RSI
    rsi_period: int = 14
    rsi_buy_threshold: int = 40       # "value area" for buys
    rsi_sell_threshold: int = 60      # "stretched" area for exits
    rsi_strong_oversold: int = 30     # strong buy zone
    rsi_strong_overbought: int = 75   # strong sell zone

    # MACD (trend / momentum)
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Volatility (ATR)
    atr_period: int = 14

    # Position sizing
    max_position_pct: float = 0.15    # Max 15% of portfolio per position
    reserve_cash_pct: float = 0.20    # Keep 20% cash reserved
    risk_per_trade_pct: float = 0.01  # Risk 1% of portfolio per trade (used with ATR)

    # Risk parameters (fixed % fallback)
    stop_loss_pct: float = 0.05       # 5% stop loss
    take_profit_pct: float = 0.10     # 10% take profit

    # ATR-based stops (optional, more "pro")
    atr_stop_multiple: float = 2.0    # e.g. 2 * ATR below entry for long
    atr_take_multiple: float = 3.0    # e.g. 3 * ATR above entry for long

    # Trend band: how far above/below trend MA to call it UP / DOWN vs SIDEWAYS
    trend_band_pct: float = 0.01      # 1% band around trend MA


class TechnicalStrategy:
    """Technical analysis strategy using SMA, RSI, MACD, ATR"""

    def __init__(self, 
                 config: Optional[StrategyConfig] = None,
                 min_buy_score: float = 3.0,
                 min_sell_score: float = -3.0,
                 strong_threshold: float = 4.5):
        self.config = config or StrategyConfig()
        self.min_buy_score = min_buy_score
        self.min_sell_score = min_sell_score
        self.strong_threshold = strong_threshold

    # -------------------------------------------------------------------------
    # INDICATORS
    # -------------------------------------------------------------------------
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate technical indicators on price data.

        Expects columns: ['open', 'high', 'low', 'close', 'volume'] at minimum.
        Adds:
            - SMA_{fast}, SMA_{slow}, SMA_{trend}
            - RSI
            - MACD, MACD_signal, MACD_hist
            - ATR
        """
        cfg = self.config

        min_bars = self.get_min_bars_required()
        if len(df) < min_bars:
            return df

        df = df.copy()  # avoid side effects if caller reuses the frame

        close = df["close"]

        # -----------------------------
        # Simple Moving Averages
        # -----------------------------
        df[f"SMA_{cfg.sma_fast}"] = close.rolling(window=cfg.sma_fast).mean()
        df[f"SMA_{cfg.sma_slow}"] = close.rolling(window=cfg.sma_slow).mean()
        df[f"SMA_{cfg.sma_trend}"] = close.rolling(window=cfg.sma_trend).mean()

        # -----------------------------
        # RSI (SMA-based, 14-period)
        # -----------------------------
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        avg_gain = gain.rolling(window=cfg.rsi_period).mean()
        avg_loss = loss.rolling(window=cfg.rsi_period).mean()

        # avoid division by zero
        avg_loss = avg_loss.replace(0, np.nan)
        rs = avg_gain / avg_loss
        df["RSI"] = 100 - (100 / (1 + rs))

        # -----------------------------
        # MACD (trend/momentum)
        # -----------------------------
        ema_fast = close.ewm(span=cfg.macd_fast, adjust=False).mean()
        ema_slow = close.ewm(span=cfg.macd_slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        macd_signal = macd.ewm(span=cfg.macd_signal, adjust=False).mean()
        macd_hist = macd - macd_signal

        df["MACD"] = macd
        df["MACD_signal"] = macd_signal
        df["MACD_hist"] = macd_hist

        # -----------------------------
        # ATR (volatility)
        # -----------------------------
        high = df["high"]
        low = df["low"]
        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        df["ATR"] = tr.rolling(window=cfg.atr_period).mean()

        return df

    # -------------------------------------------------------------------------
    # BASIC SIGNAL API (backwards compatible with your current usage)
    # -------------------------------------------------------------------------
    def evaluate_signal(
        self,
        sma_fast: float,
        sma_slow: float,
        rsi: float,
    ) -> Tuple[Optional[str], str]:
        """
        Original simple SMA + RSI signal logic (kept for compatibility).

        Returns:
            (signal, strength)
            - signal: 'BUY', 'SELL', or None
            - strength: 'WEAK', 'MEDIUM', 'STRONG'
        """
        signal: Optional[str] = None
        strength = "WEAK"

        if pd.isna(sma_fast) or pd.isna(sma_slow) or pd.isna(rsi):
            return None, strength

        # BUY: Fast SMA > Slow SMA AND RSI below buy threshold
        if sma_fast > sma_slow and rsi < self.config.rsi_buy_threshold:
            signal = "BUY"
            strength = "STRONG" if rsi < self.config.rsi_strong_oversold else "MEDIUM"

        # SELL: Fast SMA < Slow SMA AND RSI above sell threshold
        elif sma_fast < sma_slow and rsi > self.config.rsi_sell_threshold:
            signal = "SELL"
            strength = "STRONG" if rsi > self.config.rsi_strong_overbought else "MEDIUM"

        return signal, strength

    # -------------------------------------------------------------------------
    # ADVANCED SIGNAL (uses full indicator context like a pro)
    # -------------------------------------------------------------------------
    def evaluate_signal_advanced(self, row: pd.Series) -> Tuple[Optional[str], str, List[str]]:
        """
        More robust, professional-style signal evaluation.

        Uses:
            - Long-term trend (SMA_trend)
            - Local trend (fast vs slow SMA)
            - RSI zones
            - MACD direction / histogram

        Returns:
            (signal, strength, reasons)
            - signal: 'BUY', 'SELL', or None
            - strength: 'WEAK' | 'MEDIUM' | 'STRONG'
            - reasons: list of human-readable reasons
        """
        cfg = self.config
        reasons: List[str] = []

        close = row.get("close", np.nan)
        sma_fast = row.get(f"SMA_{cfg.sma_fast}", np.nan)
        sma_slow = row.get(f"SMA_{cfg.sma_slow}", np.nan)
        sma_trend = row.get(f"SMA_{cfg.sma_trend}", np.nan)
        rsi = row.get("RSI", np.nan)
        macd = row.get("MACD", np.nan)
        macd_signal = row.get("MACD_signal", np.nan)
        macd_hist = row.get("MACD_hist", np.nan)

        # Basic sanity
        if any(pd.isna(x) for x in (close, sma_fast, sma_slow, rsi)):
            reasons.append("Insufficient indicator data")
            return None, "WEAK", reasons

        # -----------------------------
        # Trend regime
        # -----------------------------
        trend = "UNKNOWN"
        if not pd.isna(sma_trend):
            band = cfg.trend_band_pct
            upper = sma_trend * (1 + band)
            lower = sma_trend * (1 - band)
            if close > upper:
                trend = "UP"
            elif close < lower:
                trend = "DOWN"
            else:
                trend = "SIDEWAYS"
        reasons.append(f"Trend regime: {trend}")

        # Local trend via fast/slow SMA
        if sma_fast > sma_slow:
            reasons.append("Local trend: bullish (fast SMA > slow SMA)")
        elif sma_fast < sma_slow:
            reasons.append("Local trend: bearish (fast SMA < slow SMA)")
        else:
            reasons.append("Local trend: neutral (fast SMA ≈ slow SMA)")

        # -----------------------------
        # Long (BUY) setup
        # -----------------------------
        long_candidate = (
            trend in ("UP", "SIDEWAYS")  # avoid fighting a strong downtrend
            and sma_fast >= sma_slow
            and rsi <= cfg.rsi_buy_threshold
        )

        # If MACD is available, require momentum turning up
        macd_filter_ok = True
        if not pd.isna(macd) and not pd.isna(macd_signal):
            macd_filter_ok = macd > macd_signal
            if macd_filter_ok:
                reasons.append("MACD bullish (MACD > signal)")
            else:
                reasons.append("MACD not yet bullish (MACD ≤ signal)")

        if long_candidate and macd_filter_ok:
            reasons.append(f"RSI={rsi:.1f} below buy threshold {cfg.rsi_buy_threshold}")
            signal = "BUY"

            # Strength classification
            if rsi <= cfg.rsi_strong_oversold and (not pd.isna(macd_hist) and macd_hist > 0):
                strength = "STRONG"
                reasons.append("Strong BUY: deeply oversold RSI + MACD histogram > 0")
            else:
                strength = "MEDIUM"
                reasons.append("Medium BUY: conditions met but not extreme")

            return signal, strength, reasons

        # -----------------------------
        # Short / Exit (SELL) setup
        # NOTE: In a long-only system this is usually a "close long" not open short.
        # -----------------------------
        short_candidate = (
            trend in ("DOWN", "SIDEWAYS")
            and sma_fast <= sma_slow
            and rsi >= cfg.rsi_sell_threshold
        )

        macd_bear_ok = True
        if not pd.isna(macd) and not pd.isna(macd_signal):
            macd_bear_ok = macd < macd_signal
            if macd_bear_ok:
                reasons.append("MACD bearish (MACD < signal)")
            else:
                reasons.append("MACD not yet bearish (MACD ≥ signal)")

        if short_candidate and macd_bear_ok:
            reasons.append(f"RSI={rsi:.1f} above sell threshold {cfg.rsi_sell_threshold}")
            signal = "SELL"

            if rsi >= cfg.rsi_strong_overbought and (not pd.isna(macd_hist) and macd_hist < 0):
                strength = "STRONG"
                reasons.append("Strong SELL: overbought RSI + MACD histogram < 0")
            else:
                strength = "MEDIUM"
                reasons.append("Medium SELL: conditions met but not extreme")

            return signal, strength, reasons

        # -----------------------------
        # No clear signal
        # -----------------------------
        reasons.append("No high-conviction BUY/SELL setup")
        return None, "WEAK", reasons

    # -------------------------------------------------------------------------
    # EXIT LOGIC
    # -------------------------------------------------------------------------
    def should_exit_position(
        self,
        entry_price: float,
        current_price: float,
    ) -> Tuple[bool, Optional[str]]:
        """
        Original simple exit logic with fixed % stop loss / take profit.

        Returns:
            (should_exit, reason)
        """
        pnl_pct = (current_price - entry_price) / entry_price

        if pnl_pct <= -self.config.stop_loss_pct:
            return True, f"Stop loss triggered ({pnl_pct:.2%})"

        if pnl_pct >= self.config.take_profit_pct:
            return True, f"Take profit triggered ({pnl_pct:.2%})"

        return False, None

    def should_exit_position_advanced(
        self,
        entry_price: float,
        current_price: float,
        direction: str = "LONG",
        entry_atr: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        ATR-aware exit logic.

        Args:
            entry_price: price at entry
            current_price: current market price
            direction: 'LONG' or 'SHORT'
            entry_atr: ATR at entry time (needs to be stored by the caller)

        Returns:
            (should_exit, reason)
        """
        cfg = self.config
        pnl_pct = (current_price - entry_price) / entry_price

        # Fallback to simple fixed % rules if ATR not provided
        if entry_atr is None or entry_atr <= 0:
            return self.should_exit_position(entry_price, current_price)

        if direction.upper() == "LONG":
            hard_stop = entry_price - cfg.atr_stop_multiple * entry_atr
            take_level = entry_price + cfg.atr_take_multiple * entry_atr

            if current_price <= hard_stop:
                return True, f"ATR stop loss hit (price {current_price:.2f} ≤ {hard_stop:.2f}, PnL {pnl_pct:.2%})"
            if current_price >= take_level:
                return True, f"ATR take profit hit (price {current_price:.2f} ≥ {take_level:.2f}, PnL {pnl_pct:.2%})"

        else:  # SHORT (if you ever allow it)
            hard_stop = entry_price + cfg.atr_stop_multiple * entry_atr
            take_level = entry_price - cfg.atr_take_multiple * entry_atr

            if current_price >= hard_stop:
                return True, f"ATR stop loss hit (short) (price {current_price:.2f} ≥ {hard_stop:.2f}, PnL {pnl_pct:.2%})"
            if current_price <= take_level:
                return True, f"ATR take profit hit (short) (price {current_price:.2f} ≤ {take_level:.2f}, PnL {pnl_pct:.2%})"

        return False, None

    # -------------------------------------------------------------------------
    # POSITION SIZING HELPER
    # -------------------------------------------------------------------------
    def compute_position_size(
        self,
        portfolio_value: float,
        price: float,
        atr: Optional[float] = None,
    ) -> int:
        """
        Compute a position size in shares based on:
        - max_position_pct cap
        - risk_per_trade_pct with ATR-based per-share risk if available

        Returns:
            integer number of shares
        """
        cfg = self.config
        if price <= 0 or portfolio_value <= 0:
            return 0

        max_dollar = portfolio_value * cfg.max_position_pct
        max_shares_by_cap = max_dollar // price

        # If no ATR, just use the cap
        if atr is None or atr <= 0:
            return int(max_shares_by_cap)

        # Per-share risk: max of ATR-based stop distance and fixed % stop
        atr_risk_per_share = cfg.atr_stop_multiple * atr
        pct_risk_per_share = price * cfg.stop_loss_pct
        per_share_risk = max(atr_risk_per_share, pct_risk_per_share)

        risk_budget = portfolio_value * cfg.risk_per_trade_pct
        if per_share_risk <= 0:
            shares_by_risk = max_shares_by_cap
        else:
            shares_by_risk = risk_budget // per_share_risk

        shares = int(max(0, min(max_shares_by_cap, shares_by_risk)))
        return shares

    # -------------------------------------------------------------------------
    # VOLUME SHELVES (Support/Resistance from Volume Profile)
    # -------------------------------------------------------------------------
    def compute_volume_profile(
        self,
        df: pd.DataFrame,
        lookback: int = 100,
        n_bins: int = 20,
    ) -> Optional[pd.DataFrame]:
        """
        Approximate volume-by-price profile over the last N bars.

        Returns a DataFrame with columns: ['price_bin_mid', 'volume']
        sorted by price_bin_mid ascending.
        """
        if len(df) < lookback:
            return None

        window = df.iloc[-lookback:]
        prices = window["close"].values
        vols = window["volume"].values

        price_min, price_max = prices.min(), prices.max()
        if price_min == price_max:
            return None

        bins = np.linspace(price_min, price_max, n_bins + 1)
        idx = np.digitize(prices, bins) - 1
        idx = np.clip(idx, 0, n_bins - 1)

        vol_by_bin = np.zeros(n_bins)
        for i, v in zip(idx, vols):
            vol_by_bin[i] += v

        bin_mids = (bins[:-1] + bins[1:]) / 2.0
        profile = pd.DataFrame(
            {"price_bin_mid": bin_mids, "volume": vol_by_bin}
        ).sort_values("price_bin_mid")

        return profile

    def find_volume_shelves(
        self,
        profile: pd.DataFrame,
        top_k: int = 3,
        prominence_pct: float = 0.5,
    ) -> pd.DataFrame:
        """
        Identify 'volume shelves' (high volume nodes) in a volume profile.

        - top_k: max number of shelves to return
        - prominence_pct: how strong a node must be vs avg volume to count
        """
        if profile is None or profile.empty:
            return pd.DataFrame(columns=["price_bin_mid", "volume"])

        avg_vol = profile["volume"].mean()
        if avg_vol <= 0:
            return profile.iloc[0:0]

        # keep bins with volume >= avg * (1 + prominence_pct)
        threshold = avg_vol * (1 + prominence_pct)
        shelves = profile[profile["volume"] >= threshold].copy()
        shelves = shelves.sort_values("volume", ascending=False).head(top_k)
        return shelves.sort_values("price_bin_mid")

    def evaluate_signal_advanced_with_shelves(
        self,
        row: pd.Series,
        shelves: Optional[pd.DataFrame] = None,
        shelf_proximity_pct: float = 0.01,   # within 1% of a shelf
    ) -> Tuple[Optional[str], str, List[str]]:
        """
        Wraps evaluate_signal_advanced and adds volume-shelf context.

        - For BUY: prefer when near a support shelf (below price),
          and avoid if very close to a strong resistance shelf above.
        """
        base_signal, base_strength, reasons = self.evaluate_signal_advanced(row)

        # Always add shelf context if available (even without a signal)
        if shelves is None or shelves.empty:
            return base_signal, base_strength, reasons

        price = row.get("close", np.nan)
        if pd.isna(price):
            return base_signal, base_strength, reasons

        # classify shelves as potential support / resistance
        support_levels = shelves[shelves["price_bin_mid"] < price]
        resistance_levels = shelves[shelves["price_bin_mid"] > price]

        # nearest support below
        nearest_support = None
        if not support_levels.empty:
            nearest_support = support_levels.iloc[-1]["price_bin_mid"]

        # nearest resistance above
        nearest_resistance = None
        if not resistance_levels.empty:
            nearest_resistance = resistance_levels.iloc[0]["price_bin_mid"]

        # distance measures
        if nearest_support is not None:
            dist_support = (price - nearest_support) / price
            reasons.append(f"Nearest support shelf: ${nearest_support:.2f} ({dist_support:.2%} below)")
        else:
            dist_support = None
            reasons.append("No support shelf below in lookback window")

        if nearest_resistance is not None:
            dist_resist = (nearest_resistance - price) / price
            reasons.append(f"Nearest resistance shelf: ${nearest_resistance:.2f} ({dist_resist:.2%} above)")
        else:
            dist_resist = None
            reasons.append("No resistance shelf above in lookback window")

        # Apply small logic tweaks using shelves only if there's a base signal
        if base_signal is None:
            return base_signal, base_strength, reasons
        
        # Apply shelf-based signal modifications
        # Example policy:
        # - For BUY: avoid if immediate resistance < 1% above.
        # - For BUY: upgrade strength if near a strong support (<1% below).
        if base_signal == "BUY":
            if dist_resist is not None and dist_resist < shelf_proximity_pct:
                reasons.append("⚠️ BUY downgraded: price too close to resistance volume shelf")
                return None, "WEAK", reasons

            if dist_support is not None and dist_support < shelf_proximity_pct:
                reasons.append("✅ BUY upgraded: price sitting on support volume shelf")
                # bump strength up one notch if not already STRONG
                if base_strength == "MEDIUM":
                    base_strength = "STRONG"
                elif base_strength == "WEAK":
                    base_strength = "MEDIUM"

        # For SELL (closing longs), you might invert the logic if you want:
        # e.g. respect support shelves by avoiding selling right into support.
        # For now we leave SELL as-is and just provide context in reasons.

        return base_signal, base_strength, reasons

    def evaluate_signal_scored(
        self,
        row: pd.Series,
        shelves: Optional[pd.DataFrame] = None,
    ) -> Tuple[Optional[str], str, List[str], float]:
        """
        Score-based signal evaluation (pro-style voting system).

        Each factor adds bullish or bearish points.
        Final decision:
            score >= +3  -> BUY
            score <= -3  -> SELL
            otherwise    -> no trade

        Returns:
            (signal, strength, reasons, score)
        """
        cfg = self.config
        reasons: List[str] = []

        close = row.get("close", np.nan)
        sma_fast = row.get(f"SMA_{cfg.sma_fast}", np.nan)
        sma_slow = row.get(f"SMA_{cfg.sma_slow}", np.nan)
        sma_trend = row.get(f"SMA_{cfg.sma_trend}", np.nan)
        rsi = row.get("RSI", np.nan)
        macd = row.get("MACD", np.nan)
        macd_signal = row.get("MACD_signal", np.nan)
        macd_hist = row.get("MACD_hist", np.nan)

        if any(pd.isna(x) for x in (close, sma_fast, sma_slow, rsi, sma_trend)):
            reasons.append("Insufficient indicator data for scored evaluation")
            return None, "WEAK", reasons, 0.0

        score = 0.0

        # -----------------------------
        # Trend regime (big weight)
        # -----------------------------
        band = cfg.trend_band_pct
        upper = sma_trend * (1 + band)
        lower = sma_trend * (1 - band)

        if close > upper:
            reasons.append("Trend: UP (price above trend SMA band) +2.0")
            score += 2.0
        elif close < lower:
            reasons.append("Trend: DOWN (price below trend SMA band) -2.0")
            score -= 2.0
        else:
            reasons.append("Trend: SIDEWAYS (price inside trend SMA band) +0.0")

        # -----------------------------
        # Local trend (fast vs slow SMA)
        # -----------------------------
        if sma_fast > sma_slow:
            reasons.append("Local trend bullish (fast SMA > slow SMA) +1.0")
            score += 1.0
        elif sma_fast < sma_slow:
            reasons.append("Local trend bearish (fast SMA < slow SMA) -1.0")
            score -= 1.0
        else:
            reasons.append("Local trend neutral (fast SMA ≈ slow SMA) +0.0")

        # -----------------------------
        # RSI contribution
        # -----------------------------
        if not pd.isna(rsi):
            if rsi <= cfg.rsi_strong_oversold:
                reasons.append(f"RSI={rsi:.1f}: strongly oversold +2.0")
                score += 2.0
            elif rsi <= cfg.rsi_buy_threshold:
                reasons.append(f"RSI={rsi:.1f}: mildly oversold / value +1.0")
                score += 1.0
            elif rsi >= cfg.rsi_strong_overbought:
                reasons.append(f"RSI={rsi:.1f}: strongly overbought -2.0")
                score -= 2.0
            elif rsi >= cfg.rsi_sell_threshold:
                reasons.append(f"RSI={rsi:.1f}: mildly overbought / stretched -1.0")
                score -= 1.0
            else:
                reasons.append(f"RSI={rsi:.1f}: neutral zone +0.0")

        # -----------------------------
        # MACD contribution
        # -----------------------------
        if not pd.isna(macd) and not pd.isna(macd_signal):
            if macd > macd_signal:
                reasons.append("MACD bullish (MACD > signal) +1.0")
                score += 1.0
            elif macd < macd_signal:
                reasons.append("MACD bearish (MACD < signal) -1.0")
                score -= 1.0

        if not pd.isna(macd_hist):
            if macd_hist > 0:
                reasons.append("MACD histogram positive (up momentum) +0.5")
                score += 0.5
            elif macd_hist < 0:
                reasons.append("MACD histogram negative (down momentum) -0.5")
                score -= 0.5

        # -----------------------------
        # Volume shelves (optional contribution)
        # -----------------------------
        if shelves is not None and not shelves.empty:
            price = close
            support_levels = shelves[shelves["price_bin_mid"] < price]
            resistance_levels = shelves[shelves["price_bin_mid"] > price]

            nearest_support = None
            nearest_resistance = None

            if not support_levels.empty:
                nearest_support = support_levels.iloc[-1]["price_bin_mid"]
            if not resistance_levels.empty:
                nearest_resistance = resistance_levels.iloc[0]["price_bin_mid"]

            if nearest_support is not None:
                dist_support = (price - nearest_support) / price
                reasons.append(f"Nearest support shelf: ${nearest_support:.2f} ({dist_support:.2%} below)")
                if dist_support < 0.01:
                    reasons.append("Price near support shelf -> bullish +0.5")
                    score += 0.5

            if nearest_resistance is not None:
                dist_resist = (nearest_resistance - price) / price
                reasons.append(f"Nearest resistance shelf: ${nearest_resistance:.2f} ({dist_resist:.2%} above)")
                if dist_resist < 0.01:
                    reasons.append("Price near resistance shelf -> bearish -0.5")
                    score -= 0.5

        # -----------------------------
        # Decision from score (using configurable thresholds)
        # -----------------------------
        signal: Optional[str] = None
        strength = "WEAK"

        if score >= self.min_buy_score:
            signal = "BUY"
            if score >= self.strong_threshold:
                strength = "STRONG"
            else:
                strength = "MEDIUM"
            reasons.append(f"✅ Final decision: BUY (score={score:.1f})")

        elif score <= self.min_sell_score:
            signal = "SELL"
            if score <= -self.strong_threshold:
                strength = "STRONG"
            else:
                strength = "MEDIUM"
            reasons.append(f"🔻 Final decision: SELL (score={score:.1f})")

        else:
            reasons.append(f"⏸️  No trade: score={score:.1f} (need ≥{self.min_buy_score} or ≤{self.min_sell_score})")

        return signal, strength, reasons, float(score)

    # -------------------------------------------------------------------------
    # META
    # -------------------------------------------------------------------------
    def get_min_bars_required(self) -> int:
        """Get minimum number of bars required for indicator calculation."""
        cfg = self.config
        return max(
            cfg.sma_slow,
            cfg.sma_trend,
            cfg.rsi_period,
            cfg.macd_slow,
            cfg.atr_period,
        )
