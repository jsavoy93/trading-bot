"""Example: Using Pro Strategy Features

This demonstrates the enhanced strategy with MACD, ATR, and advanced signals.
"""

import asyncio
import pandas as pd
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
import numpy as np

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.trading.strategy import TechnicalStrategy, StrategyConfig
from src.trading.signals import SignalGenerator


def create_realistic_price_data(symbol: str, days: int = 250) -> pd.DataFrame:
    """Create realistic price data with trend and volatility"""
    np.random.seed(42)
    
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=days, freq='D')
    
    # Create trending price with volatility
    base_price = 100
    trend = np.linspace(0, 30, days)  # Upward trend
    noise = np.random.randn(days).cumsum() * 2
    prices = base_price + trend + noise
    
    # Create OHLCV
    df = pd.DataFrame({
        'timestamp': dates,
        'open': prices + np.random.randn(days) * 0.5,
        'high': prices + abs(np.random.randn(days)) * 1.5,
        'low': prices - abs(np.random.randn(days)) * 1.5,
        'close': prices,
        'volume': np.random.randint(1000000, 5000000, days)
    })
    
    return df


async def example_1_basic_vs_advanced():
    """Compare basic and advanced signal generation"""
    print("\n" + "="*70)
    print("EXAMPLE 1: Basic vs Advanced Signal Generation")
    print("="*70)
    
    # Create strategy
    strategy = TechnicalStrategy()
    
    # Create price data
    symbol = 'AAPL'
    price_data = create_realistic_price_data(symbol, days=250)
    
    # Calculate indicators
    df = strategy.calculate_indicators(price_data)
    latest = df.iloc[-1]
    
    print(f"\nAnalyzing {symbol} with {len(df)} days of data")
    print(f"Latest Close: ${latest['close']:.2f}")
    
    # BASIC SIGNAL (original method)
    print("\n" + "-"*70)
    print("📊 BASIC SIGNAL (SMA + RSI only)")
    print("-"*70)
    
    signal, strength = strategy.evaluate_signal(
        sma_fast=latest['SMA_20'],
        sma_slow=latest['SMA_50'],
        rsi=latest['RSI']
    )
    
    print(f"  SMA Fast (20): ${latest['SMA_20']:.2f}")
    print(f"  SMA Slow (50): ${latest['SMA_50']:.2f}")
    print(f"  RSI: {latest['RSI']:.2f}")
    print(f"\n  → Signal: {signal or 'NONE'}")
    print(f"  → Strength: {strength}")
    
    # ADVANCED SIGNAL (pro method)
    print("\n" + "-"*70)
    print("🎯 ADVANCED SIGNAL (Multi-Indicator Analysis)")
    print("-"*70)
    
    signal_adv, strength_adv, reasons = strategy.evaluate_signal_advanced(latest)
    
    print(f"  SMA Fast (20): ${latest['SMA_20']:.2f}")
    print(f"  SMA Slow (50): ${latest['SMA_50']:.2f}")
    print(f"  SMA Trend (200): ${latest['SMA_200']:.2f}")
    print(f"  RSI: {latest['RSI']:.2f}")
    print(f"  MACD: {latest['MACD']:.4f}")
    print(f"  MACD Signal: {latest['MACD_signal']:.4f}")
    print(f"  MACD Histogram: {latest['MACD_hist']:.4f}")
    print(f"  ATR: ${latest['ATR']:.2f}")
    
    print(f"\n  → Signal: {signal_adv or 'NONE'}")
    print(f"  → Strength: {strength_adv}")
    print(f"\n  📋 Diagnostic Reasons:")
    for reason in reasons:
        print(f"     • {reason}")
    
    print("\n" + "="*70)


async def example_2_atr_exits():
    """Demonstrate ATR-based exit logic"""
    print("\n" + "="*70)
    print("EXAMPLE 2: ATR-Based Exit Management")
    print("="*70)
    
    strategy = TechnicalStrategy()
    
    # Simulate a position
    entry_price = 100.0
    entry_atr = 2.5
    
    print(f"\nPosition Details:")
    print(f"  Entry Price: ${entry_price:.2f}")
    print(f"  Entry ATR: ${entry_atr:.2f}")
    print(f"  ATR Stop Multiple: {strategy.config.atr_stop_multiple}x")
    print(f"  ATR Take Multiple: {strategy.config.atr_take_multiple}x")
    
    # Calculate levels
    stop_price = entry_price - (strategy.config.atr_stop_multiple * entry_atr)
    take_price = entry_price + (strategy.config.atr_take_multiple * entry_atr)
    
    print(f"\n  → Stop Loss Level: ${stop_price:.2f}")
    print(f"  → Take Profit Level: ${take_price:.2f}")
    
    # Test different scenarios
    scenarios = [
        (95.0, "Small loss"),
        (94.5, "Near stop loss"),
        (94.0, "Stop loss triggered"),
        (103.0, "Small profit"),
        (107.0, "Near take profit"),
        (108.0, "Take profit triggered"),
    ]
    
    print("\n" + "-"*70)
    print("Testing Exit Scenarios:")
    print("-"*70)
    
    for current_price, description in scenarios:
        should_exit, reason = strategy.should_exit_position_advanced(
            entry_price=entry_price,
            current_price=current_price,
            direction='LONG',
            entry_atr=entry_atr
        )
        
        pnl_pct = (current_price - entry_price) / entry_price * 100
        status = "🚨 EXIT" if should_exit else "✅ HOLD"
        
        print(f"\n  Price: ${current_price:.2f} ({pnl_pct:+.1f}%) - {description}")
        print(f"    {status}")
        if reason:
            print(f"    Reason: {reason}")
    
    print("\n" + "="*70)


