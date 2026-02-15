#!/usr/bin/env python3
"""
Backtesting Framework for Trading Bot
=====================================
Test strategies on historical data before live trading.

Usage:
    python3 backtest.py                    # Run with defaults
    python3 backtest.py --tickers AAPL     # Test specific tickers
    python3 backtest.py --days 365         # 1 year backtest
    python3 backtest.py --initial-capital 100000
"""

import os
import sys
import argparse
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json

# Load environment variables
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

import pandas as pd
import numpy as np
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class Backtester:
    """Backtesting framework for the trading strategy"""
    
    def __init__(self, 
                 initial_capital: float = 100000,
                 sma_fast: int = 10,
                 sma_slow: int = 30,
                 rsi_period: int = 14,
                 rsi_buy_threshold: float = 30,
                 rsi_sell_threshold: float = 70):
        """Initialize backtester with strategy parameters"""
        
        # API credentials
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.api_secret = os.getenv("ALPACA_API_SECRET")
        
        if not self.api_key or not self.api_secret:
            raise ValueError("Missing Alpaca API credentials")
        
        self.data_client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.api_secret
        )
        
        # Strategy parameters
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.sma_fast = sma_fast
        self.sma_slow = sma_slow
        self.rsi_period = rsi_period
        self.rsi_buy_threshold = rsi_buy_threshold
        self.rsi_sell_threshold = rsi_sell_threshold
        
        # Track positions and trades
        self.positions = {}  # {symbol: {'qty': int, 'entry_price': float}}
        self.trades = []
        self.equity_curve = []
        
        # Default tickers to test
        self.tickers = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'JPM',
            'V', 'JNJ', 'WMT', 'PG', 'MA', 'UNH', 'HD', 'DIS', 'PYPL',
            'NFLX', 'ADBE', 'CRM', 'INTC', 'VZ', 'T', 'PFE', 'XOM', 'CVX'
        ]
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators"""
        if len(df) < self.sma_slow:
            return df
        
        # SMAs
        df[f'SMA_{self.sma_fast}'] = df['close'].rolling(window=self.sma_fast).mean()
        df[f'SMA_{self.sma_slow}'] = df['close'].rolling(window=self.sma_slow).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        return df
    
    def get_historical_data(self, symbol: str, days: int = 365) -> Optional[pd.DataFrame]:
        """Fetch historical data for backtesting"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            request = StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Day,
                start=start_date,
                end=end_date
            )
            
            barset = self.data_client.get_stock_bars(request)
            
            if not barset or symbol not in barset.data:
                return None
            
            bars = barset.data[symbol]
            if not bars:
                return None
            
            df = pd.DataFrame([{
                'date': bar.timestamp.date() if hasattr(bar.timestamp, 'date') else bar.timestamp,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume
            } for bar in bars])
            
            return df
            
        except Exception as e:
            logger.warning(f"Failed to fetch data for {symbol}: {e}")
            return None
    
    def generate_signal(self, df: pd.DataFrame, lookback_idx: int) -> Optional[str]:
        """Generate trading signal based on technical indicators"""
        if lookback_idx < self.sma_slow:
            return None
        
        # Need at least self.sma_slow days of data
        historical = df.iloc[:lookback_idx+1]
        if len(historical) < self.sma_slow:
            return None
        
        # Calculate indicators up to this point
        test_df = historical.copy()
        test_df = self.calculate_indicators(test_df)
        
        if len(test_df) < self.sma_slow:
            return None
        
        latest = test_df.iloc[-1]
        
        if pd.isna(latest[f'SMA_{self.sma_fast}']) or pd.isna(latest['RSI']):
            return None
        
        sma_fast = latest[f'SMA_{self.sma_fast}']
        sma_slow = latest[f'SMA_{self.sma_slow}']
        rsi = latest['RSI']
        
        # Buy signal
        if sma_fast > sma_slow and rsi < self.rsi_buy_threshold:
            return "BUY"
        # Sell signal
        elif sma_fast < sma_slow and rsi > self.rsi_sell_threshold:
            return "SELL"
        
        return None
    
    def calculate_position_size(self, price: float) -> int:
        """Calculate position size (simple: use 10% of capital)"""
        allocation = self.cash * 0.10  # 10% of available cash
        qty = int(allocation / price)
        return max(qty, 1)
    
    def execute_trade(self, symbol: str, side: str, price: float, date: str):
        """Execute a trade in the backtest"""
        if side == "BUY":
            qty = self.calculate_position_size(price)
            cost = qty * price
            
            if cost > self.cash:
                return False
            
            self.cash -= cost
            self.positions[symbol] = {
                'qty': qty,
                'entry_price': price,
                'entry_date': date
            }
            
            self.trades.append({
                'date': date,
                'symbol': symbol,
                'side': side,
                'qty': qty,
                'price': price,
                'value': cost
            })
            
            return True
            
        elif side == "SELL" and symbol in self.positions:
            position = self.positions[symbol]
            qty = position['qty']
            proceeds = qty * price
            pnl = proceeds - (qty * position['entry_price'])
            
            self.cash += proceeds
            
            self.trades.append({
                'date': date,
                'symbol': symbol,
                'side': side,
                'qty': qty,
                'price': price,
                'value': proceeds,
                'pnl': pnl,
                'return_pct': (pnl / (qty * position['entry_price'])) * 100
            })
            
            del self.positions[symbol]
            return True
        
        return False
    
    def run_backtest(self, tickers: List[str] = None, days: int = 365) -> Dict:
        """Run the backtest on given tickers"""
        
        if tickers:
            self.tickers = tickers
        
        logger.info(f"🚀 Starting backtest: {days} days, {len(self.tickers)} tickers")
        logger.info(f"💰 Initial Capital: ${self.initial_capital:,.2f}")
        
        # Track results
        total_trades = 0
        winning_trades = 0
        losing_trades = 0
        total_pnl = 0
        trades_by_symbol = {}
        
        # Process each ticker
        for symbol in self.tickers:
            logger.info(f"📊 Testing {symbol}...")
            
            df = self.get_historical_data(symbol, days)
            if df is None or len(df) < self.sma_slow + 10:
                continue
            
            df = self.calculate_indicators(df)
            
            # Simulate trading through history
            for idx in range(self.sma_slow, len(df) - 1):
                date = df.iloc[idx]['date']
                current_price = df.iloc[idx]['close']
                
                # Check for exit signal on existing position
                if symbol in self.positions:
                    signal = self.generate_signal(df, idx)
                    if signal == "SELL":
                        self.execute_trade(symbol, "SELL", current_price, date)
                
                # Check for entry signal
                else:
                    signal = self.generate_signal(df, idx)
                    if signal == "BUY":
                        self.execute_trade(symbol, "BUY", current_price, date)
                
                # Record equity
                position_value = sum(
                    p['qty'] * df.iloc[idx]['close'] 
                    for p in self.positions.values()
                )
                self.equity_curve.append({
                    'date': date,
                    'equity': self.cash + position_value
                })
        
        # Close all positions at the end
        final_date = df.iloc[-1]['date']
        final_price = df.iloc[-1]['close']
        for symbol in list(self.positions.keys()):
            self.execute_trade(symbol, "SELL", final_price, final_date)
        
        # Calculate metrics
        for trade in self.trades:
            if trade['side'] == 'SELL' and 'pnl' in trade:
                total_trades += 1
                total_pnl += trade['pnl']
                
                if trade['pnl'] > 0:
                    winning_trades += 1
                else:
                    losing_trades += 1
                
                symbol = trade['symbol']
                if symbol not in trades_by_symbol:
                    trades_by_symbol[symbol] = {'wins': 0, 'losses': 0}
                if trade['pnl'] > 0:
                    trades_by_symbol[symbol]['wins'] += 1
                else:
                    trades_by_symbol[symbol]['losses'] += 1
        
        # Calculate metrics
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        avg_win = total_pnl / total_trades if total_trades > 0 else 0
        
        # Calculate max drawdown
        max_drawdown = 0
        peak = self.initial_capital
        for point in self.equity_curve:
            if point['equity'] > peak:
                peak = point['equity']
            drawdown = (peak - point['equity']) / peak * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # Calculate Sharpe ratio (simplified)
        if len(self.equity_curve) > 1:
            returns = []
            for i in range(1, len(self.equity_curve)):
                ret = (self.equity_curve[i]['equity'] - self.equity_curve[i-1]['equity']) / self.equity_curve[i-1]['equity']
                returns.append(ret)
            
            if returns and np.std(returns) > 0:
                sharpe_ratio = (np.mean(returns) / np.std(returns)) * np.sqrt(252)  # Annualized
            else:
                sharpe_ratio = 0
        else:
            sharpe_ratio = 0
        
        # Final capital
        final_capital = self.cash
        
        results = {
            'initial_capital': self.initial_capital,
            'final_capital': final_capital,
            'total_return_pct': ((final_capital - self.initial_capital) / self.initial_capital) * 100,
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'avg_profit': avg_win,
            'total_pnl': total_pnl,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'trades_by_symbol': trades_by_symbol
        }
        
        return results
    
    def print_results(self, results: Dict):
        """Print backtest results in a nice format"""
        
        print("\n" + "="*60)
        print("📈 BACKTEST RESULTS")
        print("="*60)
        
        print(f"\n💰 CAPITAL")
        print(f"   Initial:    ${results['initial_capital']:>12,.2f}")
        print(f"   Final:      ${results['final_capital']:>12,.2f}")
        print(f"   Return:     {results['total_return_pct']:>12.2f}%")
        
        print(f"\n📊 TRADING STATS")
        print(f"   Total Trades:     {results['total_trades']:>8}")
        print(f"   Winners:          {results['winning_trades']:>8}")
        print(f"   Losers:           {results['losing_trades']:>8}")
        print(f"   Win Rate:        {results['win_rate']:>8.1f}%")
        
        print(f"\n💵 PROFIT/LOSS")
        print(f"   Total P&L:        ${results['total_pnl']:>12,.2f}")
        print(f"   Average Profit:   ${results['avg_profit']:>12,.2f}")
        
        print(f"\n📉 RISK METRICS")
        print(f"   Max Drawdown:     {results['max_drawdown']:>8.2f}%")
        print(f"   Sharpe Ratio:     {results['sharpe_ratio']:>8.2f}")
        
        print(f"\n🔝 TOP PERFORMING SYMBOLS")
        if results['trades_by_symbol']:
            sorted_symbols = sorted(
                results['trades_by_symbol'].items(),
                key=lambda x: x[1]['wins'] - x[1]['losses'],
                reverse=True
            )[:5]
            for symbol, stats in sorted_symbols:
                print(f"   {symbol}: {stats['wins']}W / {stats['losses']}L")
        
        print("\n" + "="*60)
        
        # Interpretation
        print("\n📋 INTERPRETATION:")
        if results['win_rate'] > 50:
            print("   ✅ Win rate above 50% - strategy has positive edge")
        else:
            print("   ⚠️  Win rate below 50% - consider adjusting strategy")
        
        if results['sharpe_ratio'] > 1:
            print("   ✅ Good risk-adjusted returns (Sharpe > 1)")
        elif results['sharpe_ratio'] > 0:
            print("   ⚠️  Low but positive risk-adjusted returns")
        else:
            print("   ❌ Poor risk-adjusted returns")
        
        if results['max_drawdown'] > 20:
            print("   ⚠️  High max drawdown (>20 stops%) - consider tighter")
        
        print()


