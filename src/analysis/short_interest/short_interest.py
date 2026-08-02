"""
Short Interest Data Integration

Uses yfinance and Finviz to get short interest data.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

# Cache for 24 hours
_short_interest_cache = {}
_cache_ttl_seconds = 24 * 60 * 60  # 24 hours


def fetch_short_interest(ticker: str) -> Optional[Dict]:
    """
    Fetch short interest data for a ticker.
    
    Args:
        ticker: Stock symbol
        
    Returns:
        Dict with short interest data or None
    """
    global _short_interest_cache
    
    # Check cache
    cache_key = ticker.upper()
    if cache_key in _short_interest_cache:
        cached_time, cached_data = _short_interest_cache[cache_key]
        age = (datetime.now(timezone.utc) - cached_time).total_seconds()
        if age < _cache_ttl_seconds:
            logger.debug(f"Using cached short interest for {ticker}")
            return cached_data
    
    # Try yfinance first
    data = _fetch_from_yfinance(ticker)
    
    if data is None:
        # Fallback to Finviz
        data = _fetch_from_finviz(ticker)
    
    if data:
        # Cache result
        _short_interest_cache[cache_key] = (datetime.now(timezone.utc), data)
        save_short_interest_to_db(data)
    
    return data


def _fetch_from_yfinance(ticker: str) -> Optional[Dict]:
    """Fetch short interest from yfinance."""
    try:
        import yfinance as yf
        
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.info
        
        short_pct = info.get('shortPercentOfFloat')
        
        if short_pct is None:
            return None
        
        return {
            'ticker': ticker.upper(),
            'short_percent_of_float': float(short_pct),
            'short_ratio_days': float(info.get('shortRatio', 0) or 0),
            'shares_short': int(info.get('sharesShort', 0) or 0),
            'shares_short_prior_month': int(info.get('sharesShortPriorMonth', 0) or 0),
            'short_change_pct': 0,  # Would need to calculate from history
            'squeeze_score': 0,  # Calculated separately
            'data_source': 'yfinance',
            'fetched_at': datetime.now(timezone.utc)
        }
        
    except Exception as e:
        logger.debug(f"yfinance failed for {ticker}: {e}")
        return None


def _fetch_from_finviz(ticker: str) -> Optional[Dict]:
    """Fetch short interest from Finviz."""
    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return None
        
        # Parse HTML for short float and short ratio
        import re
        
        html = response.text
        
        # Find short float
        short_float_match = re.search(r'Short Float</td><td class="snapshot-td2">([\d.]+)%', html)
        short_float_pct = float(short_float_match.group(1)) / 100 if short_float_match else None
        
        # Find short ratio
        short_ratio_match = re.search(r'Short Ratio</td><td class="snapshot-td2">([\d.]+)', html)
        short_ratio = float(short_ratio_match.group(1)) if short_ratio_match else None
        
        if short_float_pct is None and short_ratio is None:
            return None
        
        return {
            'ticker': ticker.upper(),
            'short_percent_of_float': short_float_pct or 0,
            'short_ratio_days': short_ratio or 0,
            'shares_short': 0,  # Not available in Finviz
            'shares_short_prior_month': 0,
            'short_change_pct': 0,
            'squeeze_score': 0,
            'data_source': 'finviz',
            'fetched_at': datetime.now(timezone.utc)
        }
        
    except Exception as e:
        logger.debug(f"Finviz failed for {ticker}: {e}")
        return None


def calculate_squeeze_score(short_data: Dict, price_data: Dict = None) -> float:
    """
    Calculate short squeeze potential score.
    
    Args:
        short_data: Output from fetch_short_interest()
        price_data: Dict with rsi, price, sma position (optional)
        
    Returns:
        Score from 0 to 100
    """
    if short_data is None:
        return 0
    
    score = 0
    
    # Short percent weighting (0-30 points)
    short_pct = short_data.get('short_percent_of_float', 0)
    if short_pct > 0.30:
        score += 30
    elif short_pct > 0.20:
        score += 20
    elif short_pct > 0.10:
        score += 10
    
    # Days to cover (0-25 points)
    days_to_cover = short_data.get('short_ratio_days', 0)
    if days_to_cover > 8:
        score += 25
    elif days_to_cover > 5:
        score += 15
    elif days_to_cover > 3:
        score += 10
    
    # Short interest change (0-20 points)
    short_change = short_data.get('short_change_pct', 0)
    if short_change > 20:
        score += 20
    elif short_change > 10:
        score += 10
    
    # Price momentum (if provided)
    if price_data:
        rsi = price_data.get('rsi', 50)
        price = price_data.get('price', 0)
        sma_position = price_data.get('sma_position', 1.0)  # price/sma
        
        # Rising RSI from oversold (0-15 points)
        if rsi and 25 <= rsi <= 40:
            score += 15
        elif rsi and 40 < rsi < 50:
            score += 10
        
        # Price above SMA (0-10 points)
        if sma_position and sma_position > 1.0:
            score += 10
    
    return min(100, score)


def filter_by_short_interest(ticker: str, signal: str, 
                            current_price: float = None) -> Tuple[bool, float, str]:
    """
    Filter signal based on short interest.
    
    Args:
        ticker: Stock symbol
        signal: 'BUY' or 'SELL'
        current_price: Current price (optional)
        
    Returns:
        (allowed: bool, squeeze_score: float, reason: str)
    """
    short_data = fetch_short_interest(ticker)
    
    if short_data is None:
        return True, 0, "No short data available"
    
    # Get price data if we have current price
    price_data = None
    if current_price:
        # Could fetch RSI here in production
        price_data = {'price': current_price}
    
    squeeze_score = calculate_squeeze_score(short_data, price_data)
    short_pct = short_data.get('short_percent_of_float', 0)
    
    # For BUY signals
    if signal == 'BUY':
        # High short + bearish momentum = block
        if short_pct > 0.30 and price_data and price_data.get('rsi', 50) < 40:
            return False, squeeze_score, f"High short ({short_pct:.1%}) with bearish momentum"
        
        # High squeeze potential = boost
        if squeeze_score > 60:
            return True, squeeze_score, f"Short squeeze potential ({squeeze_score:.0f})"
        
        return True, squeeze_score, f"Normal short interest ({short_pct:.1%})"
    
    # For SELL - no blocking
    return True, squeeze_score, "Sell signals allowed"


def save_short_interest_to_db(data: Dict, db=None) -> bool:
    """Save short interest to database."""
    if db is None:
        from src.database.simple_rest import SimpleSupabaseREST
        db = SimpleSupabaseREST()
    
    if not db.is_available():
        return False
    
    try:
        record = {
            'ticker': data['ticker'],
            'short_percent_of_float': data['short_percent_of_float'],
            'short_ratio_days': data['short_ratio_days'],
            'shares_short': data.get('shares_short', 0),
            'data_source': data['data_source'],
            'fetched_at': data['fetched_at'].isoformat()
        }
        
        response = requests.post(
            f"{db.rest_url}/short_interest_cache",
            headers=db.headers,
            json=record,
            timeout=10
        )
        
        return response.status_code in [200, 201]
        
    except Exception as e:
        logger.debug(f"Error saving short interest: {e}")
        return False
