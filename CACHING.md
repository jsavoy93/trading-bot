# Response Caching System

Comprehensive time-based caching to reduce API calls and improve performance by 50-70%.

## Overview

The caching system uses TTL (Time-To-Live) to automatically expire old data while keeping frequently accessed data readily available. Thread-safe implementation ensures reliability in concurrent scenarios.

## Architecture

### Cache Manager (`src/utils/cache.py`)

Centralized management of multiple caches with different TTL settings:

- **Market Data Cache** (60s TTL) - Stock prices, bars, quotes
- **AI Analysis Cache** (18h TTL) - Expensive AI-powered ticker analysis  
- **News Cache** (24h TTL) - News articles (static once published)
- **Portfolio Cache** (5m TTL) - Portfolio positions and balances
- **Database Cache** (2m TTL) - Database query results
- **Indicators Cache** (30s TTL) - Technical indicators (RSI, SMA)

### Features

✅ **Automatic TTL Expiration** - Old data automatically removed  
✅ **Thread-Safe** - Safe for concurrent access  
✅ **LRU Eviction** - Removes least recently used items when full  
✅ **Performance Tracking** - Hit/miss rates automatically recorded  
✅ **Flexible Key Generation** - Custom keys or automatic hashing  

## Quick Start

### Using the @cached Decorator

Simplest way to add caching to any function:

```python
from utils.cache import cached

# Cache market data for 60 seconds
@cached('market_data', key_func=lambda symbol: symbol)
def get_stock_price(symbol):
    # Expensive API call
    return alpaca.get_latest_trade(symbol)

# First call - hits API
price1 = get_stock_price('AAPL')  # MISS - calls API

# Second call within 60s - uses cache
price2 = get_stock_price('AAPL')  # HIT - from cache!
```

### Using Cache Directly

For more control:

```python
from utils.cache import cache_manager

# Get from cache
cached_data = cache_manager.market_data.get('AAPL_1D_bars')

if cached_data is None:
    # Cache miss - fetch from API
    data = fetch_market_data('AAPL', '1D')
    
    # Store in cache
    cache_manager.market_data.set('AAPL_1D_bars', data)
else:
    # Cache hit - use cached data
    data = cached_data
```

## Predefined Caches

### Market Data Cache
```python
cache_manager.market_data.set(f"{symbol}_bars", bars_data)
cached = cache_manager.market_data.get(f"{symbol}_bars")
```
- **TTL**: 60 seconds
- **Max Size**: 500 entries
- **Use For**: Stock prices, bars, quotes, latest trades

### AI Analysis Cache
```python
cache_manager.ai_analysis.set(f"analysis_{symbol}", analysis_result)
cached = cache_manager.ai_analysis.get(f"analysis_{symbol}")
```
- **TTL**: 18 hours (64,800 seconds)
- **Max Size**: 200 entries  
- **Use For**: AI ticker analysis, sentiment analysis, research summaries

### News Cache
```python
cache_manager.news.set(f"news_{symbol}_{date}", articles)
cached = cache_manager.news.get(f"news_{symbol}_{date}")
```
- **TTL**: 24 hours (86,400 seconds)
- **Max Size**: 1000 entries
- **Use For**: News articles, headlines, market news

### Portfolio Cache
```python
cache_manager.portfolio.set('positions', positions_list)
cached = cache_manager.portfolio.get('positions')
```
- **TTL**: 5 minutes (300 seconds)
- **Max Size**: 100 entries
- **Use For**: Portfolio positions, account balance, buying power

### Database Cache
```python
cache_manager.database.set('cooldown_AAPL', cooldown_data)
cached = cache_manager.database.get('cooldown_AAPL')
```
- **TTL**: 2 minutes (120 seconds)
- **Max Size**: 500 entries
- **Use For**: Database query results, cooldown checks

### Indicators Cache
```python
cache_manager.indicators.set(f"RSI_{symbol}", rsi_value)
cached = cache_manager.indicators.get(f"RSI_{symbol}")
```
- **TTL**: 30 seconds
- **Max Size**: 300 entries
- **Use For**: RSI, SMA, technical indicators

## Decorator Usage

### Basic Usage
```python
@cached('market_data')
def expensive_function(arg1, arg2):
    # Cache key automatically generated from function name + args
    return perform_expensive_operation(arg1, arg2)
```

