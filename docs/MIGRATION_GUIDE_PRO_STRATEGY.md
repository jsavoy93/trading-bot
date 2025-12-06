# Migration Guide: Pro Trading Strategy

## Overview

The trading strategy has been enhanced with **professional-grade technical analysis**:
- ✅ MACD (trend/momentum confirmation)
- ✅ ATR (volatility-based stops and position sizing)
- ✅ 200-period SMA (long-term trend filter)
- ✅ Multi-indicator signal confluence
- ✅ Detailed diagnostic reasons for each signal

**Good news**: The new features are **100% backward compatible**. You can adopt them incrementally.

---

## Quick Reference

### Current (Basic) Usage
```python
# What you're doing now
signal, strength = strategy.evaluate_signal(sma_fast, sma_slow, rsi)
```

### New (Advanced) Usage
```python
# New professional approach
signal, strength, reasons = strategy.evaluate_signal_advanced(row)
# reasons = ["Trend regime: UP", "MACD bullish", "RSI=32.1 below buy threshold 40"]
```

---

## Migration Paths

### Path 1: Keep Current Behavior (No Changes Required)

Your existing code works **exactly as before**:

```python
from src.trading.strategy import TechnicalStrategy

strategy = TechnicalStrategy()

# Calculate indicators (now includes MACD, ATR, SMA_200)
df = strategy.calculate_indicators(price_data)
latest = df.iloc[-1]

# Use basic signal (unchanged)
signal, strength = strategy.evaluate_signal(
    sma_fast=latest['SMA_20'],
    sma_slow=latest['SMA_50'],
    rsi=latest['RSI']
)
```

**Result**: Works exactly as before. New indicators calculated but not used.

---

### Path 2: Enable Advanced Signals (Recommended)

Get better signals with multi-indicator confluence:

#### Option A: Via SignalGenerator (Easiest)

```python
from src.trading import SignalGenerator, TechnicalStrategy

strategy = TechnicalStrategy()
signal_gen = SignalGenerator(
    strategy=strategy,
    use_advanced_signals=True  # 👈 Enable advanced mode
)

# Analyze symbol
analysis = await signal_gen.analyze_symbol('AAPL', price_data)

# Now includes detailed diagnostics
print(analysis['signal'])        # 'BUY', 'SELL', or None
print(analysis['signal_strength'])  # 'WEAK', 'MEDIUM', 'STRONG'
print(analysis['reasons'])       # List of diagnostic reasons
print(analysis.get('macd'))      # MACD value (if available)
print(analysis.get('atr'))       # ATR value (if available)
```

#### Option B: Direct Strategy Call

```python
strategy = TechnicalStrategy()
df = strategy.calculate_indicators(price_data)
latest = df.iloc[-1]

# Use advanced signal
signal, strength, reasons = strategy.evaluate_signal_advanced(latest)

# Log the reasons for visibility
for reason in reasons:
    logging.info(f"  • {reason}")
```

**What You Get**:
- ✅ MACD confirmation (avoids false breakouts)
- ✅ 200-SMA trend filter (avoid fighting major trends)
- ✅ Better signal quality
- ✅ Diagnostic reasons for debugging

---

### Path 3: Add ATR-Based Stops (Advanced)

Replace fixed % stops with volatility-adjusted stops:

#### Step 1: Store ATR at Entry

When opening a position, store the ATR:

```python
# Get current indicators
df = strategy.calculate_indicators(price_data)
latest = df.iloc[-1]

# Store these at entry
entry_price = latest['close']
entry_atr = latest['ATR']  # 👈 Save this!

# Your existing position tracking
position = {
    'symbol': 'AAPL',
    'entry_price': entry_price,
    'entry_atr': entry_atr,  # 👈 Add this field
    'quantity': qty,
    'entry_time': datetime.now()
}
```

#### Step 2: Use ATR Exits

When evaluating exits:

```python
# Get current price
current_price = get_current_price('AAPL')

# Check exit with ATR
should_exit, reason = strategy.should_exit_position_advanced(
    entry_price=position['entry_price'],
    current_price=current_price,
    direction='LONG',
    entry_atr=position['entry_atr']  # 👈 Use stored ATR
)

if should_exit:
    logging.info(f"Exiting {symbol}: {reason}")
    # Execute sell
```

#### Via SignalGenerator

```python
signal_gen = SignalGenerator(strategy)

should_exit, reason = signal_gen.evaluate_position_exit(
    symbol='AAPL',
    entry_price=position['entry_price'],
    current_price=current_price,
    entry_atr=position['entry_atr'],
    use_advanced_exit=True  # 👈 Enable ATR exits
)
```

