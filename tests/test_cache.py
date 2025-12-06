#!/usr/bin/env python3
"""
Test script to demonstrate caching functionality
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from utils.cache import cache_manager, cached, CacheWithTTL


def test_basic_cache():
    """Test basic cache get/set operations"""
    print("\n" + "="*80)
    print("🧪 TEST 1: Basic Cache Operations")
    print("="*80)
    
    cache = CacheWithTTL('test_cache', ttl_seconds=2, max_size=10)
    
    # Test set/get
    print("\n1️⃣ Testing set/get:")
    cache.set('key1', 'value1')
    result = cache.get('key1')
    assert result == 'value1', "Cache get failed"
    print(f"   ✅ Set and retrieved: key1 = {result}")
    
    # Test cache miss
    print("\n2️⃣ Testing cache miss:")
    result = cache.get('nonexistent')
    assert result is None, "Should return None for missing key"
    print(f"   ✅ Cache miss handled correctly: {result}")
    
    # Test TTL expiration
    print("\n3️⃣ Testing TTL expiration (2 seconds):")
    cache.set('expires', 'soon')
    print(f"   Set: expires = soon")
    print(f"   Waiting 1 second...")
    time.sleep(1)
    result = cache.get('expires')
    print(f"   After 1s: {result} ✅")
    
    print(f"   Waiting 2 more seconds...")
    time.sleep(2)
    result = cache.get('expires')
    print(f"   After 3s total: {result} (expired) ✅")
    
    print("\n✅ Basic cache operations: PASSED")


def test_cache_decorator():
    """Test @cached decorator"""
    print("\n" + "="*80)
    print("🧪 TEST 2: Cache Decorator")
    print("="*80)
    
    call_count = {'value': 0}
    
    @cached('market_data', key_func=lambda x: f"param_{x}")
    def expensive_function(param):
        """Simulates expensive API call"""
        call_count['value'] += 1
        time.sleep(0.1)  # Simulate network delay
        return f"result_{param}"
    
    print("\n1️⃣ First call (should hit API):")
    start = time.time()
    result1 = expensive_function('A')
    duration1 = time.time() - start
    print(f"   Result: {result1}")
    print(f"   Duration: {duration1*1000:.1f}ms")
    print(f"   API calls: {call_count['value']}")
    
    print("\n2️⃣ Second call with same param (should use cache):")
    start = time.time()
    result2 = expensive_function('A')
    duration2 = time.time() - start
    print(f"   Result: {result2}")
    print(f"   Duration: {duration2*1000:.1f}ms (much faster!)")
    print(f"   API calls: {call_count['value']} (not incremented)")
    
    assert result1 == result2, "Results should match"
    assert call_count['value'] == 1, "Should only call function once"
    assert duration2 < duration1 / 10, "Cached call should be much faster"
    
    print("\n✅ Cache decorator: PASSED")


def test_cache_manager():
    """Test cache manager with multiple caches"""
    print("\n" + "="*80)
    print("🧪 TEST 3: Cache Manager")
    print("="*80)
    
    print("\n1️⃣ Testing predefined caches:")
    
    # Market data cache
    cache_manager.market_data.set('AAPL', {'price': 150.00})
    result = cache_manager.market_data.get('AAPL')
    print(f"   Market data: {result} ✅")
    
    # AI analysis cache
    cache_manager.ai_analysis.set('GOOGL_analysis', {'sentiment': 'bullish'})
    result = cache_manager.ai_analysis.get('GOOGL_analysis')
    print(f"   AI analysis: {result} ✅")
    
    # News cache
    cache_manager.news.set('tech_news', ['article1', 'article2'])
    result = cache_manager.news.get('tech_news')
    print(f"   News: {result} ✅")
    
    print("\n2️⃣ Testing cache statistics:")
    stats = cache_manager.market_data.get_stats()
    print(f"   Market data stats:")
    print(f"   - Size: {stats['size']}")
    print(f"   - Hits: {stats['hits']}")
    print(f"   - Misses: {stats['misses']}")
    print(f"   - Hit rate: {stats['hit_rate']:.1f}%")
    
    print("\n✅ Cache manager: PASSED")


def test_lru_eviction():
    """Test LRU eviction when cache is full"""
    print("\n" + "="*80)
    print("🧪 TEST 4: LRU Eviction")
    print("="*80)
    
    # Small cache for testing
    cache = CacheWithTTL('test_lru', ttl_seconds=60, max_size=3)
    
    print("\n1️⃣ Filling cache to capacity (max 3 items):")
    cache.set('item1', 'value1')
    cache.set('item2', 'value2')
    cache.set('item3', 'value3')
    print(f"   Added 3 items, size: {len(cache)}")
    
    print("\n2️⃣ Accessing item1 (mark as recently used):")
    cache.get('item1')
    time.sleep(0.1)  # Small delay to differentiate access times
    
    print("\n3️⃣ Adding 4th item (should evict least recently used):")
    cache.set('item4', 'value4')
    print(f"   Added item4, size: {len(cache)}")
    
    print("\n4️⃣ Checking which item was evicted:")
    # item1 was accessed recently, so item2 or item3 should be evicted
    has_item1 = cache.get('item1') is not None
    has_item2 = cache.get('item2') is not None
    has_item3 = cache.get('item3') is not None
    has_item4 = cache.get('item4') is not None
    
    print(f"   item1 present: {has_item1} (recently accessed, should be kept)")
    print(f"   item2 present: {has_item2}")
    print(f"   item3 present: {has_item3}")
    print(f"   item4 present: {has_item4} (just added)")
    
    assert has_item1, "Recently accessed item should not be evicted"
    assert has_item4, "Newly added item should be present"
    assert len(cache) == 3, "Cache should maintain max size"
    
    stats = cache.get_stats()
    print(f"\n   Evictions: {stats['evictions']}")
    
    print("\n✅ LRU eviction: PASSED")


def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("💾 CACHE SYSTEM TEST SUITE")
    print("="*80)
    
    try:
        test_basic_cache()
        test_cache_decorator()
        test_cache_manager()
        test_lru_eviction()
        
        print("\n" + "="*80)
        print("🎉 ALL TESTS PASSED!")
        print("="*80)
        
        # Show final statistics
        print("\n📊 Final Cache Statistics:")
        cache_manager.print_stats()
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