def main():
    parser = argparse.ArgumentParser(description='Backtesting Framework for Trading Bot')
    
    parser.add_argument('--tickers', type=str, 
                       help='Comma-separated list of tickers to test (default: 26 popular stocks)')
    parser.add_argument('--days', type=int, default=365,
                       help='Number of days to backtest (default: 365)')
    parser.add_argument('--initial-capital', type=float, default=100000,
                       help='Initial capital for backtest (default: 100000)')
    parser.add_argument('--sma-fast', type=int, default=10,
                       help='Fast SMA period (default: 10)')
    parser.add_argument('--sma-slow', type=int, default=30,
                       help='Slow SMA period (default: 30)')
    parser.add_argument('--rsi-buy', type=float, default=30,
                       help='RSI buy threshold (default: 30)')
    parser.add_argument('--rsi-sell', type=float, default=70,
                       help='RSI sell threshold (default: 70)')
    
    args = parser.parse_args()
    
    # Parse tickers
    tickers = None
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(',')]
    
    # Create backtester and run
    backtester = Backtester(
        initial_capital=args.initial_capital,
        sma_fast=args.sma_fast,
        sma_slow=args.sma_slow,
        rsi_buy_threshold=args.rsi_buy,
        rsi_sell_threshold=args.rsi_sell
    )
    
    results = backtester.run_backtest(tickers=tickers, days=args.days)
    backtester.print_results(results)
    
    # Save results to file
    import json
    with open('backtest_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"💾 Results saved to backtest_results.json")


if __name__ == '__main__':
    main()
