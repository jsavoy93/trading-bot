# Trading Logic Module

This module contains the isolated, testable trading logic separated from the bot orchestration layer.

## Architecture Overview

```
src/trading/
├── strategy.py         # Trading strategy configuration and rules
├── signals.py          # Signal generation from technical + AI analysis  
├── position_sizing.py  # Position size and risk calculations
└── execution.py        # Order execution and logging
```

## Module Responsibilities

### 📊 `strategy.py` - Strategy Configuration & Rules

**Purpose**: Define the trading strategy parameters and evaluation logic

**Key Classes**:
- `StrategyConfig`: Configuration dataclass for strategy parameters
  - SMA periods (fast/slow)
  - RSI thresholds (buy/sell)
  - Position sizing limits
  - Risk parameters (stop loss, take profit)

- `TechnicalStrategy`: Core strategy logic
  - `calculate_indicators()`: Compute SMAs and RSI
  - `evaluate_signal()`: Generate BUY/SELL signals from indicators
  - `should_exit_position()`: Check stop loss / take profit conditions

**Example Usage**:
```python
from src.trading.strategy import TechnicalStrategy, StrategyConfig

# Use default strategy
strategy = TechnicalStrategy()

# Or customize
config = StrategyConfig(
    sma_fast=10,
    sma_slow=30,
    rsi_buy_threshold=35,
    rsi_sell_threshold=65
)
strategy = TechnicalStrategy(config)

# Evaluate indicators
signal, strength = strategy.evaluate_signal(
    sma_fast=150.5, 
    sma_slow=148.2, 
    rsi=32.5
)
# Returns: ('BUY', 'MEDIUM')
```

---

### 🎯 `signals.py` - Signal Generation

**Purpose**: Combine technical analysis with optional AI enhancement to generate actionable signals

**Key Classes**:
- `SignalGenerator`: Orchestrates analysis from multiple sources
  - `analyze_symbol()`: Full analysis pipeline (data → indicators → signal → AI enhancement)
  - `evaluate_position_exit()`: Determine when to exit existing positions
  - `_get_ai_enhancement()`: Get AI confirmation/conflict with technical signal

**Signal Flow**:
```
Price Data → Calculate Indicators → Technical Signal → AI Enhancement (optional) → Final Signal
```

**Example Usage**:
```python
from src.trading.signals import SignalGenerator
from src.trading.strategy import TechnicalStrategy

strategy = TechnicalStrategy()
signal_gen = SignalGenerator(
    strategy=strategy,
    ai_agent=ai_agent,  # Optional
    use_ai_enhancement=True
)

# Analyze a symbol
analysis = await signal_gen.analyze_symbol('AAPL', price_data_df)
# Returns:
# {
#     'symbol': 'AAPL',
#     'price': 175.50,
#     'rsi': 38.2,
#     'signal': 'BUY',
#     'signal_strength': 'AI_ENHANCED',
#     'ai_insight': 'AI confirms BUY with 85% confidence',
#     ...
# }
```

---

### 💰 `position_sizing.py` - Position Sizing & Risk Management

**Purpose**: Calculate appropriate position sizes based on portfolio state and risk parameters

**Key Classes**:
- `PositionSizer`: Position sizing calculations
  - `calculate_buy_quantity()`: Determine how many shares to buy
  - `calculate_sell_quantity()`: Determine how many shares to sell
  - `get_portfolio_metrics()`: Calculate portfolio risk metrics

**Risk Controls**:
- Max position size (default 15% of portfolio)
- Cash reserve (default 20% kept in cash)
- Minimum order value (default $100)
- Position concentration monitoring

**Example Usage**:
```python
from src.trading.position_sizing import PositionSizer

sizer = PositionSizer(
    max_position_pct=0.15,  # 15% max per position
    reserve_cash_pct=0.20,  # Keep 20% cash
    min_order_value=100.0
)

# Calculate buy quantity
qty, reason = sizer.calculate_buy_quantity(
    symbol='AAPL',
    price=175.50,
    available_cash=10000,
    portfolio_value=50000,
    existing_position_value=2000,
    reserved_cash=500
)
# Returns: (40, None) - Buy 40 shares

# Get portfolio metrics
metrics = sizer.get_portfolio_metrics(
    cash=10000,
    portfolio_value=50000,
    positions={'AAPL': 7000, 'MSFT': 5500, 'GOOGL': 3200}
)
# Returns concentration, cash%, invested%, oversized positions, etc.
```

---

### 🔨 `execution.py` - Order Execution

**Purpose**: Execute trades with proper validation, logging, and error handling

