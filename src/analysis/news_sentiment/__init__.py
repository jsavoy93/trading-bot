"""
News Sentiment Module

Alpha Vantage news sentiment integration.
"""
from .news_sentiment import (
    fetch_news_sentiment,
    score_news_sentiment,
    filter_signal_by_sentiment,
    check_rate_limit,
    increment_rate_limit,
)

__all__ = [
    'fetch_news_sentiment',
    'score_news_sentiment',
    'filter_signal_by_sentiment',
    'check_rate_limit',
    'increment_rate_limit',
]