### Custom Key Function
```python
@cached('market_data', key_func=lambda symbol, tf: f"{symbol}_{tf}")
def get_bars(symbol, timeframe):
    return api.get_bars(symbol, timeframe)
```

### Custom TTL
```python
@cached('custom_cache', ttl_seconds=300)
def special_function():
    # Creates temporary cache with 5-minute TTL
    return expensive_calculation()
```

## Cache Management

### View Statistics
```bash
python view_cache.py
```

Output example:
```
================================================================================
💾 TRADING BOT CACHE STATISTICS
================================================================================

📈 OVERALL STATISTICS:
   Total Cache Entries: 127
   Total Hits: 842
   Total Misses: 235
   Overall Hit Rate: 78.2%

📊 INDIVIDUAL CACHE PERFORMANCE:
--------------------------------------------------------------------------------
Cache Name           Size         TTL          Hit Rate     Hits/Misses    
--------------------------------------------------------------------------------
ai_analysis          23/200       18.0h        92.3%        120/10         
market_data          85/500       1.0m         76.5%        612/187        
news                 19/1000      24.0h        88.0%        110/38         

💡 RECOMMENDATIONS:
✅ ai_analysis: Excellent hit rate (92.3%)
   - Cache is working effectively!
```

### Cleanup Expired Entries
```python
from utils.cache import cache_manager

# Cleanup all caches
results = cache_manager.cleanup_all()
print(f"Removed {sum(results.values())} expired entries")
```

### Clear Specific Cache
```python
# Clear one cache
cache_manager.market_data.clear()

# Clear all caches
cache_manager.clear_all()
```

### Get Statistics
```python
# Get stats for one cache
stats = cache_manager.market_data.get_stats()
print(f"Hit rate: {stats['hit_rate']:.1f}%")

# Get stats for all caches
all_stats = cache_manager.get_all_stats()
```

## Best Practices

### 1. Choose Appropriate TTL

**Short TTL (< 1 minute):**
- Frequently changing data (stock prices, market data)
- Data where freshness is critical

**Medium TTL (1-30 minutes):**
- Semi-stable data (portfolio positions, database queries)
- Balance between freshness and performance

**Long TTL (hours/days):**
- Expensive computations (AI analysis)
- Static data (historical news articles)

### 2. Design Good Cache Keys

```python
# Good - specific and unique
key = f"{symbol}_{timeframe}_{date}"

# Bad - too generic, will collide
key = symbol

# Good - includes all parameters
key = f"analysis_{symbol}_{strategy}_{timestamp}"
```

### 3. Handle Cache Misses Gracefully

```python
def get_data(symbol):
    # Try cache first
    data = cache_manager.market_data.get(symbol)
    
    if data is None:
        # Cache miss - fetch from source
        try:
            data = fetch_from_api(symbol)
            cache_manager.market_data.set(symbol, data)
        except Exception as e:
            logging.error(f"Failed to fetch data: {e}")
            return None
    
    return data
```

### 4. Invalidate When Needed

```python
# After placing a trade, invalidate portfolio cache
def place_order(symbol, qty):
    order = submit_order(symbol, qty)
    
    # Invalidate so next call gets fresh data
    cache_manager.portfolio.delete('positions')
    cache_manager.portfolio.delete('account_balance')
    
    return order
```

### 5. Monitor Cache Performance

Regularly check cache statistics to ensure caches are effective:

```python
# In periodic summary (e.g., every 50 loops)
cache_manager.print_stats()
```

## Integration Examples

### Example 1: Caching Market Data

```python
from utils.cache import cached, cache_manager

@cached('market_data', key_func=lambda symbol, timeframe: f"{symbol}_{timeframe}")
def get_market_bars(symbol, timeframe='1Day'):
    """Get market bars with automatic caching"""
    return data_client.get_stock_bars(
        symbol=symbol,
        timeframe=timeframe,
        limit=100
    )

# Usage
bars = get_market_bars('AAPL', '1Day')  # First call - API hit
bars = get_market_bars('AAPL', '1Day')  # Second call - cached!
```

### Example 2: Caching AI Analysis

