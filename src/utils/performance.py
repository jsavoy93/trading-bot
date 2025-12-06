"""
Performance monitoring utilities for tracking execution time, API calls, and memory usage.
"""
import time
import logging
import tracemalloc
from functools import wraps
from typing import Dict, Optional, Callable, Any
from collections import defaultdict
from datetime import datetime

class PerformanceMonitor:
    """Singleton class to track performance metrics across the application"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self.metrics: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'call_count': 0,
            'total_time': 0.0,
            'min_time': float('inf'),
            'max_time': 0.0,
            'last_called': None,
            'errors': 0
        })
        
        self.api_metrics: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'call_count': 0,
            'total_time': 0.0,
            'min_time': float('inf'),
            'max_time': 0.0,
            'errors': 0,
            'last_status': None
        })
        
        self.cache_metrics: Dict[str, Dict[str, int]] = defaultdict(lambda: {
            'hits': 0,
            'misses': 0
        })
        
        self.memory_tracking_enabled = False
    
    def record_function_call(self, func_name: str, duration: float, error: bool = False):
        """Record a function call with its duration"""
        metrics = self.metrics[func_name]
        metrics['call_count'] += 1
        metrics['total_time'] += duration
        metrics['min_time'] = min(metrics['min_time'], duration)
        metrics['max_time'] = max(metrics['max_time'], duration)
        metrics['last_called'] = datetime.now()
        
        if error:
            metrics['errors'] += 1
    
    def record_api_call(self, endpoint: str, duration: float, status: Optional[int] = None, error: bool = False):
        """Record an API call with its duration and status"""
        metrics = self.api_metrics[endpoint]
        metrics['call_count'] += 1
        metrics['total_time'] += duration
        metrics['min_time'] = min(metrics['min_time'], duration)
        metrics['max_time'] = max(metrics['max_time'], duration)
        metrics['last_status'] = status
        
        if error:
            metrics['errors'] += 1
    
    def record_cache_hit(self, cache_name: str):
        """Record a cache hit"""
        self.cache_metrics[cache_name]['hits'] += 1
    
    def record_cache_miss(self, cache_name: str):
        """Record a cache miss"""
        self.cache_metrics[cache_name]['misses'] += 1
    
    def get_function_stats(self, func_name: str) -> Dict[str, Any]:
        """Get statistics for a specific function"""
        metrics = self.metrics.get(func_name)
        if not metrics or metrics['call_count'] == 0:
            return {}
        
        avg_time = metrics['total_time'] / metrics['call_count']
        return {
            'calls': metrics['call_count'],
            'total_time': metrics['total_time'],
            'avg_time': avg_time,
            'min_time': metrics['min_time'],
            'max_time': metrics['max_time'],
            'errors': metrics['errors'],
            'error_rate': metrics['errors'] / metrics['call_count'] * 100,
            'last_called': metrics['last_called']
        }
    
    def get_api_stats(self, endpoint: str) -> Dict[str, Any]:
        """Get statistics for a specific API endpoint"""
        metrics = self.api_metrics.get(endpoint)
        if not metrics or metrics['call_count'] == 0:
            return {}
        
        avg_time = metrics['total_time'] / metrics['call_count']
        return {
            'calls': metrics['call_count'],
            'total_time': metrics['total_time'],
            'avg_time': avg_time,
            'min_time': metrics['min_time'],
            'max_time': metrics['max_time'],
            'errors': metrics['errors'],
            'error_rate': metrics['errors'] / metrics['call_count'] * 100,
            'last_status': metrics['last_status']
        }
    
    def get_cache_stats(self, cache_name: str) -> Dict[str, Any]:
        """Get cache hit/miss statistics"""
        metrics = self.cache_metrics.get(cache_name, {})
        hits = metrics.get('hits', 0)
        misses = metrics.get('misses', 0)
        total = hits + misses
        
        if total == 0:
            return {'hits': 0, 'misses': 0, 'hit_rate': 0.0}
        
        return {
            'hits': hits,
            'misses': misses,
            'total': total,
            'hit_rate': (hits / total) * 100
        }
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all performance metrics"""
        summary = {
            'functions': {},
            'apis': {},
            'caches': {}
        }
        
        # Function summaries
        for func_name in self.metrics:
            stats = self.get_function_stats(func_name)
            if stats:
                summary['functions'][func_name] = stats
        
        # API summaries
        for endpoint in self.api_metrics:
            stats = self.get_api_stats(endpoint)
            if stats:
                summary['apis'][endpoint] = stats
        
        # Cache summaries
        for cache_name in self.cache_metrics:
            stats = self.get_cache_stats(cache_name)
            if stats and stats['total'] > 0:
                summary['caches'][cache_name] = stats
        
        return summary
    
    def reset(self):
        """Reset all metrics"""
        self.metrics.clear()
        self.api_metrics.clear()
        self.cache_metrics.clear()
    
    def log_summary(self, top_n: int = 10):
        """Log a summary of top slowest functions and APIs"""
        logging.info("=" * 80)
        logging.info("📊 PERFORMANCE SUMMARY")
        logging.info("=" * 80)
        
        # Top slowest functions by average time
        if self.metrics:
            logging.info("\n🔍 Top Slowest Functions (by avg time):")
            sorted_funcs = sorted(
                [(name, self.get_function_stats(name)) for name in self.metrics],
                key=lambda x: x[1].get('avg_time', 0),
                reverse=True
            )[:top_n]
            
            for func_name, stats in sorted_funcs:
                if stats:
                    logging.info(
                        f"  {func_name}: {stats['avg_time']:.3f}s avg "
                        f"(calls: {stats['calls']}, total: {stats['total_time']:.2f}s, "
                        f"errors: {stats['errors']})"
                    )
        
        # API call statistics
        if self.api_metrics:
            logging.info("\n🌐 API Performance:")
            sorted_apis = sorted(
                [(name, self.get_api_stats(name)) for name in self.api_metrics],
                key=lambda x: x[1].get('calls', 0),
                reverse=True
            )[:top_n]
            
            for endpoint, stats in sorted_apis:
                if stats:
                    logging.info(
                        f"  {endpoint}: {stats['avg_time']:.3f}s avg "
                        f"(calls: {stats['calls']}, errors: {stats['errors']}, "
                        f"error_rate: {stats['error_rate']:.1f}%)"
                    )
        
        # Cache statistics
        if self.cache_metrics:
            logging.info("\n💾 Cache Performance:")
            for cache_name in self.cache_metrics:
                stats = self.get_cache_stats(cache_name)
                if stats and stats['total'] > 0:
                    logging.info(
                        f"  {cache_name}: {stats['hit_rate']:.1f}% hit rate "
                        f"(hits: {stats['hits']}, misses: {stats['misses']})"
                    )
        
        logging.info("=" * 80)


