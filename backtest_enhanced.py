#!/usr/bin/env python3
"""
Enhanced Backtesting System
==========================
Uses historical OHLCV data from Supabase to test trading strategies.

Usage:
    python3 backtest_enhanced.py                    # Run full backtest
    python3 backtest_enhanced.py --symbols AAPL,MSFT  # Specific symbols
    python3 backtest_enhanced.py --start 2023-01-01  # Custom date range
    python3 backtest_enhanced.py --compare          # Compare strategies
"""
import os
import sys
import argparse
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.data.historical_pipeline import HistoricalDataPipeline
from src.database.simple_rest import SimpleSupabaseREST


class EnhancedBacktester:
    """Enhanced backtesting with full strategy logic"""
    
    def __init__(self, initial_capital: float = 100000, db=None):
        self.initial_capital = initial_capital
        self.pipeline = HistoricalDataPipeline(db=db)
        
        # Strategy parameters (can be customized)
        self.sma_fast = 10
        self.sma_slow = 30
        self.rsi_period = 14
        self.rsi_buy = 30
        self.rsi_sell = 70
        self.stop_loss_pct = 8.0
        self.take_profit_pct = 15.0
        
        # Position management
        self.positions = {}  # {symbol: {'qty': int, 'entry_price': float}}
        self.cash = initial_capital
        self.portfolio_history = []
        self.trades = []
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators"""
        df = df.copy()
        
        # SMA
        df['SMA_fast'] = df['close'].rolling(window=self.sma_fast).mean()
        df['SMA_slow'] = df['close'].rolling(window=self.sma_slow).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # MACD
        exp12 = df['close'].ewm(span=12, adjust=False).mean()
        exp26 = df['close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp12 - exp26
        df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_histogram'] = df['MACD'] - df['MACD_signal']
        
        # Bollinger Bands
        df['BB_middle'] = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        df['BB_upper'] = df['BB_middle'] + (bb_std * 2)
        df['BB_lower'] = df['BB_middle'] - (bb_std * 2)
        
        # ATR
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR'] = true_range.rolling(window=14).mean()
        
        return df
    
    def generate_signal(self, row: pd.Series) -> Optional[str]:
        """Generate trading signal based on indicators"""
        # Skip if missing data
        if pd.isna(row.get('RSI')) or pd.isna(row.get('SMA_fast')) or pd.isna(row.get('SMA_slow')):
            return None
        
        rsi = row['RSI']
        price = row['close']
        sma_fast = row['SMA_fast']
        sma_slow = row['SMA_slow']
        
        # More permissive strategy for backtesting
        # BUY: RSI oversold OR SMA bullish
        if rsi < 40 or sma_fast > sma_slow:
            return 'BUY'
        
        # SELL: RSI overbought OR SMA bearish  
        if rsi > 60 or sma_fast < sma_slow:
            return 'SELL'
        
        return None
    
    def calculate_position_size(self, price: float) -> int:
        """Calculate position size (fixed $1000 per trade)"""
        trade_size = 1000
        return int(trade_size / price)
    
    def run_backtest(self, symbols: List[str], start_date: str = None, end_date: str = None) -> Dict:
        """Run backtest for given symbols and date range"""
        
        all_data = {}
        
        # Load data for each symbol
        for symbol in symbols:
            df = self.pipeline.get_ohlcv(symbol, start_date, end_date)
            if df is not None and len(df) > self.sma_slow:
                df = df.sort_values('date').reset_index(drop=True)
                df = self.calculate_indicators(df)
                all_data[symbol] = df
                logger.info(f"Loaded {len(df)} days for {symbol}")
        
        if not all_data:
            logger.error("No data loaded!")
            return {}
        
        # Find common date range
        min_date = max(df['date'].min() for df in all_data.values())
        max_date = min(df['date'].max() for df in all_data.values())
        
        logger.info(f"Backtesting from {min_date} to {max_date}")
        
        # Run simulation
        current_date = min_date
        while current_date <= max_date:
            # Check each symbol
            for symbol, df in all_data.items():
                # Handle date comparison - convert dates to comparable format
                df_check = df.copy()
                if pd.api.types.is_datetime64_any_dtype(df_check['date']):
                    # Compare using Timestamp for accuracy
                    row = df[df['date'] == pd.Timestamp(current_date)]
                else:
                    row = df[df['date'] == current_date]
                    
                if len(row) == 0:
                    continue
                row = row.iloc[0]
                
                # Skip if we have a position
                if symbol in self.positions:
                    # Check exit conditions - get a copy of position data
                    try:
                        pos_data = self.positions.get(symbol)
                        if pos_data is None:
                            continue
                            
                        signal = self.generate_signal(row)
                        if signal == 'SELL':
                            self.close_position(symbol, row['close'], current_date)
                        
                        # Check stop-loss
                        entry_price = pos_data['entry_price']
                        pnl_pct = (row['close'] - entry_price) / entry_price * 100
                        if pnl_pct < -self.stop_loss_pct:
                            self.close_position(symbol, row['close'], current_date, reason='stop_loss')
                        
                        # Check take-profit
                        if pnl_pct > self.take_profit_pct:
                            self.close_position(symbol, row['close'], current_date, reason='take_profit')
                    except Exception as e:
                        logger.debug(f"Error processing position for {symbol}: {e}")
                
                else:
                    # Check entry conditions
                    signal = self.generate_signal(row)
                    if signal == 'BUY':
                        self.open_position(symbol, row['close'], row.get('ATR', 0), current_date)
            
            # Record portfolio value
            portfolio_value = self.cash
            for symbol, pos in self.positions.items():
                if symbol in all_data:
                    symbol_data = all_data[symbol]
                    if pd.api.types.is_datetime64_any_dtype(symbol_data['date']):
                        price_row = symbol_data[symbol_data['date'] == pd.Timestamp(current_date)]
                    else:
                        price_row = symbol_data[symbol_data['date'] == current_date]
                    
                    if len(price_row) > 0:
                        portfolio_value += pos['qty'] * price_row['close'].iloc[0]
            
            self.portfolio_history.append({
                'date': current_date,
                'value': portfolio_value,
                'cash': self.cash
            })
            
            current_date += timedelta(days=1)
        
        return self.calculate_metrics()
    
    def open_position(self, symbol: str, price: float, atr: float, date):
        """Open a position"""
        qty = self.calculate_position_size(price)
        cost = qty * price
        
        if cost > self.cash:
            return
        
        self.cash -= cost
        self.positions[symbol] = {
            'qty': qty,
            'entry_price': price,
            'entry_date': date,
            'atr': atr
        }
        
        self.trades.append({
            'symbol': symbol,
            'type': 'BUY',
            'qty': qty,
            'price': price,
            'date': date
        })
    
    def close_position(self, symbol: str, price: float, date, reason: str = 'signal'):
        """Close a position"""
        if symbol not in self.positions:
            return
        
        pos = self.positions[symbol]
        proceeds = pos['qty'] * price
        self.cash += proceeds
        
        self.trades.append({
            'symbol': symbol,
            'type': 'SELL',
            'qty': pos['qty'],
            'price': price,
            'date': date,
            'reason': reason,
            'pnl': proceeds - (pos['qty'] * pos['entry_price'])
        })
        
        del self.positions[symbol]
    
    def calculate_metrics(self) -> Dict:
        """Calculate backtest performance metrics"""
        if not self.portfolio_history:
            return {}
        
        df = pd.DataFrame(self.portfolio_history)
        df['returns'] = df['value'].pct_change()
        
        # Basic metrics
        total_return = (df['value'].iloc[-1] - self.initial_capital) / self.initial_capital * 100
        sharpe_ratio = df['returns'].mean() / df['returns'].std() * np.sqrt(252) if df['returns'].std() > 0 else 0
        max_drawdown = (df['value'] / df['value'].cummax() - 1).min() * 100
        
        # Trade analysis
        trades_df = pd.DataFrame(self.trades)
        if len(trades_df) > 0 and 'pnl' in trades_df.columns:
            wins = len(trades_df[trades_df['pnl'] > 0])
            losses = len(trades_df[trades_df['pnl'] < 0])
            win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
            
            avg_win = trades_df[trades_df['pnl'] > 0]['pnl'].mean() if wins > 0 else 0
            avg_loss = abs(trades_df[trades_df['pnl'] < 0]['pnl'].mean()) if losses > 0 else 0
            profit_factor = (avg_win * wins) / (avg_loss * losses) if avg_loss > 0 and losses > 0 else 0
        else:
            win_rate = 0
            avg_win = 0
            avg_loss = 0
            profit_factor = 0
        
        return {
            'initial_capital': self.initial_capital,
            'final_value': df['value'].iloc[-1],
            'total_return_pct': total_return,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown_pct': max_drawdown,
            'total_trades': len(self.trades),
            'win_rate_pct': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'portfolio_history': df.to_dict('records')
        }
    
    def print_results(self, results: Dict):
        """Print backtest results"""
        print("\n" + "="*60)
        print("BACKTEST RESULTS")
        print("="*60)
        print(f"Initial Capital:     ${results.get('initial_capital', 0):,.2f}")
        print(f"Final Value:        ${results.get('final_value', 0):,.2f}")
        print(f"Total Return:       {results.get('total_return_pct', 0):.2f}%")
        print("-"*60)
        print(f"Sharpe Ratio:      {results.get('sharpe_ratio', 0):.2f}")
        print(f"Max Drawdown:      {results.get('max_drawdown_pct', 0):.2f}%")
        print("-"*60)
        print(f"Total Trades:      {results.get('total_trades', 0)}")
        print(f"Win Rate:          {results.get('win_rate_pct', 0):.1f}%")
        print(f"Avg Win:           ${results.get('avg_win', 0):.2f}")
        print(f"Avg Loss:          ${results.get('avg_loss', 0):.2f}")
        print(f"Profit Factor:     {results.get('profit_factor', 0):.2f}")
        print("="*60)


def main():
    parser = argparse.ArgumentParser(description='Enhanced Backtesting System')
    parser.add_argument('--symbols', type=str, default='AAPL,MSFT,GOOG,NVDA',
                       help='Comma-separated symbols to backtest')
    parser.add_argument('--start', type=str, default='2023-01-01',
                       help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, default='2024-01-01',
                       help='End date (YYYY-MM-DD)')
    parser.add_argument('--capital', type=float, default=100000,
                       help='Initial capital')
    
    args = parser.parse_args()
    
    symbols = [s.strip() for s in args.symbols.split(',')]
    
    # Initialize with database
    db = SimpleSupabaseREST()
    backtester = EnhancedBacktester(initial_capital=args.capital, db=db)
    
    logger.info(f"Running backtest for: {', '.join(symbols)}")
    logger.info(f"Date range: {args.start} to {args.end}")
    
    results = backtester.run_backtest(symbols, args.start, args.end)
    backtester.print_results(results)


if __name__ == '__main__':
    main()