```python
def analyze_ticker_with_cache(symbol):
    """Analyze ticker with 18-hour cache"""
    cache_key = f"analysis_{symbol}"
    
    # Try cache first
    cached_analysis = cache_manager.ai_analysis.get(cache_key)
    if cached_analysis:
        logging.info(f"Using cached analysis for {symbol}")
        return cached_analysis
    
    # Cache miss - do expensive AI analysis
    logging.info(f"Performing new AI analysis for {symbol}")
    analysis = expensive_ai_analysis(symbol)
    
    # Cache for 18 hours
    cache_manager.ai_analysis.set(cache_key, analysis)
    
    return analysis
```

### Example 3: Caching Portfolio Data

```python
def get_portfolio_with_cache():
    """Get portfolio with 5-minute cache"""
    cached = cache_manager.portfolio.get('current_portfolio')
    
    if cached:
        return cached
    
    # Fetch fresh data
    portfolio = {
        'positions': trading_client.get_all_positions(),
        'account': trading_client.get_account(),
        'timestamp': datetime.now()
    }
    
    cache_manager.portfolio.set('current_portfolio', portfolio)
    return portfolio
```

## Performance Impact

### Expected Improvements

Based on typical trading bot usage patterns:

- **Market Data**: 40-60% hit rate (moderate reuse within TTL)
- **AI Analysis**: 85-95% hit rate (expensive, rarely changes)
- **News**: 70-80% hit rate (static content, frequently accessed)
- **Portfolio**: 60-70% hit rate (checked frequently)

### Overall Impact

- **API Call Reduction**: 50-70% fewer API calls
- **Response Time**: 10-100x faster for cache hits (no network latency)
- **Cost Savings**: Reduced API usage = lower costs
- **Rate Limit Protection**: Fewer calls = less likely to hit rate limits

## Monitoring & Debugging

### Enable Cache Logging

Set log level to DEBUG to see cache operations:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Output:
```
DEBUG:root:Cache HIT [market_data]: AAPL_1Day (age: 23.4s)
DEBUG:root:Cache MISS [market_data]: GOOGL_1Day
DEBUG:root:Cache SET [market_data]: GOOGL_1Day
DEBUG:root:Cache EXPIRED [ai_analysis]: TSLA_analysis (age: 64801.2s)
```

### View Live Statistics

```python
from utils.cache import cache_manager

# Print current statistics
cache_manager.print_stats()
```

### Check Specific Cache

```python
stats = cache_manager.market_data.get_stats()
print(f"Market data cache:")
print(f"  Size: {stats['size']}/{stats['max_size']}")
print(f"  Hit rate: {stats['hit_rate']:.1f}%")
print(f"  Total requests: {stats['total_requests']}")
```

## Troubleshooting

### Low Hit Rate

**Cause**: TTL too short or cache keys not matching  
**Solution**: Increase TTL or review key generation logic

### High Memory Usage

**Cause**: Caches growing too large  
**Solution**: Reduce max_size or implement more aggressive cleanup

### Stale Data

**Cause**: TTL too long for rapidly changing data  
**Solution**: Reduce TTL or invalidate cache on updates

### Cache Thrashing

**Cause**: max_size too small, constant evictions  
**Solution**: Increase max_size for frequently-used caches

## Advanced Usage

### Creating Custom Caches

```python
from utils.cache import CacheWithTTL

# Create custom cache
my_cache = CacheWithTTL(
    name='my_custom_cache',
    ttl_seconds=600,  # 10 minutes
    max_size=100
)

# Use it
my_cache.set('key', 'value')
result = my_cache.get('key')
```

### Conditional Caching

```python
def get_data(symbol, use_cache=True):
    if use_cache:
        cached = cache_manager.market_data.get(symbol)
        if cached:
            return cached
    
    data = fetch_fresh_data(symbol)
    
    if use_cache:
        cache_manager.market_data.set(symbol, data)
    
    return data
```

### Cache Warmup

```python
def warmup_cache(symbols):
    """Pre-populate cache with data for common symbols"""
    for symbol in symbols:
        data = fetch_market_data(symbol)
        cache_manager.market_data.set(symbol, data)
    
    logging.info(f"Warmed up cache with {len(symbols)} symbols")
```

## Integration with Performance Monitoring

Cache statistics automatically integrate with the performance monitoring system:

```python
from utils.performance import perf_monitor

# Cache hits/misses automatically recorded
# View in performance summary
perf_monitor.log_summary()
```

Output includes cache stats:
```
💾 Cache Performance:
  market_data: 76.5% hit rate (hits: 612, misses: 187)
  ai_analysis: 92.3% hit rate (hits: 120, misses: 10)
```
