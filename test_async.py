#!/usr/bin/env python3
"""
Test script to validate async improvements and measure performance gains.

This script tests:
1. Basic async/await patterns work correctly
2. AsyncBatchRunner efficiency
3. Performance comparison: old vs new patterns
4. Error handling and edge cases
"""
import sys
import time
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from utils.async_helpers import (
    AsyncBatchRunner,
    run_async_safely,
    async_retry,
    AsyncContextManager,
    sync_to_async,
    gather_with_limit
)


# Sample async functions for testing
async def async_operation(delay: float, value: str) -> str:
    """Simulates an async API call"""
    await asyncio.sleep(delay)
    return f"Result: {value}"


async def failing_operation(should_fail: bool = True) -> str:
    """Simulates an operation that might fail"""
    await asyncio.sleep(0.1)
    if should_fail:
        raise ValueError("Simulated failure")
    return "Success"


@async_retry(max_retries=3, delay=0.1)
async def retryable_operation(fail_count: int) -> str:
    """Operation that fails a certain number of times"""
    if fail_count > 0:
        # Use a mutable container to track state
        if not hasattr(retryable_operation, 'attempts'):
            retryable_operation.attempts = 0
        retryable_operation.attempts += 1
        
        if retryable_operation.attempts <= fail_count:
            raise ValueError(f"Attempt {retryable_operation.attempts} failed")
    
    return "Success after retries"


def test_basic_async_patterns():
    """Test basic async/await improvements"""
    print("\n" + "="*80)
    print("🧪 TEST 1: Basic Async Patterns")
    print("="*80)
    
    # Test 1: asyncio.run() instead of new_event_loop()
    print("\n1️⃣ Testing asyncio.run() pattern:")
    start = time.time()
    result = asyncio.run(async_operation(0.1, "test1"))
    duration = time.time() - start
    print(f"   Result: {result}")
    print(f"   Duration: {duration*1000:.1f}ms")
    assert result == "Result: test1"
    print("   ✅ asyncio.run() works correctly")
    
    # Test 2: run_async_safely with default
    print("\n2️⃣ Testing run_async_safely with default:")
    result = run_async_safely(failing_operation(), default="Fallback")
    print(f"   Result: {result}")
    assert result == "Fallback"
    print("   ✅ Error handling with default works")
    
    # Test 3: run_async_safely with timeout
    print("\n3️⃣ Testing run_async_safely with timeout:")
    start = time.time()
    result = run_async_safely(
        async_operation(5.0, "slow"),
        default="Timeout",
        timeout=0.5
    )
    duration = time.time() - start
    print(f"   Result: {result}")
    print(f"   Duration: {duration*1000:.1f}ms (should be ~500ms)")
    assert result == "Timeout"
    assert duration < 1.0  # Should timeout before 5 seconds
    print("   ✅ Timeout handling works")
    
    print("\n✅ Basic async patterns: PASSED")


def test_batch_runner():
    """Test AsyncBatchRunner efficiency"""
    print("\n" + "="*80)
    print("🧪 TEST 2: AsyncBatchRunner Efficiency")
    print("="*80)
    
    # Test 1: Sequential asyncio.run() calls (OLD WAY)
    print("\n1️⃣ OLD WAY: Sequential asyncio.run() calls:")
    start = time.time()
    results_old = []
    for i in range(5):
        result = asyncio.run(async_operation(0.1, f"task{i}"))
        results_old.append(result)
    duration_old = time.time() - start
    print(f"   Results: {len(results_old)} tasks completed")
    print(f"   Duration: {duration_old*1000:.1f}ms")
    
    # Test 2: AsyncBatchRunner (NEW WAY)
    print("\n2️⃣ NEW WAY: AsyncBatchRunner:")
    start = time.time()
    runner = AsyncBatchRunner()
    for i in range(5):
        runner.add(async_operation(0.1, f"task{i}"))
    results_new = runner.run_all()
    duration_new = time.time() - start
    print(f"   Results: {len(results_new)} tasks completed")
    print(f"   Duration: {duration_new*1000:.1f}ms")
    
    # Calculate speedup
    speedup = duration_old / duration_new
    print(f"\n📊 Performance Comparison:")
    print(f"   Old way: {duration_old*1000:.1f}ms")
    print(f"   New way: {duration_new*1000:.1f}ms")
    print(f"   Speedup: {speedup:.1f}x faster")
    
    # AsyncBatchRunner runs tasks concurrently, so should be much faster
    assert duration_new < duration_old, "Batch runner should be faster"
    print(f"\n✅ AsyncBatchRunner is {speedup:.1f}x faster: PASSED")


