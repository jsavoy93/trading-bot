"""
News Sentiment Integration

Uses Alpha Vantage News Sentiment API to filter signals.
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

# Cache for news sentiment (2 hour TTL)
_news_sentiment_cache = {}
_cache_ttl_seconds = 2 * 60 * 60  # 2 hours


def fetch_news_sentiment(ticker: str, api_key: str = None) -> Optional[Dict]:
    """
    Fetch news sentiment for a ticker from Alpha Vantage.
    
    Args:
        ticker: Stock symbol
        api_key: Alpha Vantage API key
        
    Returns:
        Dict with sentiment data or None on failure
    """
    global _news_sentiment_cache
    
    # Check cache first
    cache_key = ticker.upper()
    if cache_key in _news_sentiment_cache:
        cached_time, cached_data = _news_sentiment_cache[cache_key]
        age = (datetime.now(timezone.utc) - cached_time).total_seconds()
        if age < _cache_ttl_seconds:
            logger.debug(f"Using cached sentiment for {ticker}")
            return cached_data
    
    # Get API key
    if api_key is None:
        import os
        api_key = os.getenv('ALPHA_VANTAGE_API_KEY')
    
    if not api_key:
        logger.warning("Alpha Vantage API key not configured")
        return None
    
    try:
        url = "https://www.alphavantage.co/query"
        params = {
            'function': 'NEWS_SENTIMENT',
            'tickers': ticker,
            'apikey': api_key
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code != 200:
            logger.warning(f"Alpha Vantage API error: {response.status_code}")
            return None
        
        data = response.json()
        
        # Check for API limit or errors
        if 'Note' in data or 'Information' in data:
            logger.warning("Alpha Vantage API rate limit reached")
            return None
        
        if 'feed' not in data:
            logger.debug(f"No news data for {ticker}")
            return None
        
        feed = data['feed']
        
        if not feed:
            return None
        
        # Parse sentiment
        article_count = len(feed)
        
        # Calculate average sentiment
        total_sentiment = 0
        total_relevance = 0
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        
        for article in feed:
            # Alpha Vantage provides sentiment per ticker in each article
            ticker_sentiments = article.get('ticker_sentiment', [])
            for ts in ticker_sentiments:
                if ts.get('ticker', '').upper() == ticker.upper():
                    sentiment = float(ts.get('ticker_sentiment_score', 0))
                    relevance = float(ts.get('relevance_score', 0))
                    
                    total_sentiment += sentiment * relevance
                    total_relevance += relevance
                    
                    if sentiment > 0.1:
                        bullish_count += 1
                    elif sentiment < -0.1:
                        bearish_count += 1
                    else:
                        neutral_count += 1
        
        # Weighted average sentiment
        avg_sentiment = total_sentiment / total_relevance if total_relevance > 0 else 0
        
        # Determine label
        if avg_sentiment > 0.2:
            label = "Bullish"
        elif avg_sentiment > 0.05:
            label = "Somewhat-Bullish"
        elif avg_sentiment < -0.2:
            label = "Bearish"
        elif avg_sentiment < -0.05:
            label = "Somewhat-Bearish"
        else:
            label = "Neutral"
        
        # Get most relevant headline
        top_headline = feed[0].get('title', '') if feed else ''
        
        # News volume ratio (simplified - would need historical average)
        news_volume_ratio = 1.0
        
        result = {
            'ticker': ticker.upper(),
            'article_count_24h': article_count,
            'avg_sentiment_score': round(avg_sentiment, 3),
            'sentiment_label': label,
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'neutral_count': neutral_count,
            'news_volume_ratio': news_volume_ratio,
            'top_headline': top_headline,
            'fetched_at': datetime.now(timezone.utc)
        }
        
        # Cache result
        _news_sentiment_cache[cache_key] = (datetime.now(timezone.utc), result)
        
        # Also save to database
        save_sentiment_to_db(result)
        
        logger.info(f"📰 {ticker} sentiment: {label} ({avg_sentiment:.2f}, {article_count} articles)")
        
        return result
        
    except Exception as e:
        logger.error(f"Error fetching news sentiment for {ticker}: {e}")
        return None


def save_sentiment_to_db(sentiment_data: Dict, db=None) -> bool:
    """Save sentiment to database cache."""
    if db is None:
        from src.database.simple_rest import SimpleSupabaseREST
        db = SimpleSupabaseREST()
    
    if not db.is_available():
        return False
    
    try:
        record = {
            'ticker': sentiment_data['ticker'],
            'article_count': sentiment_data['article_count_24h'],
            'avg_sentiment_score': sentiment_data['avg_sentiment_score'],
            'sentiment_label': sentiment_data['sentiment_label'],
            'bullish_count': sentiment_data['bullish_count'],
            'bearish_count': sentiment_data['bearish_count'],
            'top_headline': sentiment_data.get('top_headline'),
            'fetched_at': sentiment_data['fetched_at'].isoformat()
        }
        
        response = requests.post(
            f"{db.rest_url}/news_sentiment_cache",
            headers=db.headers,
            json=record,
            timeout=10
        )
        
        return response.status_code in [200, 201]
        
    except Exception as e:
        logger.debug(f"Error saving sentiment to DB: {e}")
        return False


def score_news_sentiment(sentiment_data: Dict) -> int:
    """
    Score news sentiment for signal modification.
    
    Args:
        sentiment_data: Output from fetch_news_sentiment()
        
    Returns:
        Score from -100 to +100
    """
    if sentiment_data is None:
        return 0  # Neutral if no data
    
    score = 0
    
    # Base sentiment score (-50 to +50)
    avg_sentiment = sentiment_data.get('avg_sentiment_score', 0)
    score += avg_sentiment * 50  # -50 to +50
    
    # Bullish/Bearish count weighting (-30 to +30)
    bullish = sentiment_data.get('bullish_count', 0)
    bearish = sentiment_data.get('bearish_count', 0)
    total = bullish + bearish
    
    if total > 0:
        bull_bear_ratio = (bullish - bearish) / total
        score += bull_bear_ratio * 30
    
    # News volume amplification (-20 to +20)
    volume_ratio = sentiment_data.get('news_volume_ratio', 1.0)
    if volume_ratio > 2.0:
        score += 20  # High news volume amplifies
    elif volume_ratio > 1.5:
        score += 10
    elif volume_ratio < 0.5:
        score -= 10  # Low news volume
    
    # Clamp to -100 to +100
    score = max(-100, min(100, score))
    
    return int(score)


def filter_signal_by_sentiment(ticker: str, signal: str, 
                              current_price: float = None) -> Tuple[bool, int, str]:
    """
    Filter a trading signal based on news sentiment.
    
    Args:
        ticker: Stock symbol
        signal: 'BUY' or 'SELL'
        current_price: Current price (optional)
        
    Returns:
        (allowed: bool, score: int, reason: str)
    """
    # Get sentiment data
    sentiment = fetch_news_sentiment(ticker)
    
    if sentiment is None:
        # No data - allow with neutral adjustment
        return True, 0, "No news data available"
    
    score = score_news_sentiment(sentiment)
    
    # For BUY signals
    if signal == 'BUY':
        if score < -30:
            # Strong negative sentiment - block
            return False, score, f"Negative sentiment ({sentiment['sentiment_label']})"
        elif score > 30:
            # Strong positive - boost
            return True, score, f"Positive sentiment ({sentiment['sentiment_label']})"
        else:
            # Neutral - allow
            return True, score, f"Neutral sentiment ({sentiment['sentiment_label']})"
    
    # For SELL signals - always allow (exit decisions shouldn't be blocked by news)
    return True, score, "Sell signals always allowed"


# Rate limiting
_daily_request_count = 0
_daily_reset_time = None
_daily_limit = 25  # Alpha Vantage free tier


def check_rate_limit() -> bool:
    """Check if we can make another API call today."""
    global _daily_request_count, _daily_reset_time
    
    now = datetime.now(timezone.utc)
    
    # Reset counter at start of new day
    if _daily_reset_time is None or now.date() > _daily_reset_time.date():
        _daily_request_count = 0
        _daily_reset_time = now
    
    return _daily_request_count < _daily_limit


def increment_rate_limit():
    """Increment the daily request counter."""
    global _daily_request_count
    _daily_request_count += 1
