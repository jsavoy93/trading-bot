#!/usr/bin/env python3
"""
View cache statistics and performance metrics.
"""
import sys
import os
from pathlib import Path

# Add src to path (go up 2 levels from scripts/utils to project root)
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from utils.cache import get_cache_manager


def main():
    """Display cache statistics"""
    cache_manager = get_cache_manager()
    
    print("\n" + "="*80)
    print("💾 TRADING BOT CACHE STATISTICS")
    print("="*80)
    
    stats = cache_manager.get_all_stats()
    
    # Check if any caches have been used
    total_requests = sum(s['total_requests'] for s in stats.values())
    
    if total_requests == 0:
        print("\n📊 No cache activity yet")
        print("\n   Caches will be populated as the bot runs.")
        print("   Run the bot and check back to see cache performance.\n")
        return
    
    # Overall statistics
    total_hits = sum(s['hits'] for s in stats.values())
    total_misses = sum(s['misses'] for s in stats.values())
    total_size = sum(s['size'] for s in stats.values())
    
    print(f"\n📈 OVERALL STATISTICS:")
    print(f"   Total Cache Entries: {total_size}")
    print(f"   Total Hits: {total_hits:,}")
    print(f"   Total Misses: {total_misses:,}")
    print(f"   Overall Hit Rate: {(total_hits/total_requests*100):.1f}%")
    
    # Individual cache statistics
    print(f"\n📊 INDIVIDUAL CACHE PERFORMANCE:")
    print("-" * 80)
    
    # Sort by hit rate (most effective caches first)
    sorted_stats = sorted(
        [(name, s) for name, s in stats.items() if s['total_requests'] > 0],
        key=lambda x: x[1]['hit_rate'],
        reverse=True
    )
    
    if sorted_stats:
        print(f"{'Cache Name':<20} {'Size':<12} {'TTL':<12} {'Hit Rate':<12} {'Hits/Misses':<15}")
        print("-" * 80)
        
        for name, cache_stats in sorted_stats:
            size_str = f"{cache_stats['size']}/{cache_stats['max_size']}"
            
            # Format TTL nicely
            ttl = cache_stats['ttl_seconds']
            if ttl >= 3600:
                ttl_str = f"{ttl/3600:.1f}h"
            elif ttl >= 60:
                ttl_str = f"{ttl/60:.0f}m"
            else:
                ttl_str = f"{ttl}s"
            
            hit_rate_str = f"{cache_stats['hit_rate']:.1f}%"
            hits_misses_str = f"{cache_stats['hits']}/{cache_stats['misses']}"
            
            print(f"{name:<20} {size_str:<12} {ttl_str:<12} {hit_rate_str:<12} {hits_misses_str:<15}")
            
            if cache_stats['evictions'] > 0:
                print(f"{'':20} ⚠️  {cache_stats['evictions']} evictions (cache full)")
    
    # Recommendations
    print(f"\n💡 RECOMMENDATIONS:")
    print("-" * 80)
    
    for name, cache_stats in stats.items():
        if cache_stats['total_requests'] == 0:
            continue
        
        # Low hit rate warning
        if cache_stats['hit_rate'] < 30 and cache_stats['total_requests'] > 10:
            print(f"⚠️  {name}: Low hit rate ({cache_stats['hit_rate']:.1f}%)")
            print(f"   - Consider increasing TTL or reviewing cache key strategy")
        
        # High eviction rate
        if cache_stats['evictions'] > cache_stats['hits'] * 0.1:
            print(f"⚠️  {name}: High eviction rate ({cache_stats['evictions']} evictions)")
            print(f"   - Consider increasing max_size from {cache_stats['max_size']}")
        
        # Excellent performance
        if cache_stats['hit_rate'] > 70 and cache_stats['total_requests'] > 20:
            print(f"✅ {name}: Excellent hit rate ({cache_stats['hit_rate']:.1f}%)")
            print(f"   - Cache is working effectively!")
    
    # Calculate API call savings
    if total_hits > 0:
        print(f"\n💰 ESTIMATED API CALL SAVINGS:")
        print(f"   Avoided API Calls: {total_hits:,}")
        print(f"   Reduction Rate: {(total_hits/total_requests*100):.1f}%")
        
        # Estimate cost savings (rough estimate)
        # Assuming average API call costs ~$0.001
        estimated_savings = total_hits * 0.001
        print(f"   Est. Cost Savings: ${estimated_savings:.2f}")
    
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