def test_async_context_manager():
    """Test AsyncContextManager"""
    print("\n" + "="*80)
    print("🧪 TEST 3: AsyncContextManager")
    print("="*80)
    
    print("\n1️⃣ Testing context manager with successful operations:")
    with AsyncContextManager() as runner:
        runner.add(async_operation(0.05, "A"))
        runner.add(async_operation(0.05, "B"))
        runner.add(async_operation(0.05, "C"))
        
        start = time.time()
        results = runner.execute()
        duration = time.time() - start
        
    print(f"   Results: {results}")
    print(f"   Duration: {duration*1000:.1f}ms")
    assert len(results) == 3
    print("   ✅ Context manager works")
    
    print("\n2️⃣ Testing context manager with errors (ignore_errors=True):")
    with AsyncContextManager(ignore_errors=True) as runner:
        runner.add(async_operation(0.05, "OK"))
        runner.add(failing_operation(should_fail=True))
        runner.add(async_operation(0.05, "Also OK"))
        
        results = runner.execute()
        
    print(f"   Results count: {len(results)}")
    errors = sum(1 for r in results if isinstance(r, Exception))
    print(f"   Errors: {errors}")
    successes = sum(1 for r in results if not isinstance(r, Exception))
    print(f"   Successes: {successes}")
    assert errors == 1 and successes == 2
    print("   ✅ Error handling works correctly")
    
    print("\n✅ AsyncContextManager: PASSED")


def test_retry_decorator():
    """Test async_retry decorator"""
    print("\n" + "="*80)
    print("🧪 TEST 4: Async Retry Decorator")
    print("="*80)
    
    print("\n1️⃣ Testing retry with eventual success:")
    # Reset attempts counter
    if hasattr(retryable_operation, 'attempts'):
        delattr(retryable_operation, 'attempts')
    
    start = time.time()
    result = asyncio.run(retryable_operation(fail_count=2))
    duration = time.time() - start
    
    print(f"   Result: {result}")
    print(f"   Duration: {duration*1000:.1f}ms")
    print(f"   (Should have retried 2 times before succeeding)")
    assert result == "Success after retries"
    print("   ✅ Retry mechanism works")
    
    print("\n✅ Retry decorator: PASSED")


def test_gather_with_limit():
    """Test gather_with_limit for rate limiting"""
    print("\n" + "="*80)
    print("🧪 TEST 5: Gather With Limit (Rate Limiting)")
    print("="*80)
    
    async def test_limited_concurrency():
        tasks = [async_operation(0.1, f"task{i}") for i in range(10)]
        
        print("\n1️⃣ Testing unlimited gather:")
        start = time.time()
        results1 = await asyncio.gather(*tasks)
        duration1 = time.time() - start
        print(f"   Completed {len(results1)} tasks in {duration1*1000:.1f}ms")
        
        # Recreate tasks
        tasks = [async_operation(0.1, f"task{i}") for i in range(10)]
        
        print("\n2️⃣ Testing limited gather (max 3 concurrent):")
        start = time.time()
        results2 = await gather_with_limit(tasks, limit=3)
        duration2 = time.time() - start
        print(f"   Completed {len(results2)} tasks in {duration2*1000:.1f}ms")
        
        print(f"\n📊 Comparison:")
        print(f"   Unlimited: {duration1*1000:.1f}ms (all 10 concurrent)")
        print(f"   Limited (3): {duration2*1000:.1f}ms (max 3 at a time)")
        print(f"   Limited version took {duration2/duration1:.1f}x longer (expected)")
        
        # Limited version should take longer but complete successfully
        assert len(results2) == 10
        assert duration2 > duration1
        
        return True
    
    success = asyncio.run(test_limited_concurrency())
    assert success
    print("\n✅ Gather with limit: PASSED")


