# Performance Monitoring System

The trading bot now includes comprehensive performance monitoring to help identify bottlenecks, track API latency, and optimize execution.

## Features

### 1. Function Execution Time Tracking
- Automatically tracks execution time for decorated functions
- Records min, max, average, and total execution time
- Counts function calls and errors
- Configurable logging thresholds

### 2. API Call Latency Monitoring
- Tracks all database API calls (Supabase)
- Records response times and status codes
- Monitors error rates per endpoint
- Identifies slow API endpoints

### 3. Cache Hit/Miss Tracking
- Monitor cache efficiency
- Calculate hit rates
- Identify cache optimization opportunities

### 4. Memory Usage Profiling
- Optional memory tracking for functions
- Peak and current memory usage
- Uses `tracemalloc` for detailed profiling

## Usage

### Decorators

#### Track Function Performance
```python
from src.utils.performance import track_performance

@track_performance(log_level='debug', threshold_ms=100)
def my_function():
    # Only logs if execution takes > 100ms
    pass

@track_performance(log_level='info')
def critical_function():
    # Always logs at INFO level
    pass
```

#### Track API Calls
```python
from src.utils.performance import track_api_call

@track_api_call('alpaca_get_bars')
def fetch_market_data(symbol):
    # Automatically tracks API call latency
    response = alpaca.get_bars(symbol)
    return response
```

#### Track Memory Usage
```python
from src.utils.performance import track_memory

@track_memory
def memory_intensive_function():
    # Logs memory usage
    large_data = process_data()
    return large_data
```

### View Performance Metrics

#### During Bot Execution
Performance metrics are automatically displayed in the summary every N loops (default: 50).

The summary shows:
- ⚡ Top 5 slowest functions by average time
- 🌐 Top 5 API endpoints by call count
- Error rates for functions and APIs

#### View Live Metrics
```bash
python view_performance.py
```

This displays:
- All tracked functions with call counts and timing
- All API endpoints with latency statistics
- Cache hit/miss rates
- Performance insights and recommendations

#### Programmatic Access
```python
from src.utils.performance import get_performance_monitor

monitor = get_performance_monitor()

# Get stats for specific function
stats = monitor.get_function_stats('analyze_symbol')
print(f"Average time: {stats['avg_time']:.3f}s")
print(f"Call count: {stats['calls']}")
print(f"Error rate: {stats['error_rate']:.1f}%")

# Get API stats
api_stats = monitor.get_api_stats('supabase_get_research_cooldown')
print(f"API calls: {api_stats['calls']}")
print(f"Avg latency: {api_stats['avg_time']*1000:.1f}ms")

# Get complete summary
summary = monitor.get_summary()

# Log comprehensive summary
monitor.log_summary(top_n=10)

# Reset all metrics
monitor.reset()
```

## Already Instrumented

### Database Operations (simple_rest.py)
All database methods are now tracked:
- `create_session` - INFO level, tracks session creation time
- `log_trade` - Threshold 100ms, logs slow trade writes
- `update_session` - Threshold 100ms
- `get_research_cooldown` - Threshold 50ms
- `set_research_cooldown` - Threshold 100ms
- `get_position_sell_cooldown` - Threshold 50ms
- `set_position_sell_cooldown` - Threshold 100ms
- `get_trade_cooldown` - Threshold 50ms
- `set_trade_cooldown` - Threshold 100ms

Each method also has `@track_api_call` to monitor Supabase API latency.

## Performance Benefits

### Implemented So Far:
1. ✅ **Connection Pooling** (Improvement #3) - 2-3x faster DB operations
2. ✅ **Performance Monitoring** (Improvement #4) - Data-driven optimization

### Expected Impact:
- **Visibility**: Identify bottlenecks in real-time
- **Optimization**: Data-driven decisions on what to optimize
- **Debugging**: Quickly spot performance regressions
- **API Usage**: Monitor API call patterns and quotas

## Adding Monitoring to New Code

### For New Functions
```python
@track_performance(threshold_ms=50)
def new_analysis_function(symbol):
    # Your code here
    return result
```

### For New API Calls
```python
@track_api_call('new_api_endpoint')
def call_external_api():
    response = requests.get('https://api.example.com/data')
    return response
```

### For Cache Operations
```python
from src.utils.performance import perf_monitor

def get_cached_data(key):
    if key in cache:
        perf_monitor.record_cache_hit('my_cache')
        return cache[key]
    else:
        perf_monitor.record_cache_miss('my_cache')
        data = fetch_data(key)
        cache[key] = data
        return data
```

## Configuration

### Logging Levels
- `debug` - Only shown when debug logging is enabled (default)
- `info` - Always visible in normal operation
- `warning` - For slow operations that need attention

### Thresholds
Use `threshold_ms` parameter to only log operations exceeding a time limit:
```python
@track_performance(threshold_ms=100)  # Only log if > 100ms
```

### Disable Monitoring
To disable performance monitoring, simply don't import or use the decorators. The system has zero overhead when not used.

## Metrics Persistence

Currently, metrics are stored in memory and reset when the bot restarts. For long-running analysis:

1. Use `monitor.get_summary()` to get all metrics
2. Save to JSON/CSV if needed
3. Use `monitor.reset()` to clear metrics between sessions

## Next Steps

Consider implementing:
- Metrics export to CSV/JSON
- Historical performance tracking
- Performance alerts (e.g., email if avg time > threshold)
- Integration with monitoring services (Datadog, New Relic, etc.)
- Automatic bottleneck detection and recommendations

## Troubleshooting

### No metrics showing
- Ensure functions are decorated with `@track_performance()`
- Check that performance module is imported correctly
- Verify logging level is set appropriately

### High overhead
- Use `threshold_ms` to reduce logging
- Avoid `@track_memory` on frequently-called functions
- Consider sampling (only track every Nth call)

### Import errors
The module has fallback decorators that do nothing if imports fail, so the bot continues working even if performance module is unavailable.