**Key Classes**:
- `OrderExecutor`: Handle order execution
  - `execute_buy()`: Place buy order
  - `execute_sell()`: Place sell order
  - `get_current_position()`: Query existing position
  - `_log_trade_to_db()`: Persist trade to database

**Features**:
- Dry-run mode for testing
- Comprehensive logging
- Database persistence
- Error handling and validation

**Example Usage**:
```python
from src.trading.execution import OrderExecutor

executor = OrderExecutor(
    trading_client=alpaca_client,
    db_client=db,
    dry_run=False  # Set True for testing
)

# Execute a buy
success, error = executor.execute_buy(
    symbol='AAPL',
    quantity=40,
    price=175.50,
    reason='Technical BUY signal: SMA crossover + RSI oversold',
    analysis={'rsi': 38.2, 'signal_strength': 'STRONG'}
)

# Execute a sell
success, error = executor.execute_sell(
    symbol='AAPL',
    quantity=40,
    price=180.25,
    reason='Take profit: +2.7%',
    analysis={'rsi': 62.1, 'signal_strength': 'MEDIUM'}
)
```

---

## Integration with SmartBot

The `SmartBot` class orchestrates these modules:

```python
from src.trading import SignalGenerator, TechnicalStrategy, PositionSizer, OrderExecutor

class SmartBot:
    def __init__(self):
        # Initialize trading components
        self.strategy = TechnicalStrategy()
        self.signal_generator = SignalGenerator(self.strategy, self.ai)
        self.position_sizer = PositionSizer()
        self.executor = OrderExecutor(self.trading_client, self.db)
    
    async def run_analysis(self):
        # Get market data
        price_data = self.get_market_data(symbol)
        
        # Generate signal
        analysis = await self.signal_generator.analyze_symbol(symbol, price_data)
        
        # Calculate position size
        qty, reason = self.position_sizer.calculate_buy_quantity(
            symbol, analysis['price'], cash, portfolio_value
        )
        
        # Execute if valid
        if qty > 0:
            self.executor.execute_buy(symbol, qty, analysis['price'], reason, analysis)
```

---

## Testing Strategy

Each module can be tested independently:

```python
# Test strategy logic
def test_buy_signal():
    strategy = TechnicalStrategy()
    signal, strength = strategy.evaluate_signal(sma_fast=150, sma_slow=145, rsi=35)
    assert signal == 'BUY'
    assert strength == 'MEDIUM'

# Test position sizing
def test_position_size_limits():
    sizer = PositionSizer(max_position_pct=0.15)
    qty, _ = sizer.calculate_buy_quantity(
        'AAPL', price=100, available_cash=10000, 
        portfolio_value=50000, existing_position_value=7000
    )
    assert qty == 5  # Can only add $500 more (15% of $50k = $7500 max)

# Test signal generation (with mock data)
async def test_signal_generation():
    strategy = TechnicalStrategy()
    signal_gen = SignalGenerator(strategy)
    analysis = await signal_gen.analyze_symbol('AAPL', mock_price_data)
    assert 'signal' in analysis
    assert analysis['signal'] in ['BUY', 'SELL', None]
```

---

## Extending the System

### Adding a New Strategy

1. Create a new strategy class inheriting from `TechnicalStrategy`
2. Override `evaluate_signal()` with your logic
3. Optionally add new indicators in `calculate_indicators()`

```python
class MomentumStrategy(TechnicalStrategy):
    def evaluate_signal(self, sma_fast, sma_slow, rsi, momentum=None):
        # Your custom logic here
        if momentum > 0.05 and rsi < 50:
            return 'BUY', 'STRONG'
        return None, 'WEAK'
```

### Adding New Risk Rules

Update `PositionSizer` to add new constraints:

```python
class EnhancedPositionSizer(PositionSizer):
    def calculate_buy_quantity(self, ...):
        qty, reason = super().calculate_buy_quantity(...)
        
        # Add your constraint
        if sector_exposure > 0.30:
            return 0, "Sector exposure limit exceeded"
        
        return qty, reason
```

---

## Benefits of This Architecture

✅ **Testability**: Each module can be tested in isolation  
✅ **Clarity**: Trading logic is separated from orchestration  
✅ **Maintainability**: Changes to strategy don't affect execution logic  
✅ **Extensibility**: Easy to add new strategies or risk rules  
✅ **Reusability**: Modules can be used in different bots/contexts  
✅ **Debugging**: Clear boundaries make issues easier to isolate  

---

## Next Steps

1. **Migrate SmartBot**: Refactor `smart_bot.py` to use these modules
2. **Add Tests**: Create comprehensive test suite for each module
3. **Add Strategies**: Implement additional strategy variations
4. **Enhance AI**: Improve AI enhancement logic in `signals.py`
5. **Add Metrics**: Track strategy performance over time