async def example_3_atr_position_sizing():
    """Demonstrate ATR-based position sizing"""
    print("\n" + "="*70)
    print("EXAMPLE 3: ATR-Based Position Sizing")
    print("="*70)
    
    strategy = TechnicalStrategy()
    
    portfolio_value = 100000
    
    print(f"\nPortfolio Value: ${portfolio_value:,}")
    print(f"Risk Per Trade: {strategy.config.risk_per_trade_pct:.1%}")
    print(f"Max Position: {strategy.config.max_position_pct:.0%}")
    
    # Test different stocks with different ATRs
    stocks = [
        ('AAPL', 175.00, 2.50, 'Low volatility tech'),
        ('NVDA', 450.00, 15.00, 'High volatility tech'),
        ('JNJ', 160.00, 1.80, 'Stable blue chip'),
        ('TSLA', 240.00, 12.00, 'Very high volatility'),
    ]
    
    print("\n" + "-"*70)
    print("Position Sizing Results:")
    print("-"*70)
    
    for symbol, price, atr, description in stocks:
        # Calculate position size
        shares = strategy.compute_position_size(
            portfolio_value=portfolio_value,
            price=price,
            atr=atr
        )
        
        position_value = shares * price
        position_pct = (position_value / portfolio_value) * 100
        
        # Calculate stop distance
        stop_distance = strategy.config.atr_stop_multiple * atr
        stop_price = price - stop_distance
        risk_per_share = stop_distance
        total_risk = shares * risk_per_share
        risk_pct = (total_risk / portfolio_value) * 100
        
        print(f"\n  {symbol} @ ${price:.2f} ({description})")
        print(f"    ATR: ${atr:.2f}")
        print(f"    → Shares: {shares}")
        print(f"    → Position Value: ${position_value:,.2f} ({position_pct:.1f}% of portfolio)")
        print(f"    → Stop Price: ${stop_price:.2f}")
        print(f"    → Risk: ${total_risk:,.2f} ({risk_pct:.2f}% of portfolio)")
    
    print("\n💡 Notice:")
    print("   - Higher volatility (ATR) → Smaller position size")
    print("   - Lower volatility (ATR) → Larger position size")
    print("   - All respect max position % and risk % limits")
    
    print("\n" + "="*70)


async def example_4_signal_generator_integration():
    """Show SignalGenerator with advanced features"""
    print("\n" + "="*70)
    print("EXAMPLE 4: SignalGenerator with Advanced Features")
    print("="*70)
    
    # Create components
    strategy = TechnicalStrategy()
    
    # Basic mode
    signal_gen_basic = SignalGenerator(
        strategy=strategy,
        use_advanced_signals=False
    )
    
    # Advanced mode
    signal_gen_advanced = SignalGenerator(
        strategy=strategy,
        use_advanced_signals=True
    )
    
    # Create price data
    symbol = 'MSFT'
    price_data = create_realistic_price_data(symbol, days=250)
    
    print(f"\nAnalyzing {symbol}...")
    
    # BASIC MODE
    print("\n" + "-"*70)
    print("📊 Basic Mode (Legacy)")
    print("-"*70)
    
    analysis_basic = await signal_gen_basic.analyze_symbol(symbol, price_data)
    
    if analysis_basic:
        print(f"  Signal: {analysis_basic['signal'] or 'NONE'}")
        print(f"  Strength: {analysis_basic['signal_strength']}")
        print(f"  Price: ${analysis_basic['price']:.2f}")
        print(f"  RSI: {analysis_basic['rsi']:.2f}")
        print(f"\n  Fields: {list(analysis_basic.keys())}")
    
    # ADVANCED MODE
    print("\n" + "-"*70)
    print("🎯 Advanced Mode (Pro)")
    print("-"*70)
    
    analysis_advanced = await signal_gen_advanced.analyze_symbol(symbol, price_data)
    
    if analysis_advanced:
        print(f"  Signal: {analysis_advanced['signal'] or 'NONE'}")
        print(f"  Strength: {analysis_advanced['signal_strength']}")
        print(f"  Price: ${analysis_advanced['price']:.2f}")
        print(f"  RSI: {analysis_advanced['rsi']:.2f}")
        
        if 'macd' in analysis_advanced:
            print(f"  MACD: {analysis_advanced['macd']:.4f}")
            print(f"  MACD Signal: {analysis_advanced['macd_signal']:.4f}")
        
        if 'atr' in analysis_advanced:
            print(f"  ATR: ${analysis_advanced['atr']:.2f}")
        
        print(f"\n  📋 Diagnostic Reasons:")
        for reason in analysis_advanced.get('reasons', []):
            print(f"     • {reason}")
        
        print(f"\n  Fields: {list(analysis_advanced.keys())}")
    
    print("\n💡 Difference:")
    print("   - Basic: Returns core indicators only")
    print("   - Advanced: Includes MACD, ATR, and diagnostic reasons")
    
    print("\n" + "="*70)