def test_sync_to_async():
    """Test sync_to_async decorator"""
    print("\n" + "="*80)
    print("🧪 TEST 6: Sync to Async Decorator")
    print("="*80)
    
    @sync_to_async
    def blocking_operation(value: int) -> int:
        """Simulates blocking I/O"""
        time.sleep(0.1)
        return value * 2
    
    async def test_conversion():
        print("\n1️⃣ Testing sync function converted to async:")
        start = time.time()
        result = await blocking_operation(21)
        duration = time.time() - start
        
        print(f"   Input: 21")
        print(f"   Result: {result}")
        print(f"   Duration: {duration*1000:.1f}ms")
        assert result == 42
        return True
    
    success = asyncio.run(test_conversion())
    assert success
    print("   ✅ Sync to async conversion works")
    
    print("\n✅ Sync to async decorator: PASSED")


def test_performance_comparison():
    """Compare old vs new async patterns"""
    print("\n" + "="*80)
    print("📊 PERFORMANCE COMPARISON: Old vs New Patterns")
    print("="*80)
    
    def old_pattern_single_call():
        """OLD: new_event_loop pattern (deprecated)"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(async_operation(0.01, "test"))
            return result
        finally:
            loop.close()
    
    def new_pattern_single_call():
        """NEW: asyncio.run pattern"""
        return asyncio.run(async_operation(0.01, "test"))
    
    # Test single calls
    print("\n1️⃣ Single async call (10ms operation):")
    
    # Warm up
    old_pattern_single_call()
    new_pattern_single_call()
    
    # Old pattern
    start = time.time()
    for _ in range(10):
        old_pattern_single_call()
    duration_old = time.time() - start
    
    # New pattern
    start = time.time()
    for _ in range(10):
        new_pattern_single_call()
    duration_new = time.time() - start
    
    speedup_single = duration_old / duration_new
    print(f"   Old pattern (10 calls): {duration_old*1000:.1f}ms")
    print(f"   New pattern (10 calls): {duration_new*1000:.1f}ms")
    print(f"   Speedup: {speedup_single:.1f}x faster")
    
    # Test multiple calls
    print("\n2️⃣ Multiple async calls (5 x 10ms operations):")
    
    def old_pattern_multiple():
        """OLD: Multiple event loop creations"""
        results = []
        for i in range(5):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(async_operation(0.01, f"task{i}"))
                results.append(result)
            finally:
                loop.close()
        return results
    
    def new_pattern_multiple():
        """NEW: AsyncBatchRunner"""
        runner = AsyncBatchRunner()
        for i in range(5):
            runner.add(async_operation(0.01, f"task{i}"))
        return runner.run_all()
    
    # Old pattern
    start = time.time()
    old_pattern_multiple()
    duration_old_multi = time.time() - start
    
    # New pattern
    start = time.time()
    new_pattern_multiple()
    duration_new_multi = time.time() - start
    
    speedup_multi = duration_old_multi / duration_new_multi
    print(f"   Old pattern: {duration_old_multi*1000:.1f}ms")
    print(f"   New pattern: {duration_new_multi*1000:.1f}ms")
    print(f"   Speedup: {speedup_multi:.1f}x faster")
    
    print("\n" + "="*80)
    print("📈 OVERALL PERFORMANCE GAINS:")
    print("="*80)
    print(f"   Single calls: {speedup_single:.1f}x faster")
    print(f"   Multiple calls: {speedup_multi:.1f}x faster")
    avg_speedup = (speedup_single + speedup_multi) / 2
    print(f"   Average speedup: {avg_speedup:.1f}x faster")
    print(f"   Estimated real-world gain: {(avg_speedup - 1) * 100:.0f}% faster")
    print("="*80)
    
    return avg_speedup


def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("🚀 ASYNC/AWAIT IMPROVEMENTS TEST SUITE")
    print("="*80)
    
    try:
        test_basic_async_patterns()
        test_batch_runner()
        test_async_context_manager()
        test_retry_decorator()
        test_gather_with_limit()
        test_sync_to_async()
        
        # Performance comparison
        avg_speedup = test_performance_comparison()
        
        print("\n" + "="*80)
        print("🎉 ALL TESTS PASSED!")
        print("="*80)
        print(f"\n✨ Average Performance Improvement: {avg_speedup:.1f}x faster")
        print(f"✨ Estimated Real-World Speedup: {(avg_speedup - 1) * 100:.0f}%")
        print("\n💡 Key Benefits:")
        print("   • Cleaner async code (no manual event loop management)")
        print("   • Better resource cleanup (automatic)")
        print("   • Faster execution (batched operations)")
        print("   • More maintainable (standardized patterns)")
        
        return 0
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
