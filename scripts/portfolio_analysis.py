#!/usr/bin/env python3
"""
Portfolio Analysis Tool
Analyzes your trading performance, positions, and provides insights
"""

import os
import sys
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
from core.smart_bot import SmartTradingBot

def analyze_portfolio():
    """Comprehensive portfolio analysis"""
    load_dotenv()
    
    print("📊 PORTFOLIO ANALYSIS")
    print("=" * 80)
    
    try:
        # Initialize components
        bot = SmartTradingBot()
        db = bot.db
        
        # Get current portfolio from Alpaca
        print("\n🏦 CURRENT POSITIONS")
        print("-" * 50)
        
        try:
            positions = bot.trading_client.get_all_positions()
            
            if positions:
                total_market_value = 0
                total_unrealized_pnl = 0
                
                print(f"{'Symbol':<8} {'Qty':<8} {'Market Value':<15} {'Unrealized P&L':<15} {'%':<8}")
                print("-" * 65)
                
                for position in positions:
                    market_value = float(position.market_value)
                    unrealized_pnl = float(position.unrealized_pl)
                    unrealized_pnl_pct = float(position.unrealized_plpc) * 100
                    
                    total_market_value += market_value
                    total_unrealized_pnl += unrealized_pnl
                    
                    print(f"{position.symbol:<8} {position.qty:<8} ${market_value:<14.2f} ${unrealized_pnl:<14.2f} {unrealized_pnl_pct:<7.2f}%")
                
                print("-" * 65)
                print(f"{'TOTAL':<8} {'':<8} ${total_market_value:<14.2f} ${total_unrealized_pnl:<14.2f}")
                
            else:
                print("No current positions")
                
        except Exception as e:
            print(f"❌ Error getting positions: {e}")
        
        # Get account information
        print("\n💰 ACCOUNT SUMMARY")
        print("-" * 50)
        
        try:
            account = bot.trading_client.get_account()
            
            buying_power = float(account.buying_power)
            cash = float(account.cash)
            portfolio_value = float(account.portfolio_value)
            
            print(f"Portfolio Value: ${portfolio_value:,.2f}")
            print(f"Cash Available: ${cash:,.2f}")
            print(f"Buying Power:   ${buying_power:,.2f}")
            
        except Exception as e:
            print(f"❌ Error getting account info: {e}")
        
        # Analyze trading history from database
        print("\n📈 TRADING PERFORMANCE")
        print("-" * 50)
        
        if db.is_available():
            try:
                # Get recent trades
                trades_data = db.select_data("trades", limit=100)
                
                if trades_data:
                    df_trades = pd.DataFrame(trades_data)
                    
                    # Calculate performance metrics
                    total_trades = len(df_trades)
                    
                    # Group by symbol
                    symbol_performance = df_trades.groupby('symbol').agg({
                        'signal': 'count',
                        'price': 'mean',
                        'rsi': 'mean',
                        'sma_fast': 'mean',
                        'sma_slow': 'mean'
                    }).round(2)
                    
                    print(f"Total Trades: {total_trades}")
                    print(f"Unique Symbols: {df_trades['symbol'].nunique()}")
                    
                    print(f"\n📊 Top Trading Symbols:")
                    top_symbols = df_trades['symbol'].value_counts().head(10)
                    for symbol, count in top_symbols.items():
                        avg_rsi = df_trades[df_trades['symbol'] == symbol]['rsi'].mean()
                        print(f"  {symbol}: {count} trades (avg RSI: {avg_rsi:.1f})")
                    
                    # Signal analysis
                    signal_counts = df_trades['signal'].value_counts()
                    print(f"\n🔄 Signal Distribution:")
                    for signal, count in signal_counts.items():
                        pct = (count / total_trades) * 100
                        print(f"  {signal}: {count} trades ({pct:.1f}%)")
                    
                    # RSI analysis
                    avg_rsi = df_trades['rsi'].mean()
                    rsi_buy_trades = df_trades[df_trades['signal'] == 'BUY']['rsi'].mean()
                    rsi_sell_trades = df_trades[df_trades['signal'] == 'SELL']['rsi'].mean()
                    
                    print(f"\n📊 RSI Analysis:")
                    print(f"  Average RSI: {avg_rsi:.1f}")
                    if not pd.isna(rsi_buy_trades):
                        print(f"  Buy Signal RSI: {rsi_buy_trades:.1f}")
                    if not pd.isna(rsi_sell_trades):
                        print(f"  Sell Signal RSI: {rsi_sell_trades:.1f}")
                    
                else:
                    print("No trading history found in database")
                    
            except Exception as e:
                print(f"❌ Error analyzing trades: {e}")
        else:
            print("Database not available - using Alpaca data only")
        
        # Get recent orders from Alpaca
        print("\n📋 RECENT ORDERS")  
        print("-" * 50)
        
        try:
            # Get orders from last 30 days
            orders = bot.trading_client.get_orders(
                status='all',
                limit=20
            )
            
            if orders:
                print(f"{'Symbol':<8} {'Side':<5} {'Qty':<8} {'Status':<10} {'Date':<12}")
                print("-" * 50)
                
                for order in orders:
                    order_date = order.created_at.strftime('%Y-%m-%d')
                    print(f"{order.symbol:<8} {order.side.value:<5} {order.qty:<8} {order.status.value:<10} {order_date:<12}")
            else:
                print("No recent orders found")
                
        except Exception as e:
            print(f"❌ Error getting orders: {e}")
        
        # AI-powered portfolio insights (if available)
        if bot.ai and bot.ai.is_configured:
            print("\n🤖 AI PORTFOLIO INSIGHTS")
            print("-" * 50)
            
            try:
                # Get current positions symbols for analysis
                position_symbols = []
                try:
                    positions = bot.trading_client.get_all_positions()
                    position_symbols = [pos.symbol for pos in positions]
                except:
                    pass
                
                if position_symbols:
                    print("🔍 Analyzing current positions with AI...")
                    
                    # Create a summary prompt
                    symbols_text = ", ".join(position_symbols[:5])  # Limit to first 5
                    
                    ai_prompt = f"""
                    Analyze this portfolio of stocks: {symbols_text}
                    
                    Provide:
                    1. Overall portfolio assessment
                    2. Risk factors to watch
                    3. Potential opportunities
                    4. Recommendations for portfolio balance
                    
                    Keep response concise (max 200 words).
                    """
                    
                    ai_response = bot.ai._call_ai_sync(ai_prompt, max_tokens=300)
                    
                    if ai_response:
                        print("💡 AI Analysis:")
                        print(ai_response)
                    else:
                        print("AI analysis unavailable")
                else:
                    print("No positions to analyze")
                    
            except Exception as e:
                print(f"❌ AI analysis failed: {e}")
        
        print(f"\n✅ Portfolio analysis completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
    except Exception as e:
        print(f"❌ Portfolio analysis failed: {e}")
        return False
    
    return True

if __name__ == "__main__":
    analyze_portfolio()