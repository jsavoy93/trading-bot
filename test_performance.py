#!/usr/bin/env python3
"""
Test script to demonstrate performance monitoring
"""
import sys
import time
sys.path.insert(0, 'src')

from database.simple_rest import simple_rest
from utils.performance import perf_monitor
from datetime import datetime

print("="*80)
print("🧪 TESTING PERFORMANCE MONITORING")
print("="*80)

if not simple_rest.is_available():
    print("\n❌ Database not available - can't test performance monitoring")
    print("   (Performance monitoring will still work for other operations)")
    sys.exit(0)

print("\n✅ Database connected - running performance test...")
print("\n📊 Executing database operations with performance tracking:\n")

# Test various database operations
test_symbols = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA']

# Test 1: Research cooldown checks (tracked with @track_performance and @track_api_call)
print("1️⃣ Testing research cooldown checks...")
for symbol in test_symbols:
    cooldown = simple_rest.get_research_cooldown(symbol)
    time.sleep(0.1)  # Small delay to see timing differences

# Test 2: Setting research cooldowns
print("2️⃣ Testing research cooldown updates...")
for symbol in test_symbols:
    simple_rest.set_research_cooldown(symbol, datetime.utcnow())
    time.sleep(0.05)

# Test 3: Trade cooldown operations
print("3️⃣ Testing trade cooldown operations...")
for symbol in test_symbols[:3]:
    simple_rest.get_trade_cooldown(symbol)
    simple_rest.set_trade_cooldown(symbol)
    time.sleep(0.08)

# Test 4: Position sell cooldowns
print("4️⃣ Testing position sell cooldowns...")
for symbol in test_symbols[:2]:
    simple_rest.get_position_sell_cooldown(symbol)
    simple_rest.set_position_sell_cooldown(symbol)
    time.sleep(0.1)

print("\n✅ Test operations complete!\n")

# Display collected metrics
print("="*80)
print("📊 PERFORMANCE ANALYSIS RESULTS")
print("="*80)

summary = perf_monitor.get_summary()

# Function Performance
if summary['functions']:
    print("\n🔍 FUNCTION PERFORMANCE (sorted by total time):")
    print("-" * 80)
    print(f"{'Function Name':<45} {'Calls':<8} {'Avg(ms)':<10} {'Total(s)':<10} {'Errors':<8}")
    print("-" * 80)
    
    sorted_funcs = sorted(
        summary['functions'].items(),
        key=lambda x: x[1]['total_time'],
        reverse=True
    )
    
    for func_name, stats in sorted_funcs:
        avg_ms = stats['avg_time'] * 1000
        print(
            f"{func_name:<45} "
            f"{stats['calls']:<8} "
            f"{avg_ms:<10.2f} "
            f"{stats['total_time']:<10.3f} "
            f"{stats['errors']:<8}"
        )

# API Performance
if summary['apis']:
    print("\n\n🌐 API CALL PERFORMANCE (sorted by call count):")
    print("-" * 80)
    print(f"{'Endpoint':<45} {'Calls':<8} {'Avg(ms)':<10} {'Total(s)':<10} {'Errors':<8}")
    print("-" * 80)
    
    sorted_apis = sorted(
        summary['apis'].items(),
        key=lambda x: x[1]['calls'],
        reverse=True
    )
    
    for endpoint, stats in sorted_apis:
        avg_ms = stats['avg_time'] * 1000
        error_pct = f"{stats['error_rate']:.1f}%" if stats['errors'] > 0 else "-"
        print(
            f"{endpoint:<45} "
            f"{stats['calls']:<8} "
            f"{avg_ms:<10.2f} "
            f"{stats['total_time']:<10.3f} "
            f"{stats['errors']:<8}"
        )

# Performance Insights
print("\n\n💡 PERFORMANCE INSIGHTS:")
print("-" * 80)

if summary['functions']:
    # Slowest average function
    slowest = max(summary['functions'].items(), key=lambda x: x[1]['avg_time'])
    print(f"⚠️  Slowest function (avg): {slowest[0]} ({slowest[1]['avg_time']*1000:.2f}ms)")
    
    # Most called function
    most_called = max(summary['functions'].items(), key=lambda x: x[1]['calls'])
    print(f"🔥 Most called function: {most_called[0]} ({most_called[1]['calls']} calls)")
    
    # Total time across all functions
    total_time = sum(stats['total_time'] for stats in summary['functions'].values())
    print(f"⏱️  Total tracked time: {total_time:.3f}s")
    
    # Functions with errors
    with_errors = [(name, stats) for name, stats in summary['functions'].items() if stats['errors'] > 0]
    if with_errors:
        print(f"❌ Functions with errors: {len(with_errors)}")
        for name, stats in with_errors:
            print(f"   - {name}: {stats['errors']} errors ({stats['error_rate']:.1f}% error rate)")
    else:
        print(f"✅ No errors detected in {len(summary['functions'])} tracked functions")

if summary['apis']:
    print(f"\n🌐 Total API calls: {sum(stats['calls'] for stats in summary['apis'].values())}")
    total_api_time = sum(stats['total_time'] for stats in summary['apis'].values())
    print(f"⏱️  Total API time: {total_api_time:.3f}s")
    
    api_errors = sum(stats['errors'] for stats in summary['apis'].values())
    if api_errors > 0:
        print(f"❌ Total API errors: {api_errors}")
    else:
        print(f"✅ No API errors detected")

print("\n" + "="*80)
print("✅ Performance monitoring test complete!")
print("="*80)
print("\n💡 This demonstrates how performance tracking works automatically")
print("   when decorators are applied to functions.")
print("\n📖 See PERFORMANCE_MONITORING.md for full documentation\n")
