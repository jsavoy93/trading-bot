"""Trading strategy configuration and logic

This module contains the core trading strategy logic, including:
- Technical indicator thresholds (RSI, SMA)
- Signal generation rules
- Strategy parameters
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class StrategyConfig:
    """Configuration for technical trading strategy"""
    # SMA parameters
    sma_fast: int = 20
    sma_slow: int = 50
    
    # RSI parameters
    rsi_period: int = 14
    rsi_buy_threshold: int = 40
    rsi_sell_threshold: int = 60
    
    # Position sizing
    max_position_pct: float = 0.15  # Max 15% of portfolio per position
    reserve_cash_pct: float = 0.20  # Keep 20% cash reserved
    
    # Risk parameters
    stop_loss_pct: float = 0.05  # 5% stop loss
    take_profit_pct: float = 0.10  # 10% take profit


class TechnicalStrategy:
    """Technical analysis strategy using SMA and RSI indicators"""
    
    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators on price data
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            DataFrame with added indicator columns
        """
        if len(df) < max(self.config.sma_slow, self.config.rsi_period):
            return df
        
        # SMAs
        df[f'SMA_{self.config.sma_fast}'] = df['close'].rolling(
            window=self.config.sma_fast
        ).mean()
        df[f'SMA_{self.config.sma_slow}'] = df['close'].rolling(
            window=self.config.sma_slow
        ).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(
            window=self.config.rsi_period
        ).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(
            window=self.config.rsi_period
        ).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        return df
    
    def evaluate_signal(
        self, 
        sma_fast: float, 
        sma_slow: float, 
        rsi: float
    ) -> tuple[Optional[str], str]:
        """Evaluate technical indicators to generate trading signal
        
        Args:
            sma_fast: Fast SMA value
            sma_slow: Slow SMA value
            rsi: RSI value
            
        Returns:
            Tuple of (signal, signal_strength) where signal is 'BUY', 'SELL', or None
        """
        signal = None
        signal_strength = "WEAK"
        
        # BUY signal: Fast SMA > Slow SMA AND RSI oversold
        if sma_fast > sma_slow and rsi < self.config.rsi_buy_threshold:
            signal = "BUY"
            signal_strength = "STRONG" if rsi < 25 else "MEDIUM"
        
        # SELL signal: Fast SMA < Slow SMA AND RSI overbought
        elif sma_fast < sma_slow and rsi > self.config.rsi_sell_threshold:
            signal = "SELL"
            signal_strength = "STRONG" if rsi > 75 else "MEDIUM"
        
        return signal, signal_strength
    
    def should_exit_position(
        self, 
        entry_price: float, 
        current_price: float
    ) -> tuple[bool, Optional[str]]:
        """Determine if a position should be exited based on stop loss or take profit
        
        Args:
            entry_price: Original entry price
            current_price: Current market price
            
        Returns:
            Tuple of (should_exit, reason)
        """
        pnl_pct = (current_price - entry_price) / entry_price
        
        # Stop loss check
        if pnl_pct <= -self.config.stop_loss_pct:
            return True, f"Stop loss triggered ({pnl_pct:.2%})"
        
        # Take profit check
        if pnl_pct >= self.config.take_profit_pct:
            return True, f"Take profit triggered ({pnl_pct:.2%})"
        
        return False, None
    
    def get_min_bars_required(self) -> int:
        """Get minimum number of bars required for indicator calculation"""
        return max(self.config.sma_slow, self.config.rsi_period)
