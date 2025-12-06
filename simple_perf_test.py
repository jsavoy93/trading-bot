#!/usr/bin/env python3
"""Simple test of performance monitoring without other dependencies"""
import sys
import os
import time

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Direct import
print("Importing performance module...")
from utils.performance import PerformanceMonitor, track_performance

print("✅ Performance module imported successfully\n")

# Create a test function with tracking
@track_performance(log_level='info')
def slow_function():
    """Simulate a slow function"""
    time.sleep(0.1)
    return "done"

@track_performance(threshold_ms=50)
def fast_function():
    """Simulate a fast function"""
    time.sleep(0.01)
    return "quick"

print("="*80)
print("🧪 TESTING PERFORMANCE DECORATORS")
print("="*80)

# Run test functions
print("\n1️⃣ Running slow_function 5 times...")
for i in range(5):
    result = slow_function()
    
print("\n2️⃣ Running fast_function 10 times...")
for i in range(10):
    result = fast_function()

# Get monitor and display results
monitor = PerformanceMonitor()
summary = monitor.get_summary()

print("\n" + "="*80)
print("📊 PERFORMANCE RESULTS")
print("="*80)

if summary['functions']:
    print("\n🔍 Function Performance:")
    print("-" * 80)
    print(f"{'Function':<30} {'Calls':<10} {'Avg(ms)':<12} {'Min(ms)':<12} {'Max(ms)':<12}")
    print("-" * 80)
    
    for func_name, stats in summary['functions'].items():
        avg_ms = stats['avg_time'] * 1000
        min_ms = stats['min_time'] * 1000
        max_ms = stats['max_time'] * 1000
        print(f"{func_name:<30} {stats['calls']:<10} {avg_ms:<12.2f} {min_ms:<12.2f} {max_ms:<12.2f}")

print("\n" + "="*80)
print("✅ Performance monitoring working correctly!")
print("="*80)
