#!/usr/bin/env python3
"""
Simple Trading Performance Analysis
Shows current portfolio status, positions, and basic performance metrics.
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

def analyze_trading_performance():
    """Analyze current trading performance using Alpaca API"""
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        from alpaca.trading.enums import QueryOrderStatus
        
        print("📊 TRADING BOT PERFORMANCE ANALYSIS")
        print("=" * 60)
        
        # Initialize Alpaca client
        trading_api_key = os.getenv("ALPACA_API_KEY")
        trading_secret_key = os.getenv("ALPACA_API_SECRET")
        
        if not trading_api_key or not trading_secret_key:
            print("❌ Alpaca API keys not found in .env file")
            return
        
        trading_client = TradingClient(trading_api_key, trading_secret_key, paper=True)
        
        # 1. ACCOUNT OVERVIEW
        print("\n💼 ACCOUNT OVERVIEW")
        print("-" * 25)
        
        account = trading_client.get_account()
        portfolio_value = float(account.portfolio_value)
        cash = float(account.cash)
        equity = float(account.equity)
        buying_power = float(account.buying_power)
        
        # Safe attribute access with fallbacks
        day_change = 0
        try:
            if hasattr(account, 'plpc'):
                day_change = float(account.plpc) * 100
            elif hasattr(account, 'todays_plpc'):
                day_change = float(account.todays_plpc) * 100
        except:
            pass
        
        print(f"Portfolio Value: ${portfolio_value:,.2f}")
        print(f"Cash Available: ${cash:,.2f} ({cash/portfolio_value*100:.1f}%)")
        print(f"Equity: ${equity:,.2f}")
        print(f"Buying Power: ${buying_power:,.2f}")
        if day_change != 0:
            print(f"Day P&L: {day_change:+.2f}%")
        print(f"All-time P&L: ${portfolio_value - 100000:+,.2f}")
        
        # 2. CURRENT POSITIONS
        print(f"\n📈 CURRENT POSITIONS")
        print("-" * 25)
        
        positions = trading_client.get_all_positions()
        if not positions:
            print("   No current positions")
        else:
            print(f"Total Positions: {len(positions)}")
            print("\nSymbol | Qty   | Avg Cost | Current  | Value     | P&L      | P&L%")
            print("-" * 70)
            
            total_unrealized = 0
            winners = 0
            losers = 0
            
            # Sort positions by market value (largest first)
            positions_sorted = sorted(positions, key=lambda x: abs(float(x.market_value)), reverse=True)
            
            for pos in positions_sorted:
                symbol = pos.symbol
                qty = float(pos.qty)
                avg_cost = float(pos.avg_entry_price)
                current_price = float(pos.current_price) if pos.current_price else 0
                market_value = float(pos.market_value)
                unrealized_pl = float(pos.unrealized_pl)
                unrealized_plpc = float(pos.unrealized_plpc) * 100
                
                total_unrealized += unrealized_pl
                if unrealized_pl > 0:
                    winners += 1
                elif unrealized_pl < 0:
                    losers += 1
                
                print(f"{symbol:6} | {qty:5.0f} | ${avg_cost:7.2f} | ${current_price:7.2f} | ${market_value:8.2f} | ${unrealized_pl:+7.2f} | {unrealized_plpc:+5.1f}%")
            
            print("-" * 70)
            print(f"Total Unrealized P&L: ${total_unrealized:+,.2f}")
            print(f"Winning Positions: {winners}, Losing Positions: {losers}")
            if winners + losers > 0:
                win_rate = winners / (winners + losers) * 100
                print(f"Position Win Rate: {win_rate:.1f}%")
        
        # 3. RECENT ORDERS
        print(f"\n📋 RECENT ORDERS (Last 7 days)")
        print("-" * 35)
        
        try:
            from alpaca.trading.requests import GetOrdersRequest
            
            # Get recent orders
            get_orders_request = GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                limit=50
            )
            orders = trading_client.get_orders(get_orders_request)
            
            # Filter orders from last 7 days
            week_ago = datetime.now() - timedelta(days=7)
            recent_orders = [order for order in orders if order.created_at.replace(tzinfo=None) > week_ago]
            
            if not recent_orders:
                print("   No recent orders")
            else:
                print(f"Total Orders (7 days): {len(recent_orders)}")
                
                # Separate by order type
                buy_orders = [o for o in recent_orders if o.side.value == 'buy' and o.status.value == 'filled']
                sell_orders = [o for o in recent_orders if o.side.value == 'sell' and o.status.value == 'filled']
                
                print(f"Buy Orders: {len(buy_orders)}")
                print(f"Sell Orders: {len(sell_orders)}")
                
                print(f"\nRecent Filled Orders:")
                print("Date     | Symbol | Side | Qty   | Price   | Total")
                print("-" * 55)
                
                for order in recent_orders[:15]:  # Show last 15 orders
                    if order.status.value == 'filled':
                        date = order.filled_at.strftime('%m/%d %H:%M') if order.filled_at else order.created_at.strftime('%m/%d %H:%M')
                        symbol = order.symbol
                        side = order.side.value
                        qty = float(order.filled_qty or order.qty)
                        price = float(order.filled_avg_price or order.limit_price or 0)
                        total = qty * price
                        
                        print(f"{date} | {symbol:6} | {side:4} | {qty:5.0f} | ${price:6.2f} | ${total:7.2f}")
        
        except Exception as e:
            print(f"   Could not fetch order history: {e}")
        
        # 4. PORTFOLIO PERFORMANCE OVER TIME
        print(f"\n📊 PORTFOLIO PERFORMANCE (Last 30 days)")
        print("-" * 45)
        
        try:
            # Get portfolio history
            portfolio_history_request = GetPortfolioHistoryRequest(
                period="1M",  # 1 month
                timeframe="1D"  # Daily
            )
            
            portfolio_history = trading_client.get_portfolio_history(portfolio_history_request)
            
            if portfolio_history and portfolio_history.equity:
                equity_values = portfolio_history.equity
                timestamps = portfolio_history.timestamp
                
                if len(equity_values) >= 2:
                    # Calculate performance metrics
                    start_value = equity_values[0]
                    end_value = equity_values[-1]
                    total_return = ((end_value - start_value) / start_value) * 100
                    
                    # Find peak and trough
                    max_value = max(equity_values)
                    min_value = min(equity_values)
                    
                    print(f"30-Day Performance: {total_return:+.2f}%")
                    print(f"Starting Value: ${start_value:,.2f}")
                    print(f"Current Value: ${end_value:,.2f}")
                    print(f"Peak Value: ${max_value:,.2f}")
                    print(f"Lowest Value: ${min_value:,.2f}")
                    
                    # Calculate max drawdown
                    peak = equity_values[0]
                    max_drawdown = 0
                    for value in equity_values:
                        if value > peak:
                            peak = value
                        drawdown = (peak - value) / peak * 100
                        if drawdown > max_drawdown:
                            max_drawdown = drawdown
                    
                    print(f"Max Drawdown: -{max_drawdown:.2f}%")
                else:
                    print("   Insufficient history data")
            else:
                print("   No portfolio history available")
                
        except Exception as e:
            print(f"   Could not fetch portfolio history: {e}")
        
        # 5. SUMMARY & RECOMMENDATIONS
        print(f"\n💡 PERFORMANCE SUMMARY")
        print("-" * 25)
        
        cash_percentage = cash / portfolio_value * 100
        
        if cash_percentage > 80:
            print("⚠️  High cash allocation - consider increasing position sizes")
        elif cash_percentage < 10:
            print("⚠️  Low cash reserves - consider taking some profits")
        else:
            print("✅ Balanced cash allocation")
        
        if positions:
            # Position concentration analysis
            position_values = [abs(float(pos.market_value)) for pos in positions]
            total_position_value = sum(position_values)
            largest_position_pct = max(position_values) / portfolio_value * 100
            
            if largest_position_pct > 20:
                print("⚠️  High concentration risk - largest position is {:.1f}% of portfolio".format(largest_position_pct))
            else:
                print("✅ Good diversification")
            
            # P&L analysis
            if total_unrealized > 0:
                print("📈 Currently profitable - consider taking some profits")
            elif total_unrealized < -1000:
                print("📉 Significant unrealized losses - review stop-loss strategy")
        
        print(f"\n🎉 Analysis completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
    except Exception as e:
        print(f"❌ Failed to analyze performance: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    analyze_trading_performance()