"""Example: Using the isolated trading modules

This script demonstrates how to use the new trading logic modules
independently of the full SmartBot system.
"""

import asyncio
import pandas as pd
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.trading.strategy import TechnicalStrategy, StrategyConfig
from src.trading.signals import SignalGenerator
from src.trading.position_sizing import PositionSizer
from src.trading.execution import OrderExecutor


def create_mock_price_data(symbol: str, days: int = 100) -> pd.DataFrame:
    """Create mock price data for testing"""
    import numpy as np
    
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=days, freq='D')
    
    # Generate random walk price data
    np.random.seed(42)
    prices = 100 + np.cumsum(np.random.randn(days) * 2)
    
    df = pd.DataFrame({
        'timestamp': dates,
        'open': prices + np.random.randn(days) * 0.5,
        'high': prices + abs(np.random.randn(days)) * 1.5,
        'low': prices - abs(np.random.randn(days)) * 1.5,
        'close': prices,
        'volume': np.random.randint(1000000, 5000000, days)
    })
    
    return df


async def example_1_basic_strategy():
    """Example 1: Basic strategy evaluation"""
    print("\n" + "="*60)
    print("EXAMPLE 1: Basic Strategy Evaluation")
    print("="*60)
    
    # Create strategy with default config
    strategy = TechnicalStrategy()
    
    # Simulate current indicator values
    sma_fast = 152.5
    sma_slow = 148.0
    rsi = 35.2
    
    # Evaluate signal
    signal, strength = strategy.evaluate_signal(sma_fast, sma_slow, rsi)
    
    print(f"\nIndicator Values:")
    print(f"  SMA Fast: {sma_fast}")
    print(f"  SMA Slow: {sma_slow}")
    print(f"  RSI: {rsi}")
    print(f"\nSignal: {signal}")
    print(f"Strength: {strength}")
    print("\n" + "="*60)


async def example_2_custom_strategy():
    """Example 2: Custom strategy configuration"""
    print("\n" + "="*60)
    print("EXAMPLE 2: Custom Strategy Configuration")
    print("="*60)
    
    # Create custom strategy config
    config = StrategyConfig(
        sma_fast=10,
        sma_slow=30,
        rsi_buy_threshold=35,
        rsi_sell_threshold=65,
        max_position_pct=0.20,  # 20% max position
        stop_loss_pct=0.03,  # 3% stop loss
        take_profit_pct=0.08  # 8% take profit
    )
    
    strategy = TechnicalStrategy(config)
    
    print(f"\nCustom Strategy Parameters:")
    print(f"  SMA Fast: {config.sma_fast}")
    print(f"  SMA Slow: {config.sma_slow}")
    print(f"  RSI Buy Threshold: {config.rsi_buy_threshold}")
    print(f"  RSI Sell Threshold: {config.rsi_sell_threshold}")
    print(f"  Max Position: {config.max_position_pct:.0%}")
    print(f"  Stop Loss: {config.stop_loss_pct:.0%}")
    print(f"  Take Profit: {config.take_profit_pct:.0%}")
    
    # Test stop loss logic
    should_exit, reason = strategy.should_exit_position(
        entry_price=100.0,
        current_price=96.5
    )
    
    print(f"\nPosition Management:")
    print(f"  Entry Price: $100.00")
    print(f"  Current Price: $96.50")
    print(f"  Should Exit: {should_exit}")
    print(f"  Reason: {reason}")
    print("\n" + "="*60)


async def example_3_signal_generation():
    """Example 3: Full signal generation pipeline"""
    print("\n" + "="*60)
    print("EXAMPLE 3: Signal Generation Pipeline")
    print("="*60)
    
    # Create strategy and signal generator
    strategy = TechnicalStrategy()
    signal_gen = SignalGenerator(
        strategy=strategy,
        ai_agent=None,  # No AI for this example
        use_ai_enhancement=False
    )
    
    # Create mock price data
    symbol = 'AAPL'
    price_data = create_mock_price_data(symbol, days=100)
    
    print(f"\nAnalyzing {symbol}...")
    print(f"Price Data: {len(price_data)} days")
    print(f"Latest Close: ${price_data['close'].iloc[-1]:.2f}")
    
    # Analyze symbol
    analysis = await signal_gen.analyze_symbol(symbol, price_data)
    
    if analysis:
        print(f"\nAnalysis Result:")
        print(f"  Symbol: {analysis['symbol']}")
        print(f"  Price: ${analysis['price']:.2f}")
        print(f"  SMA Fast: {analysis['sma_fast']:.2f}")
        print(f"  SMA Slow: {analysis['sma_slow']:.2f}")
        print(f"  RSI: {analysis['rsi']:.2f}")
        print(f"  Signal: {analysis['signal']}")
        print(f"  Strength: {analysis['signal_strength']}")
    else:
        print("\n❌ No signal generated (insufficient data or no signal)")
    
    print("\n" + "="*60)


