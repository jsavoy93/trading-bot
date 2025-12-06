"""Signal generation and evaluation

This module handles the generation of trading signals by combining:
- Technical analysis (from strategy.py)
- AI insights (optional)
- Market data
"""

from typing import Optional, Dict, Any
from datetime import datetime, timezone
import pandas as pd
import logging

from .strategy import TechnicalStrategy


class SignalGenerator:
    """Generates trading signals from technical and AI analysis"""
    
    def __init__(
        self, 
        strategy: TechnicalStrategy,
        ai_agent: Optional[Any] = None,
        use_ai_enhancement: bool = False,
        use_advanced_signals: bool = False
    ):
        """Initialize signal generator
        
        Args:
            strategy: Technical strategy instance
            ai_agent: Optional AI agent for enhanced analysis
            use_ai_enhancement: Whether to use AI to enhance signals
            use_advanced_signals: Whether to use advanced multi-indicator analysis
        """
        self.strategy = strategy
        self.ai_agent = ai_agent
        self.use_ai_enhancement = use_ai_enhancement
        self.use_advanced_signals = use_advanced_signals
    
    async def analyze_symbol(
        self, 
        symbol: str, 
        price_data: pd.DataFrame
    ) -> Optional[Dict[str, Any]]:
        """Analyze a symbol and generate trading signal
        
        Args:
            symbol: Stock symbol to analyze
            price_data: DataFrame with OHLCV data
            
        Returns:
            Dictionary with analysis results or None if insufficient data
        """
        # Validate data
        min_bars = self.strategy.get_min_bars_required()
        if len(price_data) < min_bars:
            logging.debug(f"{symbol}: Only {len(price_data)} bars, need {min_bars}")
            return None
        
        # Calculate indicators
        df = self.strategy.calculate_indicators(price_data)
        latest = df.iloc[-1]
        
        # Validate indicators calculated
        if pd.isna(latest[f'SMA_{self.strategy.config.sma_fast}']):
            logging.debug(f"{symbol}: SMA_fast is NaN")
            return None
        if pd.isna(latest['RSI']):
            logging.debug(f"{symbol}: RSI is NaN")
            return None
        
        price = latest['close']
        
        # Generate signal using appropriate method
        if self.use_advanced_signals:
            signal, signal_strength, reasons = self.strategy.evaluate_signal_advanced(latest)
            
            # Build analysis result with advanced info
            analysis_result = {
                'symbol': symbol,
                'price': price,
                'sma_fast': latest[f'SMA_{self.strategy.config.sma_fast}'],
                'sma_slow': latest[f'SMA_{self.strategy.config.sma_slow}'],
                'rsi': latest['RSI'],
                'signal': signal,
                'signal_strength': signal_strength,
                'reasons': reasons,  # List of diagnostic reasons
                'timestamp': latest.get('timestamp', datetime.now(timezone.utc))
            }
            
            # Include additional indicators if available
            if 'MACD' in latest and not pd.isna(latest['MACD']):
                analysis_result['macd'] = latest['MACD']
                analysis_result['macd_signal'] = latest.get('MACD_signal')
                analysis_result['macd_hist'] = latest.get('MACD_hist')
            
            if 'ATR' in latest and not pd.isna(latest['ATR']):
                analysis_result['atr'] = latest['ATR']
            
        else:
            # Use basic signal logic (backwards compatible)
            sma_fast = latest[f'SMA_{self.strategy.config.sma_fast}']
            sma_slow = latest[f'SMA_{self.strategy.config.sma_slow}']
            rsi = latest['RSI']
            
            signal, signal_strength = self.strategy.evaluate_signal(sma_fast, sma_slow, rsi)
            
            analysis_result = {
                'symbol': symbol,
                'price': price,
                'sma_fast': sma_fast,
                'sma_slow': sma_slow,
                'rsi': rsi,
                'signal': signal,
                'signal_strength': signal_strength,
                'timestamp': latest.get('timestamp', datetime.now(timezone.utc))
            }
        
        # AI enhancement (if enabled and configured)
        ai_insight = None
        if self.use_ai_enhancement and self.ai_agent and signal:
            ai_insight = await self._get_ai_enhancement(symbol, signal, signal_strength)
            if ai_insight:
                signal_strength = ai_insight['strength']
                analysis_result['signal_strength'] = signal_strength
                analysis_result['ai_insight'] = ai_insight['message']
        
        return analysis_result
    
    async def _get_ai_enhancement(
        self, 
        symbol: str, 
        signal: str, 
        signal_strength: str
    ) -> Optional[Dict[str, str]]:
        """Get AI enhancement for a trading signal
        
        Args:
            symbol: Stock symbol
            signal: Technical signal (BUY/SELL)
            signal_strength: Technical signal strength
            
        Returns:
            Dictionary with enhanced signal info or None
        """
        try:
            # Get AI research
            ai_research = await self.ai_agent.research_symbol(symbol, lookback_days=2)
            
            if ai_research and ai_research.get('ai_recommendation'):
                ai_rec = ai_research['ai_recommendation']
                ai_signal = ai_rec.recommendation.upper()
                
                # Check if AI confirms or conflicts with technical signal
                if ai_signal == signal and ai_rec.confidence > 0.7:
                    return {
                        'strength': 'AI_ENHANCED',
                        'message': f"AI confirms {signal} with {ai_rec.confidence:.1%} confidence"
                    }
                elif ai_signal != signal:
                    return {
                        'strength': 'CONFLICTED',
                        'message': f"AI suggests {ai_signal} vs technical {signal}"
                    }
            
            return None
            
        except Exception as e:
            logging.debug(f"AI analysis failed for {symbol}: {e}")
            return None
    
    def evaluate_position_exit(
        self, 
        symbol: str,
        entry_price: float,
        current_price: float,
        current_rsi: Optional[float] = None,
        entry_atr: Optional[float] = None,
        direction: str = "LONG",
        use_advanced_exit: bool = False
    ) -> tuple[bool, Optional[str]]:
        """Evaluate if an existing position should be exited
        
        Args:
            symbol: Stock symbol
            entry_price: Original entry price
            current_price: Current market price
            current_rsi: Current RSI value (optional)
            entry_atr: ATR at entry (optional, for advanced exits)
            direction: Position direction ('LONG' or 'SHORT')
            use_advanced_exit: Whether to use ATR-based exits
            
        Returns:
            Tuple of (should_exit, reason)
        """
        # Use advanced exit if requested and ATR available
        if use_advanced_exit and entry_atr is not None:
            should_exit, reason = self.strategy.should_exit_position_advanced(
                entry_price=entry_price,
                current_price=current_price,
                direction=direction,
                entry_atr=entry_atr
            )
            
            if should_exit:
                return True, reason
        else:
            # Check basic stop loss / take profit
            should_exit, reason = self.strategy.should_exit_position(
                entry_price, 
                current_price
            )
            
            if should_exit:
                return True, reason
        
        # Check RSI-based exit (extreme overbought for longs)
        if current_rsi and current_rsi > 80:
            return True, f"Extreme RSI overbought ({current_rsi:.1f})"
        
        return False, None