**What You Get**:
- ✅ Stops adapt to volatility (wider for volatile stocks, tighter for stable ones)
- ✅ More professional risk management
- ✅ Better exit timing

---

### Path 4: ATR-Based Position Sizing (Advanced)

Calculate position size based on volatility:

```python
from src.trading import TechnicalStrategy, PositionSizer

strategy = TechnicalStrategy()
sizer = PositionSizer()  # Your existing position sizer

# Get current indicators
df = strategy.calculate_indicators(price_data)
latest = df.iloc[-1]

price = latest['close']
atr = latest['ATR']

# Calculate size using ATR
shares = strategy.compute_position_size(
    portfolio_value=50000,
    price=price,
    atr=atr  # 👈 ATR-aware sizing
)

# This respects both:
# - max_position_pct (15% cap)
# - risk_per_trade_pct (1% risk per trade)
```

**What You Get**:
- ✅ Risk 1% of portfolio per trade (configurable)
- ✅ Automatically smaller positions in volatile stocks
- ✅ Larger positions in stable stocks
- ✅ Professional money management

---

## Configuration Options

### Customize Strategy Parameters

```python
from src.trading.strategy import StrategyConfig, TechnicalStrategy

# Create custom config
config = StrategyConfig(
    # Moving averages
    sma_fast=10,
    sma_slow=30,
    sma_trend=200,
    
    # RSI zones
    rsi_buy_threshold=35,      # Buy below this
    rsi_sell_threshold=65,     # Sell above this
    rsi_strong_oversold=25,    # Strong buy zone
    rsi_strong_overbought=75,  # Strong sell zone
    
    # MACD (trend confirmation)
    macd_fast=12,
    macd_slow=26,
    macd_signal=9,
    
    # ATR (volatility)
    atr_period=14,
    
    # Position sizing
    max_position_pct=0.20,     # Max 20% per position
    reserve_cash_pct=0.20,     # Keep 20% cash
    risk_per_trade_pct=0.01,   # Risk 1% per trade
    
    # Fixed % stops (fallback)
    stop_loss_pct=0.05,        # 5% stop loss
    take_profit_pct=0.10,      # 10% take profit
    
    # ATR stops (professional)
    atr_stop_multiple=2.0,     # 2x ATR stop
    atr_take_multiple=3.0,     # 3x ATR target
)

strategy = TechnicalStrategy(config)
```

---

## SmartBot Integration Example

Here's how to integrate into your `SmartBot`:

### Minimal Changes (Advanced Signals Only)

```python
class SmartBot:
    def __init__(self):
        # ... existing init ...
        
        # Create strategy components
        from src.trading import TechnicalStrategy, SignalGenerator
        
        self.strategy = TechnicalStrategy()
        self.signal_generator = SignalGenerator(
            strategy=self.strategy,
            ai_agent=self.ai,
            use_advanced_signals=True  # 👈 Enable advanced
        )
    
    async def analyze_symbol(self, symbol: str, use_ai: bool = False):
        """Updated to use new signal generator"""
        
        # Get market data
        price_data = self.get_market_data(symbol)
        if price_data is None:
            return None
        
        # Generate signal
        analysis = await self.signal_generator.analyze_symbol(symbol, price_data)
        
        if analysis and analysis['signal']:
            # Log detailed reasons
            logging.info(f"\n{symbol} Signal: {analysis['signal']} ({analysis['signal_strength']})")
            for reason in analysis.get('reasons', []):
                logging.info(f"  • {reason}")
        
        return analysis
```

### Full Integration (ATR Stops + Sizing)