async def example_4_position_sizing():
    """Example 4: Position sizing calculations"""
    print("\n" + "="*60)
    print("EXAMPLE 4: Position Sizing Calculations")
    print("="*60)
    
    # Create position sizer
    sizer = PositionSizer(
        max_position_pct=0.15,  # 15% max per position
        reserve_cash_pct=0.20,  # Keep 20% in reserves
        min_order_value=100.0
    )
    
    # Portfolio state
    symbol = 'AAPL'
    price = 175.50
    available_cash = 10000
    portfolio_value = 50000
    existing_position_value = 2000
    reserved_cash = 500
    
    print(f"\nPortfolio State:")
    print(f"  Total Value: ${portfolio_value:,.2f}")
    print(f"  Available Cash: ${available_cash:,.2f}")
    print(f"  Reserved for Orders: ${reserved_cash:,.2f}")
    print(f"  Existing {symbol} Position: ${existing_position_value:,.2f}")
    
    print(f"\nCalculating position size for {symbol} @ ${price:.2f}...")
    
    # Calculate buy quantity
    qty, reason = sizer.calculate_buy_quantity(
        symbol=symbol,
        price=price,
        available_cash=available_cash,
        portfolio_value=portfolio_value,
        existing_position_value=existing_position_value,
        reserved_cash=reserved_cash
    )
    
    if qty > 0:
        order_value = qty * price
        new_position_value = existing_position_value + order_value
        position_pct = (new_position_value / portfolio_value) * 100
        
        print(f"\n✅ Position Sizing Result:")
        print(f"  Quantity: {qty} shares")
        print(f"  Order Value: ${order_value:,.2f}")
        print(f"  New Position Value: ${new_position_value:,.2f}")
        print(f"  Position %: {position_pct:.2f}% of portfolio")
    else:
        print(f"\n❌ Cannot buy: {reason}")
    
    # Get portfolio metrics
    positions = {
        'AAPL': 7000,
        'MSFT': 5500,
        'GOOGL': 3200,
        'TSLA': 2800
    }
    
    metrics = sizer.get_portfolio_metrics(
        cash=available_cash,
        portfolio_value=portfolio_value,
        positions=positions
    )
    
    print(f"\nPortfolio Metrics:")
    print(f"  Position Count: {metrics['position_count']}")
    print(f"  Cash %: {metrics['cash_pct']:.1f}%")
    print(f"  Invested %: {metrics['invested_pct']:.1f}%")
    print(f"  Concentration: {metrics['concentration']:.1f}% (largest position)")
    print(f"  Usable Cash: ${metrics['usable_cash']:,.2f}")
    
    if metrics['oversized_positions']:
        print(f"  ⚠️  Oversized Positions: {list(metrics['oversized_positions'].keys())}")
    
    print("\n" + "="*60)


async def example_5_dry_run_execution():
    """Example 5: Dry-run order execution"""
    print("\n" + "="*60)
    print("EXAMPLE 5: Dry-Run Order Execution")
    print("="*60)
    
    # Create executor in dry-run mode (no actual trading)
    executor = OrderExecutor(
        trading_client=None,  # Not needed for dry-run
        db_client=None,
        dry_run=True
    )
    
    # Execute a buy order (dry-run)
    print("\nExecuting BUY order (dry-run)...")
    success, error = executor.execute_buy(
        symbol='AAPL',
        quantity=40,
        price=175.50,
        reason='Technical BUY signal: SMA crossover + RSI oversold',
        analysis={
            'rsi': 38.2,
            'sma_fast': 152.5,
            'sma_slow': 148.0,
            'signal_strength': 'STRONG'
        }
    )
    
    print(f"Success: {success}")
    if error:
        print(f"Error: {error}")
    
    # Execute a sell order (dry-run)
    print("\nExecuting SELL order (dry-run)...")
    success, error = executor.execute_sell(
        symbol='MSFT',
        quantity=25,
        price=380.25,
        reason='Take profit: +8.5%',
        analysis={
            'rsi': 68.1,
            'sma_fast': 375.2,
            'sma_slow': 378.5,
            'signal_strength': 'MEDIUM'
        }
    )
    
    print(f"Success: {success}")
    if error:
        print(f"Error: {error}")
    
    print("\n" + "="*60)


async def main():
    """Run all examples"""
    print("\n" + "🚀 "* 20)
    print("TRADING MODULES EXAMPLES")
    print("🚀 " * 20)
    
    await example_1_basic_strategy()
    await example_2_custom_strategy()
    await example_3_signal_generation()
    await example_4_position_sizing()
    await example_5_dry_run_execution()
    
    print("\n" + "✅ " * 20)
    print("ALL EXAMPLES COMPLETED")
    print("✅ " * 20 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
