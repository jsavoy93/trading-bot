"""
Async helper utilities for improved async/await patterns.

This module provides utilities to make async code more efficient and maintainable:
- Batch async operations to reduce event loop overhead
- Safely run async functions from sync contexts
- Proper error handling and resource cleanup
"""
import asyncio
import logging
from typing import List, Callable, Any, Optional, TypeVar, Coroutine
from concurrent.futures import ThreadPoolExecutor
import functools

T = TypeVar('T')


class AsyncBatchRunner:
    """
    Efficiently runs multiple async operations in batches.
    
    This reduces the overhead of creating event loops for each operation
    by batching them together into a single asyncio.run() call.
    
    Example:
        runner = AsyncBatchRunner()
        runner.add(ai.research_symbol('AAPL'))
        runner.add(ai.research_symbol('GOOGL'))
        results = runner.run_all()  # Runs both in same event loop
    """
    
    def __init__(self):
        self.tasks: List[Coroutine] = []
        
    def add(self, coro: Coroutine) -> int:
        """
        Add a coroutine to the batch.
        Returns the index where the result will be stored.
        """
        self.tasks.append(coro)
        return len(self.tasks) - 1
    
    def run_all(self, ignore_errors: bool = False) -> List[Any]:
        """
        Run all batched tasks in a single event loop.
        
        Args:
            ignore_errors: If True, exceptions are logged but don't stop other tasks
            
        Returns:
            List of results in the same order as tasks were added
        """
        if not self.tasks:
            return []
        
        async def _run_batch():
            if ignore_errors:
                # Use gather with return_exceptions=True
                return await asyncio.gather(*self.tasks, return_exceptions=True)
            else:
                # Let exceptions propagate
                return await asyncio.gather(*self.tasks)
        
        try:
            results = asyncio.run(_run_batch())
            self.tasks.clear()  # Reset for potential reuse
            return results
        except Exception as e:
            logging.error(f"Async batch execution failed: {e}")
            self.tasks.clear()
            raise
    
    def clear(self):
        """Clear all pending tasks"""
        self.tasks.clear()
    
    def __len__(self):
        """Number of pending tasks"""
        return len(self.tasks)


def run_async_safely(coro: Coroutine[Any, Any, T], 
                     default: Optional[T] = None,
                     timeout: Optional[float] = None) -> T:
    """
    Safely run a coroutine from sync code with proper error handling.
    
    This is a drop-in replacement for asyncio.run() with better error handling
    and optional timeout support.
    
    Args:
        coro: The coroutine to run
        default: Value to return if execution fails (prevents exceptions)
        timeout: Maximum execution time in seconds
        
    Returns:
        Result of the coroutine, or default value if it fails
        
    Example:
        result = run_async_safely(ai.research_symbol('AAPL'), default={})
    """
    async def _run_with_timeout():
        if timeout:
            return await asyncio.wait_for(coro, timeout=timeout)
        else:
            return await coro
    
    try:
        return asyncio.run(_run_with_timeout())
    except asyncio.TimeoutError:
        logging.warning(f"Async operation timed out after {timeout}s")
        return default
    except Exception as e:
        if default is not None:
            logging.debug(f"Async operation failed, using default: {e}")
            return default
        raise


def async_retry(max_retries: int = 3, 
                delay: float = 1.0,
                exponential_backoff: bool = True):
    """
    Decorator to retry async functions with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        exponential_backoff: If True, delay doubles after each retry
        
    Example:
        @async_retry(max_retries=3, delay=2.0)
        async def fetch_data():
            # Your async code here
            pass
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logging.debug(f"Retry {attempt + 1}/{max_retries} for {func.__name__} after {current_delay}s delay: {e}")
                        await asyncio.sleep(current_delay)
                        if exponential_backoff:
                            current_delay *= 2
                    else:
                        logging.warning(f"All {max_retries} retries failed for {func.__name__}: {e}")
            
            raise last_exception
        
        return wrapper
    return decorator


class AsyncContextManager:
    """
    Context manager for running multiple async operations efficiently.
    
    Ensures proper cleanup of resources and provides a cleaner API for
    batching async operations.
    
    Example:
        with AsyncContextManager() as runner:
            runner.add(ai.research_symbol('AAPL'))
            runner.add(ai.research_symbol('GOOGL'))
            results = runner.execute()
    """
    
    def __init__(self, ignore_errors: bool = True):
        self.runner = AsyncBatchRunner()
        self.ignore_errors = ignore_errors
        
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Clean up any pending tasks
        self.runner.clear()
        return False
    
    def add(self, coro: Coroutine) -> int:
        """Add a coroutine to the batch"""
        return self.runner.add(coro)
    
    def execute(self) -> List[Any]:
        """Execute all batched coroutines"""
        return self.runner.run_all(ignore_errors=self.ignore_errors)
    
    def __len__(self):
        return len(self.runner)


def sync_to_async(func: Callable) -> Callable:
    """
    Decorator to convert a synchronous function to async.
    
    Runs the sync function in a thread pool to avoid blocking the event loop.
    Useful for CPU-bound operations or blocking I/O in async contexts.
    
    Example:
        @sync_to_async
        def expensive_calculation(x):
            return x ** 2
        
        # Can now be awaited
        result = await expensive_calculation(42)
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        loop = asyncio.get_running_loop()
        # Use thread pool for CPU-bound operations
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
    
    return wrapper


# Convenience function for common pattern
async def gather_with_limit(tasks: List[Coroutine], 
                           limit: int = 10,
                           return_exceptions: bool = True) -> List[Any]:
    """
    Run async tasks with a concurrency limit to avoid overwhelming APIs.
    
    This is useful when you have many tasks but want to limit how many
    run concurrently to respect rate limits.
    
    Args:
        tasks: List of coroutines to execute
        limit: Maximum number of concurrent tasks
        return_exceptions: Whether to return exceptions or raise them
        
    Returns:
        List of results in the same order as input tasks
        
    Example:
        tasks = [fetch_data(symbol) for symbol in symbols]
        results = await gather_with_limit(tasks, limit=5)
    """
    semaphore = asyncio.Semaphore(limit)
    
    async def limited_task(coro):
        async with semaphore:
            return await coro
    
    limited_tasks = [limited_task(task) for task in tasks]
    return await asyncio.gather(*limited_tasks, return_exceptions=return_exceptions)


# Export public API
__all__ = [
    'AsyncBatchRunner',
    'run_async_safely',
    'async_retry',
    'AsyncContextManager',
    'sync_to_async',
    'gather_with_limit',
]