async def example_5_custom_config():
    """Show custom strategy configuration"""
    print("\n" + "="*70)
    print("EXAMPLE 5: Custom Strategy Configuration")
    print("="*70)
    
    # Create conservative config
    conservative_config = StrategyConfig(
        # Shorter SMAs for faster signals
        sma_fast=10,
        sma_slow=30,
        
        # Stricter RSI zones
        rsi_buy_threshold=30,      # Only buy when very oversold
        rsi_sell_threshold=70,     # Sell when overbought
        
        # Tighter stops
        atr_stop_multiple=1.5,     # 1.5x ATR stop
        atr_take_multiple=2.5,     # 2.5x ATR target
        
        # Smaller positions
        max_position_pct=0.10,     # Max 10% per position
        risk_per_trade_pct=0.005,  # Risk 0.5% per trade
    )
    
    # Create aggressive config
    aggressive_config = StrategyConfig(
        # Longer SMAs for trend following
        sma_fast=30,
        sma_slow=100,
        
        # Looser RSI zones
        rsi_buy_threshold=45,
        rsi_sell_threshold=55,
        
        # Wider stops
        atr_stop_multiple=3.0,
        atr_take_multiple=5.0,
        
        # Larger positions
        max_position_pct=0.25,
        risk_per_trade_pct=0.02,
    )
    
    print("\n📘 Conservative Strategy:")
    print(f"  SMAs: {conservative_config.sma_fast}/{conservative_config.sma_slow}")
    print(f"  RSI: {conservative_config.rsi_buy_threshold}/{conservative_config.rsi_sell_threshold}")
    print(f"  ATR Stop: {conservative_config.atr_stop_multiple}x")
    print(f"  Max Position: {conservative_config.max_position_pct:.0%}")
    print(f"  Risk/Trade: {conservative_config.risk_per_trade_pct:.1%}")
    
    print("\n📕 Aggressive Strategy:")
    print(f"  SMAs: {aggressive_config.sma_fast}/{aggressive_config.sma_slow}")
    print(f"  RSI: {aggressive_config.rsi_buy_threshold}/{aggressive_config.rsi_sell_threshold}")
    print(f"  ATR Stop: {aggressive_config.atr_stop_multiple}x")
    print(f"  Max Position: {aggressive_config.max_position_pct:.0%}")
    print(f"  Risk/Trade: {aggressive_config.risk_per_trade_pct:.1%}")
    
    # Test both
    price_data = create_realistic_price_data('TEST', days=250)
    
    for name, config in [('Conservative', conservative_config), ('Aggressive', aggressive_config)]:
        strategy = TechnicalStrategy(config)
        df = strategy.calculate_indicators(price_data)
        latest = df.iloc[-1]
        
        signal, strength, reasons = strategy.evaluate_signal_advanced(latest)
        
        print(f"\n{name} Result: {signal or 'NONE'} ({strength})")
    
    print("\n" + "="*70)


async def main():
    """Run all examples"""
    print("\n" + "🚀 "* 25)
    print("PRO TRADING STRATEGY EXAMPLES")
    print("🚀 " * 25)
    
    await example_1_basic_vs_advanced()
    await example_2_atr_exits()
    await example_3_atr_position_sizing()
    await example_4_signal_generator_integration()
    await example_5_custom_config()
    
    print("\n" + "✅ " * 25)
    print("ALL EXAMPLES COMPLETED")
    print("✅ " * 25 + "\n")
    
    print("\n📚 Next Steps:")
    print("  1. Read MIGRATION_GUIDE_PRO_STRATEGY.md")
    print("  2. Test with your actual data")
    print("  3. Enable advanced signals incrementally")
    print("  4. Monitor diagnostic reasons in logs")
    print("  5. Compare basic vs advanced performance\n")


if __name__ == "__main__":
    asyncio.run(main())
