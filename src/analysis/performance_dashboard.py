#!/usr/bin/env python3
"""
Trading Bot Performance Dashboard
Run this script to get performance insights and recommendations.
"""
import os
import sys
import json
from datetime import datetime
from dotenv import load_dotenv

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv()

def main():
    """Generate performance report"""
    print("📊 Trading Bot Performance Dashboard")
    print("=" * 50)
    
    try:
        from data_manager import data_manager
        from learning_engine import learning_engine
        
        # Get overall performance
        print("📈 Overall Performance (Last 30 days)")
        print("-" * 30)
        
        overall_performance = learning_engine.get_overall_performance_insights(days=30)
        
        if "error" in overall_performance:
            print(f"❌ Error: {overall_performance['error']}")
            return
        
        if "message" in overall_performance:
            print(f"ℹ️  {overall_performance['message']}")
            return
        
        # Display key metrics
        print(f"Total Trades: {overall_performance['total_trades']}")
        print(f"Win Rate: {overall_performance['win_rate']:.1f}%")
        print(f"Total P&L: ${overall_performance['total_pnl']:.2f}")
        print(f"Avg P&L per Trade: ${overall_performance['avg_pnl_per_trade']:.4f}")
        
        # Top performers
        print(f"\n🏆 Top Performing Symbols:")
        for symbol_data in overall_performance['top_performing_symbols'][:3]:
            print(f"  {symbol_data['symbol']}: ${symbol_data['pnl']:.2f} ({symbol_data['trades']} trades)")
        
        # Worst performers
        print(f"\n⚠️  Worst Performing Symbols:")
        for symbol_data in overall_performance['worst_performing_symbols'][:3]:
            print(f"  {symbol_data['symbol']}: ${symbol_data['pnl']:.2f} ({symbol_data['trades']} trades)")
        
        # Strategy analysis
        strategy = overall_performance['strategy_analysis']
        print(f"\n📋 Strategy Analysis:")
        print(f"  Buy Trades: {strategy['buy_trades']} (Win Rate: {strategy['buy_win_rate']:.1f}%)")
        print(f"  Sell Trades: {strategy['sell_trades']} (Win Rate: {strategy['sell_win_rate']:.1f}%)")
        
        # Recommendations
        print(f"\n💡 Recommendations:")
        for rec in overall_performance['recommendations']:
            print(f"  • {rec}")
        
        # Detailed symbol analysis for top performers
        print(f"\n🔍 Detailed Analysis (Top 3 Symbols)")
        print("-" * 40)
        
        for symbol_data in overall_performance['top_performing_symbols'][:3]:
            symbol = symbol_data['symbol']
            print(f"\n📌 {symbol} Analysis:")
            
            analysis = learning_engine.analyze_symbol_performance(symbol, days=60)
            
            if "error" not in analysis and "analysis" not in analysis:
                print(f"  Period: {analysis['period_days']} days")
                print(f"  Total Trades: {analysis['total_trades']}")
                print(f"  Win Rate: {analysis['win_rate']:.1f}%")
                print(f"  Total P&L: ${analysis['total_pnl']:.2f}")
                print(f"  Recommendation: {analysis['recommendation']}")
                
                # RSI insights
                rsi_rec = analysis['rsi_analysis'].get('recommendation', 'No RSI data')
                print(f"  RSI Insight: {rsi_rec}")
        
        # Get recent errors
        print(f"\n🚨 Recent Issues (Last 24 hours)")
        print("-" * 35)
        
        recent_errors = data_manager.get_recent_errors(hours=24)
        
        if recent_errors:
            error_counts = {}
            for error in recent_errors:
                error_type = error['error_type']
                error_counts[error_type] = error_counts.get(error_type, 0) + 1
            
            print("Error Summary:")
            for error_type, count in error_counts.items():
                print(f"  {error_type}: {count} occurrences")
            
            print(f"\nMost Recent Errors:")
            for error in recent_errors[:5]:
                print(f"  {error['timestamp'].strftime('%H:%M')} - {error['error_type']}")
                if error['symbol']:
                    print(f"    Symbol: {error['symbol']}")
                print(f"    {error['message'][:100]}...")
        else:
            print("✅ No recent errors found")
        
        print(f"\n🎉 Report generated successfully at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
    except Exception as e:
        print(f"❌ Failed to generate performance report: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()