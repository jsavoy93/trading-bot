"""
Time-based caching system with TTL (Time-To-Live) support.
Reduces API calls and improves performance.
"""
import time
import logging
from datetime import datetime, timedelta
from typing import Any, Optional, Dict, Callable
from functools import wraps
from threading import Lock
import hashlib
import json

try:
    from utils.performance import perf_monitor
except ImportError:
    perf_monitor = None


class CacheWithTTL:
    """
    Thread-safe cache with Time-To-Live (TTL) support.
    
    Automatically expires entries after the specified TTL period.
    Tracks cache hits/misses for performance monitoring.
    """
    
    def __init__(self, name: str, ttl_seconds: int, max_size: int = 1000):
        """
        Initialize cache.
        
        Args:
            name: Cache name for monitoring/logging
            ttl_seconds: Time-to-live in seconds
            max_size: Maximum number of entries (LRU eviction when full)
        """
        self.name = name
        self.ttl = ttl_seconds
        self.max_size = max_size
        self.cache: Dict[str, tuple[Any, float]] = {}  # key -> (value, timestamp)
        self.access_times: Dict[str, float] = {}  # key -> last_access_time
        self.lock = Lock()
        
        # Statistics
        self.hits = 0
        self.misses = 0
        self.evictions = 0
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache if not expired.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value if valid, None otherwise
        """
        with self.lock:
            if key not in self.cache:
                self.misses += 1
                if perf_monitor:
                    perf_monitor.record_cache_miss(self.name)
                logging.debug(f"Cache MISS [{self.name}]: {key}")
                return None
            
            value, timestamp = self.cache[key]
            age = time.time() - timestamp
            
            if age >= self.ttl:
                # Expired
                del self.cache[key]
                if key in self.access_times:
                    del self.access_times[key]
                self.misses += 1
                if perf_monitor:
                    perf_monitor.record_cache_miss(self.name)
                logging.debug(f"Cache EXPIRED [{self.name}]: {key} (age: {age:.1f}s)")
                return None
            
            # Valid cache hit
            self.hits += 1
            self.access_times[key] = time.time()
            if perf_monitor:
                perf_monitor.record_cache_hit(self.name)
            logging.debug(f"Cache HIT [{self.name}]: {key} (age: {age:.1f}s)")
            return value
    
    def set(self, key: str, value: Any) -> None:
        """
        Store value in cache with current timestamp.
        
        Args:
            key: Cache key
            value: Value to cache
        """
        with self.lock:
            # Check if cache is full
            if len(self.cache) >= self.max_size and key not in self.cache:
                self._evict_lru()
            
            self.cache[key] = (value, time.time())
            self.access_times[key] = time.time()
            logging.debug(f"Cache SET [{self.name}]: {key}")
    
    def _evict_lru(self) -> None:
        """Evict least recently used entry"""
        if not self.access_times:
            return
        
        # Find least recently accessed key
        lru_key = min(self.access_times.items(), key=lambda x: x[1])[0]
        
        if lru_key in self.cache:
            del self.cache[lru_key]
        del self.access_times[lru_key]
        self.evictions += 1
        logging.debug(f"Cache EVICT [{self.name}]: {lru_key}")
    
    def delete(self, key: str) -> None:
        """
        Remove key from cache.
        
        Args:
            key: Cache key to remove
        """
        with self.lock:
            if key in self.cache:
                del self.cache[key]
            if key in self.access_times:
                del self.access_times[key]
            logging.debug(f"Cache DELETE [{self.name}]: {key}")
    
    def clear(self) -> None:
        """Clear all cache entries"""
        with self.lock:
            self.cache.clear()
            self.access_times.clear()
            logging.info(f"Cache CLEARED [{self.name}]")
    
    def cleanup_expired(self) -> int:
        """
        Remove all expired entries.
        
        Returns:
            Number of entries removed
        """
        with self.lock:
            current_time = time.time()
            expired_keys = [
                key for key, (_, timestamp) in self.cache.items()
                if current_time - timestamp >= self.ttl
            ]
            
            for key in expired_keys:
                del self.cache[key]
                if key in self.access_times:
                    del self.access_times[key]
            
            if expired_keys:
                logging.debug(f"Cache CLEANUP [{self.name}]: Removed {len(expired_keys)} expired entries")
            
            return len(expired_keys)
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        with self.lock:
            total_requests = self.hits + self.misses
            hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0
            
            return {
                'name': self.name,
                'size': len(self.cache),
                'max_size': self.max_size,
                'ttl_seconds': self.ttl,
                'hits': self.hits,
                'misses': self.misses,
                'hit_rate': hit_rate,
                'evictions': self.evictions,
                'total_requests': total_requests
            }
    
    def __len__(self) -> int:
        """Get number of entries in cache"""
        return len(self.cache)


class CacheManager:
    """
    Centralized cache manager for the trading bot.
    Manages multiple caches with different TTL settings.
    """
    
    def __init__(self):
        """Initialize cache manager with predefined caches"""
        self.caches: Dict[str, CacheWithTTL] = {}
        
        # Market data cache (1 minute - data changes frequently)
        self.market_data = self._create_cache('market_data', ttl_seconds=60, max_size=500)
        
        # AI analysis cache (18 hours - analysis is expensive and relatively stable)
        self.ai_analysis = self._create_cache('ai_analysis', ttl_seconds=18*3600, max_size=200)
        
        # News articles cache (24 hours - news is static once published)
        self.news = self._create_cache('news', ttl_seconds=24*3600, max_size=1000)
        
        # Portfolio positions cache (5 minutes - positions don't change that often)
        self.portfolio = self._create_cache('portfolio', ttl_seconds=300, max_size=100)
        
        # Database query cache (2 minutes - balance between freshness and performance)
        self.database = self._create_cache('database', ttl_seconds=120, max_size=500)
        
        # Technical indicators cache (30 seconds - derived from market data)
        self.indicators = self._create_cache('indicators', ttl_seconds=30, max_size=300)
    
    def _create_cache(self, name: str, ttl_seconds: int, max_size: int) -> CacheWithTTL:
        """Create and register a new cache"""
        cache = CacheWithTTL(name, ttl_seconds, max_size)
        self.caches[name] = cache
        return cache
    
    def get_cache(self, name: str) -> Optional[CacheWithTTL]:
        """Get cache by name"""
        return self.caches.get(name)
    
    def cleanup_all(self) -> Dict[str, int]:
        """
        Cleanup expired entries in all caches.
        
        Returns:
            Dictionary with cleanup counts per cache
        """
        results = {}
        for name, cache in self.caches.items():
            count = cache.cleanup_expired()
            if count > 0:
                results[name] = count
        
        if results:
            logging.info(f"Cache cleanup: {results}")
        
        return results
    
    def clear_all(self) -> None:
        """Clear all caches"""
        for cache in self.caches.values():
            cache.clear()
        logging.info("All caches cleared")
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all caches"""
        return {name: cache.get_stats() for name, cache in self.caches.items()}
    
    def print_stats(self) -> None:
        """Print cache statistics to console"""
        print("\n" + "="*80)
        print("💾 CACHE STATISTICS")
        print("="*80)
        
        stats = self.get_all_stats()
        
        for name, cache_stats in stats.items():
            if cache_stats['total_requests'] == 0:
                continue  # Skip unused caches
            
            print(f"\n📊 {name.upper().replace('_', ' ')} Cache:")
            print(f"   Size: {cache_stats['size']}/{cache_stats['max_size']} entries")
            print(f"   TTL: {cache_stats['ttl_seconds']}s ({cache_stats['ttl_seconds']/3600:.1f}h)")
            print(f"   Hits: {cache_stats['hits']}, Misses: {cache_stats['misses']}")
            print(f"   Hit Rate: {cache_stats['hit_rate']:.1f}%")
            if cache_stats['evictions'] > 0:
                print(f"   Evictions: {cache_stats['evictions']}")
        
        print("="*80 + "\n")


