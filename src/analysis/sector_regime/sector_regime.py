"""
Sector Momentum Regime Detection

Detects whether the market is in a "sector rotation" regime versus
a "broad market" regime, and adjusts sector weights accordingly.
"""
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# SPDR Sector ETFs
SECTOR_ETFS = [
    'XLK',  # Technology
    'XLF',  # Financials
    'XLE',  # Energy
    'XLV',  # Health Care
    'XLI',  # Industrials
    'XLC',  # Communication Services
    'XLY',  # Consumer Discretionary
    'XLP',  # Consumer Staples
    'XLU',  # Utilities
    'XLRE', # Real Estate
    'XLB',  # Materials
]

# GICS Sector to ETF mapping
SECTOR_TO_ETF = {
    'Technology': 'XLK',
    'Financials': 'XLF',
    'Energy': 'XLE',
    'Health Care': 'XLV',
    'Industrials': 'XLI',
    'Communication Services': 'XLC',
    'Consumer Discretionary': 'XLY',
    'Consumer Staples': 'XLP',
    'Utilities': 'XLU',
    'Real Estate': 'XLRE',
    'Materials': 'XLB',
}

# Reverse mapping
ETF_TO_SECTOR = {v: k for k, v in SECTOR_TO_ETF.items()}


def fetch_sector_data(days: int = 60) -> pd.DataFrame:
    """
    Fetch daily closing prices for all 11 sector ETFs.
    
    Args:
        days: Number of trading days to fetch (default: 60)
        
    Returns:
        DataFrame with dates as index and ETF symbols as columns
    """
    import yfinance as yf
    
    try:
        # Download all sector ETFs in one call
        data = yf.download(SECTOR_ETFS, period=f"{days+30}d", progress=False)
        
        if data.empty:
            logger.warning("No sector data returned from yfinance")
            return pd.DataFrame()
        
        # Extract close prices
        if 'Close' in data.columns.get_level_values(0):
            close_prices = data['Close']
        else:
            close_prices = data
        
        # Remove any rows with all NaN
        close_prices = close_prices.dropna(how='all')
        
        logger.info(f"Fetched {len(close_prices)} days of sector data for {len(SECTOR_ETFS)} ETFs")
        return close_prices
        
    except Exception as e:
        logger.error(f"Error fetching sector data: {e}")
        return pd.DataFrame()


def calculate_sector_momentum(prices_df: pd.DataFrame, windows: List[int] = None) -> pd.DataFrame:
    """
    Calculate momentum metrics for each sector ETF.
    
    Args:
        prices_df: DataFrame with ETF prices (dates as index, ETFs as columns)
        windows: List of lookback periods (default: [5, 20, 60])
        
    Returns:
        DataFrame with columns: sector, return_5d, return_20d, return_60d, composite_score
    """
    if windows is None:
        windows = [5, 20, 60]
    
    if prices_df.empty or len(prices_df) < max(windows):
        logger.warning("Insufficient data for momentum calculation")
        return pd.DataFrame()
    
    results = []
    
    for etf in prices_df.columns:
        if etf not in SECTOR_ETFS:
            continue
            
        try:
            prices = prices_df[etf].dropna()
            
            if len(prices) < max(windows):
                continue
            
            # Calculate returns for each window
            return_5d = (prices.iloc[-1] / prices.iloc[-5] - 1) if len(prices) >= 5 else 0
            return_20d = (prices.iloc[-1] / prices.iloc[-20] - 1) if len(prices) >= 20 else 0
            return_60d = (prices.iloc[-1] / prices.iloc[-60] - 1) if len(prices) >= 60 else 0
            
            # Composite score: weighted average
            # (0.5 × return_5d) + (0.3 × return_20d) + (0.2 × return_60d)
            composite_score = (0.5 * return_5d) + (0.3 * return_20d) + (0.2 * return_60d)
            
            results.append({
                'sector': etf,
                'sector_name': ETF_TO_SECTOR.get(etf, etf),
                'return_5d': return_5d,
                'return_20d': return_20d,
                'return_60d': return_60d,
                'composite_score': composite_score,
                'current_price': prices.iloc[-1]
            })
            
        except Exception as e:
            logger.debug(f"Error calculating momentum for {etf}: {e}")
    
    result_df = pd.DataFrame(results)
    
    if not result_df.empty:
        result_df = result_df.sort_values('composite_score', ascending=False)
    
    return result_df


def detect_rotation_regime(momentum_df: pd.DataFrame) -> Dict:
    """
    Detect the current market regime based on sector momentum dispersion.
    
    Args:
        momentum_df: DataFrame with composite_score for each sector
        
    Returns:
        Dict with keys: regime, dispersion, mean_momentum, top_sectors, bottom_sectors, timestamp
    """
    if momentum_df.empty:
        return {
            'regime': 'unknown',
            'dispersion': 0,
            'mean_momentum': 0,
            'top_sectors': [],
            'bottom_sectors': [],
            'timestamp': datetime.now(timezone.utc)
        }
    
    # Calculate cross-sectional standard deviation (dispersion)
    scores = momentum_df['composite_score'].values
    dispersion = np.std(scores)
    mean_momentum = np.mean(scores)
    
    # Determine regime
    if dispersion > 0.03:
        # 3% dispersion threshold - sectors diverging
        regime = 'rotation'
    elif mean_momentum > 0:
        regime = 'broad_bull'
    else:
        regime = 'broad_bear'
    
    # Get top and bottom 3 sectors
    top_sectors = momentum_df.head(3)['sector'].tolist()
    bottom_sectors = momentum_df.tail(3)['sector'].tolist()
    
    result = {
        'regime': regime,
        'dispersion': float(dispersion),
        'mean_momentum': float(mean_momentum),
        'top_sectors': top_sectors,
        'bottom_sectors': bottom_sectors,
        'timestamp': datetime.now(timezone.utc)
    }
    
    logger.info(f"📊 Sector Regime: {regime.upper()} (dispersion: {dispersion:.2%}, mean: {mean_momentum:.2%})")
    logger.info(f"   Top: {', '.join(top_sectors)} | Bottom: {', '.join(bottom_sectors)}")
    
    return result


