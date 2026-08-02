"""
Market Regime Classifier

Uses ADX (Average Directional Index) to detect market trends:
- ADX > 25 = Trending
- ADX < 20 = Range-bound
- ADX 20-25 = Transition zone
"""
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)


class MarketRegimeClassifier:
    """Classifies market regime using ADX indicator"""
    
    # Regime constants
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING = "RANGING"
    TRANSITIONING = "TRANSITIONING"
    
    def __init__(self, db=None, adx_period: int = 14, trend_threshold: float = 25.0, 
                 range_threshold: float = 20.0, regime_symbol: str = "SPY"):
        """
        Initialize the regime classifier.
        
        Args:
            db: Supabase database client for storing regime history
            adx_period: Period for ADX calculation (default: 14)
            trend_threshold: ADX above this = trending (default: 25)
            range_threshold: ADX below this = ranging (default: 20)
            regime_symbol: Symbol to use for regime detection (default: SPY)
        """
        self.db = db
        self.adx_period = adx_period
        self.trend_threshold = trend_threshold
        self.range_threshold = range_threshold
        self.regime_symbol = regime_symbol
        
        # Cache for current regime (recalculated each cycle)
        self._current_regime = None
        self._regime_cache_time = None
        self._cache_ttl_seconds = 60  # Cache for 60 seconds
        
        # Track previous regime for change detection
        self._previous_regime = None
    
    def calculate_adx(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate ADX (Average Directional Index), +DI, and -DI.
        
        Uses Wilder's smoothing method as per standard ADX implementation.
        
        Args:
            df: DataFrame with OHLCV data (needs high, low, close columns)
            
        Returns:
            DataFrame with ADX, +DI, -DI columns added
        """
        if len(df) < self.adx_period + 1:
            return df
        
        df = df.copy()
        
        # Calculate True Range (TR)
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        
        # Calculate +DM and -DM (Directional Movement)
        high_diff = df['high'].diff()
        low_diff = -df['low'].diff()
        
        plus_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
        minus_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
        
        # Calculate smoothed values using Wilder's smoothing
        # First value = simple average, subsequent = prior + (current - prior) / period
        def wilder_smooth(series, period):
            """Apply Wilder's smoothing"""
            result = series.copy()
            result.iloc[period] = series.iloc[:period+1].mean()
            for i in range(period + 1, len(series)):
                result.iloc[i] = (result.iloc[i-1] * (period - 1) + series.iloc[i]) / period
            return result
        
        # Smooth TR, +DM, -DM
        tr_smooth = wilder_smooth(tr, self.adx_period)
        plus_dm_smooth = wilder_smooth(plus_dm, self.adx_period)
        minus_dm_smooth = wilder_smooth(minus_dm, self.adx_period)
        
        # Calculate +DI and -DI (Directional Indicators)
        # +DI = (+DM / TR) * 100
        # -DI = (-DM / TR) * 100
        plus_di = (plus_dm_smooth / tr_smooth) * 100
        minus_di = (minus_dm_smooth / tr_smooth) * 100
        
        # Calculate DX (Directional Index)
        # DX = (|+DI - -DI| / |+DI + -DI|) * 100
        di_sum = plus_di + minus_di
        di_diff = abs(plus_di - minus_di)
        dx = (di_diff / di_sum) * 100
        
        # Calculate ADX (Average Directional Index) = smoothed DX
        adx = wilder_smooth(dx, self.adx_period)
        
        # Store results
        df['ADX'] = adx
        df['plus_DI'] = plus_di
        df['minus_DI'] = minus_di
        
        return df
    
    def get_regime_from_adx(self, adx: float, plus_di: float, minus_di: float) -> Tuple[str, Dict]:
        """
        Determine market regime from ADX values.
        
        Args:
            adx: ADX value
            plus_di: +DI value
            minus_di: -DI value
            
        Returns:
            Tuple of (regime_name, details_dict)
        """
        details = {
            'adx': adx,
            'plus_di': plus_di,
            'minus_di': minus_di,
            'trend_strength': 'strong' if adx > 25 else ('weak' if adx > 20 else 'none')
        }
        
        if adx >= self.trend_threshold:
            # Trending market
            if plus_di > minus_di:
                return self.TRENDING_BULLISH, details
            else:
                return self.TRENDING_BEARISH, details
        
        elif adx <= self.range_threshold:
            # Range-bound market
            return self.RANGING, details
        
        else:
            # Transition zone (20-25)
            return self.TRENDING_BEARISH, details
    
    def get_current_regime(self, force_refresh: bool = False) -> Dict:
        """
        Get the current market regime.
        
        Uses cached value if available and not expired.
        
        Args:
            force_refresh: Force recalculation even if cached
            
        Returns:
            Dict with regime, adx, plus_di, minus_di, timestamp
        """
        now = datetime.now(timezone.utc)
        
        # Return cached value if valid
        if (not force_refresh and 
            self._current_regime is not None and 
            self._regime_cache_time is not None and
            (now - self._regime_cache_time).total_seconds() < self._cache_ttl_seconds):
            return self._current_regime
        
        return self._current_regime
    
    def calculate_current_regime(self, data_source=None) -> Dict:
        """
        Calculate and return current market regime.
        
        Args:
            data_source: Optional data provider (if None, uses database)
            
        Returns:
            Dict with regime information
        """
        from src.data.historical_pipeline import HistoricalDataPipeline
        from src.database.simple_rest import SimpleSupabaseREST
        
        try:
            # Get data - try database first, then API
            df = None
            
            # Try Supabase first
            if self.db and self.db.available:
                try:
                    pipeline = HistoricalDataPipeline(db=self.db)
                    df = pipeline.get_ohlcv(self.regime_symbol, 
                                          start_date=(datetime.now() - pd.Timedelta(days=60)).strftime('%Y-%m-%d'))
                except:
                    pass
            
            # Fallback to Alpaca API
            if df is None or len(df) < 30:
                try:
                    from alpaca.data import StockBarsRequest, TimeFrame
                    from datetime import timedelta
                    
                    client = data_source
                    if client is None:
                        from alpaca.data import StockHistoricalDataClient
                        import os
                        from dotenv import load_dotenv
                        load_dotenv()
                        client = StockHistoricalDataClient(
                            os.getenv('APCA_API_KEY_ID'),
                            os.getenv('APCA_API_SECRET_KEY')
                        )
                    
                    request = StockBarsRequest(
                        symbol_or_symbols=[self.regime_symbol],
                        timeframe=TimeFrame.Day,
                        start=datetime.now() - timedelta(days=60),
                        end=datetime.now()
                    )
                    barset = client.get_stock_bars(request)
                    
                    if barset and self.regime_symbol in barset.data:
                        bars = barset.data[self.regime_symbol]
                        df = pd.DataFrame([{
                            'date': bar.timestamp,
                            'open': bar.open,
                            'high': bar.high,
                            'low': bar.low,
                            'close': bar.close,
                            'volume': bar.volume
                        } for bar in bars])
                except Exception as e:
                    logger.debug(f"Could not get data from Alpaca: {e}")
            
            if df is None or len(df) < self.adx_period:
                logger.warning(f"Insufficient data for ADX calculation on {self.regime_symbol}")
                return {
                    'regime': self.TRANSITIONING,
                    'adx': 0,
                    'plus_di': 0,
                    'minus_di': 0,
                    'symbol': self.regime_symbol,
                    'timestamp': datetime.now(timezone.utc),
                    'error': 'Insufficient data'
                }
            
            # Calculate ADX
            df = df.sort_values('date').reset_index(drop=True)
            df = self.calculate_adx(df)
            
            # Get latest values
            latest = df.iloc[-1]
            adx = latest.get('ADX', 0)
            plus_di = latest.get('plus_DI', 0)
            minus_di = latest.get('minus_DI', 0)
            
            if pd.isna(adx) or adx is None:
                adx = 0
            if pd.isna(plus_di) or plus_di is None:
                plus_di = 0
            if pd.isna(minus_di) or minus_di is None:
                minus_di = 0
            
            # Get regime
            regime, details = self.get_regime_from_adx(adx, plus_di, minus_di)
            
            # Check for regime change
            regime_changed = False
            if self._current_regime is not None:
                old_regime = self._current_regime.get('regime')
                if old_regime != regime:
                    regime_changed = True
                    logger.info(f"🔄 Regime Change: {old_regime} → {regime} (ADX: {adx:.1f})")
            
            # Build result
            result = {
                'regime': regime,
                'adx': float(adx),
                'plus_di': float(plus_di),
                'minus_di': float(minus_di),
                'symbol': self.regime_symbol,
                'timestamp': datetime.now(timezone.utc),
                'regime_changed': regime_changed,
                'previous_regime': self._previous_regime
            }
            
            # Update cache
            self._previous_regime = self._current_regime.get('regime') if self._current_regime else None
            self._current_regime = result
            self._regime_cache_time = datetime.now(timezone.utc)
            
            # Log current regime
            logger.info(f"📊 Market Regime: {regime} (ADX: {adx:.1f}, +DI: {plus_di:.1f}, -DI: {minus_di:.1f})")
            
            # Store in database if available
            if self.db and self.db.available:
                self._store_regime(result)
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating market regime: {e}")
            return {
                'regime': self.TRANSITIONING,
                'adx': 0,
                'plus_di': 0,
                'minus_di': 0,
                'symbol': self.regime_symbol,
                'timestamp': datetime.now(timezone.utc),
                'error': str(e)
            }
    
    def _store_regime(self, regime_data: Dict):
        """Store regime data in database."""
        try:
            import requests
            
            record = {
                'symbol': regime_data['symbol'],
                'adx_value': regime_data['adx'],
                'plus_di': regime_data['plus_di'],
                'minus_di': regime_data['minus_di'],
                'regime': regime_data['regime'],
                'spy_price': 0,  # Could add price if available
            }
            
            requests.post(
                f"{self.db.rest_url}/market_regime_log",
                headers=self.db.headers,
                json=record
            )
        except Exception as e:
            logger.debug(f"Could not store regime: {e}")
    
    def get_strategy_modifiers(self, regime: str) -> Dict:
        """
        Get strategy modifiers based on current regime.
        
        Returns:
            Dict with modifier values for signal generation
        """
        modifiers = {
            'rsi_buy_threshold': 30,      # Default RSI buy threshold
            'rsi_sell_threshold': 70,     # Default RSI sell threshold
            'position_size_multiplier': 1.0,
            'stop_loss_multiplier': 1.0,
            'use_momentum_signals': False,
            'use_mean_reversion': False,
            'trend_following': False,
            'description': ''
        }
        
        if regime == self.TRENDING_BULLISH:
            modifiers.update({
                'rsi_buy_threshold': 35,      # More lenient (buy even at 35 RSI in strong trend)
                'rsi_sell_threshold': 80,     # Let winners ride (RSI 80+ to sell)
                'position_size_multiplier': 1.0,  # Full size in trending
                'stop_loss_multiplier': 1.5,    # Wider stops in trends
                'use_momentum_signals': True,
                'use_mean_reversion': False,
                'trend_following': True,
                'description': 'Trending bullish - momentum strategy, wider stops'
            })
        elif regime == self.TRENDING_BEARISH:
            modifiers.update({
                'rsi_buy_threshold': 25,      # Only extreme oversold
                'rsi_sell_threshold': 60,     # Sell earlier
                'position_size_multiplier': 0.5,  # Reduced size in downtrend
                'stop_loss_multiplier': 0.7,    # Tighter stops
                'use_momentum_signals': True,
                'use_mean_reversion': False,
                'trend_following': True,
                'description': 'Trending bearish - reduced exposure, momentum shorts'
            })
        elif regime == self.RANGING:
            modifiers.update({
                'rsi_buy_threshold': 30,      # Standard oversold
                'rsi_sell_threshold': 70,     # Standard overbought
                'position_size_multiplier': 0.8,  # Slightly reduced
                'stop_loss_multiplier': 1.0,    # Standard stops
                'use_momentum_signals': False,
                'use_mean_reversion': True,
                'trend_following': False,
                'description': 'Range-bound - mean reversion strategy'
            })
        else:  # TRANSITIONING
            modifiers.update({
                'rsi_buy_threshold': 30,
                'rsi_sell_threshold': 70,
                'position_size_multiplier': 0.5,  # Half size in transition
                'stop_loss_multiplier': 1.0,
                'use_momentum_signals': False,
                'use_mean_reversion': False,
                'trend_following': False,
                'description': 'Transitioning - reduced size, use previous regime'
            })
        
        return modifiers


# Standalone function for quick checks
def get_current_regime(symbol: str = "SPY", db=None) -> Dict:
    """Quick function to get current market regime."""
    classifier = MarketRegimeClassifier(db=db, regime_symbol=symbol)
    return classifier.calculate_current_regime()