# Global cache manager instance
cache_manager = CacheManager()


def cached(cache_name: str, key_func: Optional[Callable] = None, ttl_seconds: Optional[int] = None):
    """
    Decorator to cache function results.
    
    Args:
        cache_name: Name of cache to use ('market_data', 'ai_analysis', etc.)
        key_func: Optional function to generate cache key from args/kwargs
        ttl_seconds: Optional custom TTL (creates temporary cache if not using predefined cache)
    
    Example:
        @cached('market_data', key_func=lambda symbol, timeframe: f"{symbol}_{timeframe}")
        def get_bars(symbol, timeframe):
            return expensive_api_call(symbol, timeframe)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Get or create cache
            cache = cache_manager.get_cache(cache_name)
            if cache is None and ttl_seconds:
                # Create temporary cache
                cache = CacheWithTTL(f"temp_{cache_name}", ttl_seconds)
            
            if cache is None:
                # No cache available, just call function
                return func(*args, **kwargs)
            
            # Generate cache key
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # Default: hash args and kwargs
                key_parts = [func.__name__]
                key_parts.extend(str(arg) for arg in args)
                key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
                cache_key = hashlib.md5("_".join(key_parts).encode()).hexdigest()
            
            # Try to get from cache
            result = cache.get(cache_key)
            if result is not None:
                return result
            
            # Cache miss - call function and store result
            result = func(*args, **kwargs)
            cache.set(cache_key, result)
            
            return result
        
        return wrapper
    return decorator


def get_cache_manager() -> CacheManager:
    """Get the global cache manager instance"""
    return cache_manager