# Global performance monitor instance
perf_monitor = PerformanceMonitor()


def track_performance(log_level: str = 'debug', threshold_ms: Optional[float] = None):
    """
    Decorator to track function execution time and record metrics.
    
    Args:
        log_level: Logging level ('debug', 'info', 'warning'). Default 'debug'.
        threshold_ms: Only log if execution time exceeds this threshold (in milliseconds).
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            error_occurred = False
            
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                error_occurred = True
                raise
            finally:
                duration = time.perf_counter() - start
                perf_monitor.record_function_call(func.__name__, duration, error_occurred)
                
                # Log if threshold is met or no threshold is set
                duration_ms = duration * 1000
                should_log = threshold_ms is None or duration_ms >= threshold_ms
                
                if should_log:
                    log_msg = f"{func.__name__}: {duration:.3f}s ({duration_ms:.1f}ms)"
                    
                    if error_occurred:
                        log_msg += " [ERROR]"
                    
                    # Log at appropriate level
                    if log_level == 'info':
                        logging.info(log_msg)
                    elif log_level == 'warning':
                        logging.warning(log_msg)
                    else:
                        logging.debug(log_msg)
        
        return wrapper
    return decorator


def track_api_call(endpoint_name: str):
    """
    Decorator to track API call latency and status codes.
    
    Args:
        endpoint_name: Name of the API endpoint for tracking
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            error_occurred = False
            status_code = None
            
            try:
                result = func(*args, **kwargs)
                
                # Try to extract status code from response object
                if hasattr(result, 'status_code'):
                    status_code = result.status_code
                elif isinstance(result, tuple) and len(result) > 1:
                    # Handle cases where function returns (data, status_code)
                    status_code = result[1]
                
                return result
            except Exception as e:
                error_occurred = True
                raise
            finally:
                duration = time.perf_counter() - start
                perf_monitor.record_api_call(endpoint_name, duration, status_code, error_occurred)
                
                log_msg = f"API {endpoint_name}: {duration:.3f}s"
                if status_code:
                    log_msg += f" [status: {status_code}]"
                if error_occurred:
                    log_msg += " [ERROR]"
                
                logging.debug(log_msg)
        
        return wrapper
    return decorator


def track_memory(func: Callable) -> Callable:
    """
    Decorator to track memory usage of a function.
    Note: This uses tracemalloc which has some overhead.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        tracemalloc.start()
        
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            
            logging.debug(
                f"{func.__name__}: "
                f"Memory: current={current / 1024 / 1024:.2f}MB, "
                f"peak={peak / 1024 / 1024:.2f}MB"
            )
    
    return wrapper


# Convenience function to get the global monitor
def get_performance_monitor() -> PerformanceMonitor:
    """Get the global performance monitor instance"""
    return perf_monitor