def get_stock_sector(ticker: str) -> Optional[str]:
    """
    Get the GICS sector for a stock ticker.
    
    Args:
        ticker: Stock symbol
        
    Returns:
        Sector name (e.g., 'Technology') or None if not found
    """
    # Use the existing bot's sector mapping
    from src.core.smart_bot import SmartTradingBot
    try:
        bot = SmartTradingBot()
        sector_etf = bot.get_sector_for_symbol(ticker)
        if sector_etf:
            return ETF_TO_SECTOR.get(sector_etf)
    except Exception as e:
        logger.debug(f"Could not get sector from bot: {e}")
    
    # Fallback: try Yahoo Finance
    try:
        import yfinance as yf
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.info
        sector = info.get('sector')
        if sector:
            return sector
    except Exception as e:
        logger.debug(f"Yahoo Finance lookup failed for {ticker}: {e}")
    
    return None


def map_sector_to_etf(sector: str) -> Optional[str]:
    """
    Map a GICS sector name to the corresponding SPDR ETF.
    
    Args:
        sector: Sector name (e.g., 'Technology')
        
    Returns:
        ETF symbol (e.g., 'XLK') or None if not found
    """
    return SECTOR_TO_ETF.get(sector)


def filter_signal_by_regime(ticker: str, signal: str, regime_info: Dict, 
                            position_size: float = 1.0) -> Tuple[bool, float, str]:
    """
    Filter a trading signal based on sector regime.
    
    Args:
        ticker: Stock symbol
        signal: 'BUY' or 'SELL'
        regime_info: Output from detect_rotation_regime()
        position_size: Base position size multiplier (0.0 to 1.0+)
        
    Returns:
        (allowed: bool, adjusted_size: float, reason: str)
    """
    # SELL signals are never blocked - always allow exits
    if signal == 'SELL':
        return True, position_size, "Sell signals always allowed"
    
    # Get stock's sector
    sector = get_stock_sector(ticker)
    if not sector:
        logger.debug(f"Unknown sector for {ticker}, allowing with neutral adjustment")
        return True, position_size * 0.8, "Unknown sector"
    
    etf = map_sector_to_etf(sector)
    if not etf:
        logger.debug(f"No ETF mapping for sector {sector}")
        return True, position_size * 0.8, "No sector ETF"
    
    regime = regime_info.get('regime', 'unknown')
    
    # In rotation regime: prefer top sectors, avoid bottom
    if regime == 'rotation':
        top = regime_info.get('top_sectors', [])
        bottom = regime_info.get('bottom_sectors', [])
        
        if etf in top:
            return True, position_size * 1.2, f"{etf} is top-performing sector"
        elif etf in bottom:
            # Block or heavily reduce bottom sectors
            return False, 0, f"{etf} is bottom-performing sector (rotation regime)"
        else:
            # Middle sectors - reduce position
            return True, position_size * 0.5, f"{etf} is middle-performing sector"
    
    # In broad_bull: everything rising together, no sector restrictions
    elif regime == 'broad_bull':
        return True, position_size, "Broad bull market - all sectors OK"
    
    # In broad_bear: reduce all positions
    elif regime == 'broad_bear':
        return True, position_size * 0.5, "Broad bear market - reduced exposure"
    
    # Unknown regime
    return True, position_size * 0.8, "Unknown regime"


def get_regime_summary_message(regime_info: Dict, momentum_df: pd.DataFrame) -> str:
    """
    Generate a Telegram-ready summary message for the current regime.
    
    Args:
        regime_info: Output from detect_rotation_regime()
        momentum_df: Sector momentum DataFrame
        
    Returns:
        Formatted message string
    """
    regime = regime_info.get('regime', 'unknown').upper()
    dispersion = regime_info.get('dispersion', 0)
    mean = regime_info.get('mean_momentum', 0)
    top = regime_info.get('top_sectors', [])
    bottom = regime_info.get('bottom_sectors', [])
    
    emoji = {
        'rotation': '🔄',
        'broad_bull': '🚀',
        'broad_bear': '📉',
        'unknown': '❓'
    }
    
    msg = f"""
{emoji.get(regime, '')} **SECTOR REGIME: {regime}**

📊 Dispersion: {dispersion:.1%}
📈 Mean Momentum: {mean:+.1%}

**Top Performers:**
{', '.join(top[:3])}

**Lagging Sectors:**
{', '.join(bottom[:3])}
"""
    
    return msg.strip()


# Standalone function for quick checks
def get_current_regime() -> Dict:
    """Quick function to get current sector regime."""
    prices = fetch_sector_data()
    if prices.empty:
        return {'regime': 'unknown'}
    
    momentum = calculate_sector_momentum(prices)
    return detect_rotation_regime(momentum)
