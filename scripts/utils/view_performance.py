#!/usr/bin/env python3
"""
Utility script to view performance metrics from the trading bot.
Usage: python view_performance.py
"""
import sys
import os
from pathlib import Path

# Add src to path (go up 2 levels from scripts/utils to project root)
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from utils.performance import get_performance_monitor

def main():
    """Display performance metrics summary"""
    monitor = get_performance_monitor()
    
    print("\n" + "=" * 80)
    print("📊 TRADING BOT PERFORMANCE METRICS")
    print("=" * 80)
    
    summary = monitor.get_summary()
    
    # Function performance
    if summary['functions']:
        print("\n🔍 Function Performance:")
        print("-" * 80)
        
        # Sort by total time
        sorted_funcs = sorted(
            summary['functions'].items(),
            key=lambda x: x[1]['total_time'],
            reverse=True
        )
        
        print(f"{'Function':<40} {'Calls':<8} {'Avg (ms)':<12} {'Total (s)':<12} {'Errors':<8}")
        print("-" * 80)
        
        for func_name, stats in sorted_funcs:
            avg_ms = stats['avg_time'] * 1000
            print(
                f"{func_name:<40} "
                f"{stats['calls']:<8} "
                f"{avg_ms:<12.1f} "
                f"{stats['total_time']:<12.2f} "
                f"{stats['errors']:<8}"
            )
    else:
        print("\n🔍 No function metrics recorded yet")
    
    # API performance
    if summary['apis']:
        print("\n🌐 API Call Performance:")
        print("-" * 80)
        
        # Sort by call count
        sorted_apis = sorted(
            summary['apis'].items(),
            key=lambda x: x[1]['calls'],
            reverse=True
        )
        
        print(f"{'Endpoint':<40} {'Calls':<8} {'Avg (ms)':<12} {'Total (s)':<12} {'Errors':<8}")
        print("-" * 80)
        
        for endpoint, stats in sorted_apis:
            avg_ms = stats['avg_time'] * 1000
            print(
                f"{endpoint:<40} "
                f"{stats['calls']:<8} "
                f"{avg_ms:<12.1f} "
                f"{stats['total_time']:<12.2f} "
                f"{stats['errors']:<8}"
            )
    else:
        print("\n🌐 No API metrics recorded yet")
    
    # Cache performance
    if summary['caches']:
        print("\n💾 Cache Performance:")
        print("-" * 80)
        
        print(f"{'Cache':<40} {'Hits':<10} {'Misses':<10} {'Hit Rate':<12}")
        print("-" * 80)
        
        for cache_name, stats in summary['caches'].items():
            print(
                f"{cache_name:<40} "
                f"{stats['hits']:<10} "
                f"{stats['misses']:<10} "
                f"{stats['hit_rate']:<12.1f}%"
            )
    else:
        print("\n💾 No cache metrics recorded yet")
    
    # Performance insights
    if summary['functions']:
        print("\n💡 Performance Insights:")
        print("-" * 80)
        
        # Find slowest average function
        slowest = max(summary['functions'].items(), key=lambda x: x[1]['avg_time'])
        slowest_name, slowest_stats = slowest
        print(f"⚠️  Slowest function (avg): {slowest_name} ({slowest_stats['avg_time']*1000:.1f}ms)")
        
        # Find most called function
        most_called = max(summary['functions'].items(), key=lambda x: x[1]['calls'])
        most_name, most_stats = most_called
        print(f"🔥 Most called function: {most_name} ({most_stats['calls']} calls)")
        
        # Find functions with errors
        with_errors = [(name, stats) for name, stats in summary['functions'].items() if stats['errors'] > 0]
        if with_errors:
            print(f"❌ Functions with errors: {len(with_errors)}")
            for name, stats in sorted(with_errors, key=lambda x: x[1]['errors'], reverse=True)[:5]:
                print(f"   - {name}: {stats['errors']} errors ({stats['error_rate']:.1f}% error rate)")
    
    print("\n" + "=" * 80)
    print("\n💡 Tip: Run this script while the bot is running to see live metrics")
    print("   Or check the logs for performance summaries at the end of each loop\n")


if __name__ == "__main__":
    main()