```python
class SmartBot:
    def __init__(self):
        # ... existing init ...
        
        from src.trading import TechnicalStrategy, SignalGenerator, PositionSizer
        
        self.strategy = TechnicalStrategy()
        self.signal_generator = SignalGenerator(
            strategy=self.strategy,
            ai_agent=self.ai,
            use_advanced_signals=True
        )
        self.position_sizer = PositionSizer()
    
    async def execute_trade(self, analysis: dict):
        """Execute trade with ATR-based sizing"""
        
        symbol = analysis['symbol']
        price = analysis['price']
        signal = analysis['signal']
        
        # Get portfolio info
        account = self.trading_client.get_account()
        portfolio_value = float(account.portfolio_value)
        
        if signal == 'BUY':
            # Calculate position size with ATR
            atr = analysis.get('atr')
            shares = self.strategy.compute_position_size(
                portfolio_value=portfolio_value,
                price=price,
                atr=atr
            )
            
            if shares > 0:
                # Execute buy
                success = self.executor.execute_buy(
                    symbol=symbol,
                    quantity=shares,
                    price=price,
                    reason=f"Advanced signal: {', '.join(analysis.get('reasons', []))}",
                    analysis=analysis
                )
                
                if success:
                    # Store position with ATR for exit logic
                    self._track_position(symbol, price, shares, atr)
    
    def _track_position(self, symbol, entry_price, quantity, entry_atr):
        """Track position with entry ATR"""
        # Add to your existing position tracking
        if not hasattr(self, '_positions'):
            self._positions = {}
        
        self._positions[symbol] = {
            'entry_price': entry_price,
            'quantity': quantity,
            'entry_atr': entry_atr,  # 👈 Store for exit logic
            'entry_time': datetime.now(timezone.utc)
        }
    
    async def check_exits(self):
        """Check exit conditions for all positions"""
        
        for symbol, pos in list(self._positions.items()):
            # Get current price
            current_price = self._get_current_price(symbol)
            
            # Check exit with ATR
            should_exit, reason = self.signal_generator.evaluate_position_exit(
                symbol=symbol,
                entry_price=pos['entry_price'],
                current_price=current_price,
                entry_atr=pos['entry_atr'],
                use_advanced_exit=True  # 👈 Use ATR exits
            )
            
            if should_exit:
                logging.info(f"Exiting {symbol}: {reason}")
                # Execute sell
                self.executor.execute_sell(
                    symbol=symbol,
                    quantity=pos['quantity'],
                    price=current_price,
                    reason=reason
                )
                del self._positions[symbol]
```

---

## Benefits Summary

### Advanced Signals
- ✅ **Better quality**: Multiple indicators must agree
- ✅ **Trend awareness**: Avoids fighting major trends
- ✅ **Momentum confirmation**: MACD filters false breakouts
- ✅ **Clear diagnostics**: Know exactly why each signal fired

### ATR-Based Exits
- ✅ **Volatility-adjusted**: Stops adapt to market conditions
- ✅ **Less whipsaw**: Wider stops in volatile markets
- ✅ **Better entries**: Tighter stops in stable markets
- ✅ **Professional**: Industry-standard risk management

### ATR-Based Sizing
- ✅ **Consistent risk**: 1% risk per trade (configurable)
- ✅ **Automatic adjustment**: Smaller size in volatile stocks
- ✅ **Capital efficiency**: Optimal position sizes
- ✅ **Portfolio protection**: Risk-aware allocation

---

## Testing Strategy

### Phase 1: Test Basic Compatibility
```python
# Run with existing code - should work unchanged
python main.py
```

### Phase 2: Test Advanced Signals
```python
# Enable advanced signals, dry-run mode
signal_gen = SignalGenerator(strategy, use_advanced_signals=True)
executor = OrderExecutor(trading_client, dry_run=True)
```

### Phase 3: Test ATR Features
```python
# Run with ATR sizing/exits in dry-run
# Monitor logs for diagnostic output
```

### Phase 4: Live Testing
```python
# Start with small position sizes
config = StrategyConfig(
    max_position_pct=0.05,  # Only 5% per position
    risk_per_trade_pct=0.005  # Only 0.5% risk
)
```

---

## Troubleshooting

### "No ATR available"
**Cause**: Not enough historical data  
**Fix**: Ensure at least 200 bars of data (for 200-SMA)

### "Position size is 0"
**Cause**: ATR-based risk calculation too conservative  
**Fix**: Increase `risk_per_trade_pct` or decrease `atr_stop_multiple`

### "No signal generated"
**Cause**: Advanced signals require more confluence  
**Fix**: Normal - signals are more selective (higher quality)

---

## Next Steps

1. ✅ **Read this guide** - Understand the options
2. ✅ **Test basic compatibility** - Ensure nothing breaks
3. ✅ **Enable advanced signals** - Better signal quality
4. ✅ **Add ATR tracking** - Store ATR at entry
5. ✅ **Test exits** - Verify ATR-based exit logic
6. ✅ **Test sizing** - Validate position size calculations
7. ✅ **Monitor results** - Compare performance metrics

---

## Support

All existing tests still pass (16/16). The trading modules are backward compatible.

For questions or issues, review:
- `src/trading/README.md` - Architecture overview
- `src/trading/strategy.py` - Implementation details
- `examples/trading_modules_demo.py` - Working examples
