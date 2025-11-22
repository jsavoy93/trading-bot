#!/usr/bin/env python3
"""
Trading Bot Performance Analysis
Get detailed analysis of past trades, profits/losses, and performance metrics.
"""

import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

# Load environment variables
load_dotenv()

def analyze_database_performance():
    """Analyze performance using direct database queries"""
    try:
        from database.simple_rest import SimpleRESTClient
        
        print("📊 TRADING BOT PERFORMANCE ANALYSIS")
        print("=" * 60)
        
        # Initialize database client
        db = SimpleRESTClient()
        
        # Get recent trades
        print("\n📈 RECENT TRADING ACTIVITY")
        print("-" * 40)
        
        # Query recent trades
        recent_trades = db._execute_query("""
            SELECT symbol, side, quantity, price, executed_at, pnl
            FROM trades 
            WHERE executed_at >= NOW() - INTERVAL '30 days'
            ORDER BY executed_at DESC 
            LIMIT 20
        """)
        
        if recent_trades:
            print(f"📊 Last {len(recent_trades)} trades (30 days):")
            total_pnl = 0
            winners = 0
            losers = 0
            
            for trade in recent_trades:
                symbol = trade[0]
                side = trade[1]
                qty = trade[2]
                price = trade[3]
                date = trade[4].strftime('%m/%d %H:%M')
                pnl = float(trade[5]) if trade[5] else 0
                
                total_pnl += pnl
                if pnl > 0:
                    winners += 1
                elif pnl < 0:
                    losers += 1
                
                pnl_str = f"${pnl:+.2f}" if pnl != 0 else "$0.00"
                print(f"  {date} | {symbol:6} | {side:4} | {qty:4.0f} @ ${price:.2f} | {pnl_str}")
            
            print(f"\n📊 SUMMARY:")
            print(f"  Total P&L: ${total_pnl:.2f}")
            print(f"  Winners: {winners}, Losers: {losers}")
            win_rate = (winners / (winners + losers) * 100) if (winners + losers) > 0 else 0
            print(f"  Win Rate: {win_rate:.1f}%")
        else:
            print("   No recent trades found")
        
        # Get symbol performance
        print(f"\n🏆 TOP PERFORMING SYMBOLS (30 days)")
        print("-" * 45)
        
        symbol_performance = db._execute_query("""
            SELECT symbol, 
                   COUNT(*) as trade_count,
                   SUM(pnl) as total_pnl,
                   AVG(pnl) as avg_pnl,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winners,
                   COUNT(*) as total_trades
            FROM trades 
            WHERE executed_at >= NOW() - INTERVAL '30 days'
              AND pnl IS NOT NULL
            GROUP BY symbol
            HAVING COUNT(*) >= 2
            ORDER BY total_pnl DESC
            LIMIT 10
        """)
        
        if symbol_performance:
            print("Symbol | Trades | Total P&L | Avg P&L | Win Rate")
            print("-" * 50)
            for row in symbol_performance:
                symbol = row[0]
                trades = row[1]
                total_pnl = float(row[2])
                avg_pnl = float(row[3])
                winners = row[4]
                total = row[5]
                win_rate = (winners / total * 100) if total > 0 else 0
                
                print(f"{symbol:6} | {trades:6} | ${total_pnl:8.2f} | ${avg_pnl:7.2f} | {win_rate:6.1f}%")
        
        # Get session performance
        print(f"\n📅 RECENT SESSION PERFORMANCE")
        print("-" * 35)
        
        session_performance = db._execute_query("""
            SELECT DATE(start_time) as session_date,
                   COUNT(*) as sessions,
                   SUM(total_trades) as trades,
                   SUM(total_symbols_analyzed) as symbols_analyzed
            FROM trading_sessions 
            WHERE start_time >= NOW() - INTERVAL '7 days'
            GROUP BY DATE(start_time)
            ORDER BY session_date DESC
            LIMIT 7
        """)
        
        if session_performance:
            print("Date       | Sessions | Trades | Symbols")
            print("-" * 40)
            for row in session_performance:
                date = row[0].strftime('%m/%d/%Y')
                sessions = row[1]
                trades = row[2] or 0
                symbols = row[3] or 0
                print(f"{date} | {sessions:8} | {trades:6} | {symbols:7}")
        
        # Get portfolio analysis
        print(f"\n💼 CURRENT PORTFOLIO STATUS")
        print("-" * 30)
        
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import GetPortfolioHistoryRequest
            
            # Initialize Alpaca client
            trading_api_key = os.getenv("ALPACA_API_KEY")
            trading_secret_key = os.getenv("ALPACA_API_SECRET")
            
            if trading_api_key and trading_secret_key:
                trading_client = TradingClient(trading_api_key, trading_secret_key, paper=True)
                
                # Get account info
                account = trading_client.get_account()
                print(f"Portfolio Value: ${float(account.portfolio_value):,.2f}")
                print(f"Cash: ${float(account.cash):,.2f} ({float(account.cash)/float(account.portfolio_value)*100:.1f}%)")
                print(f"Day P&L: ${float(account.todays_plpc)*100:+.2f}%")
                print(f"All-time P&L: ${float(account.portfolio_value) - 100000:.2f}")
                
                # Get positions
                positions = trading_client.get_all_positions()
                if positions:
                    print(f"\n📈 CURRENT POSITIONS ({len(positions)}):")
                    print("Symbol | Qty   | Value    | P&L%")
                    print("-" * 35)
                    for pos in positions[:10]:  # Show top 10 positions
                        qty = float(pos.qty)
                        value = float(pos.market_value)
                        pnl_pct = float(pos.unrealized_plpc) * 100
                        print(f"{pos.symbol:6} | {qty:5.0f} | ${value:8.2f} | {pnl_pct:+5.1f}%")
            else:
                print("   Alpaca API keys not configured")
                
        except Exception as e:
            print(f"   Could not fetch portfolio data: {e}")
        
        print(f"\n🎉 Analysis completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
    except Exception as e:
        print(f"❌ Failed to analyze performance: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    analyze_database_performance()