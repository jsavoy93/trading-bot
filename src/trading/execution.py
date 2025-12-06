"""Order execution logic

This module handles the actual execution of trades:
- Placing market orders
- Validating order parameters
- Logging trade details
- Error handling
"""

from typing import Optional, Dict, Any
from datetime import datetime
import logging

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce


class OrderExecutor:
    """Executes trading orders with proper validation and logging"""
    
    def __init__(
        self,
        trading_client: TradingClient,
        db_client: Optional[Any] = None,
        dry_run: bool = False
    ):
        """Initialize order executor
        
        Args:
            trading_client: Alpaca trading client
            db_client: Optional database client for logging
            dry_run: If True, log orders but don't execute
        """
        self.trading_client = trading_client
        self.db_client = db_client
        self.dry_run = dry_run
    
    def execute_buy(
        self,
        symbol: str,
        quantity: int,
        price: float,
        reason: str,
        analysis: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, Optional[str]]:
        """Execute a buy order
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares to buy
            price: Current price (for logging)
            reason: Reason for the trade
            analysis: Optional analysis data (RSI, SMA, etc.)
            
        Returns:
            Tuple of (success, error_message)
        """
        if quantity <= 0:
            return False, f"Invalid quantity: {quantity}"
        
        order_value = quantity * price
        
        # Log intent
        logging.info(f"\n{'='*60}")
        logging.info(f"🛒 BUY ORDER: {symbol}")
        logging.info(f"   Quantity: {quantity} shares @ ${price:.2f}")
        logging.info(f"   Total Value: ${order_value:.2f}")
        logging.info(f"   Reason: {reason}")
        if analysis:
            logging.info(f"   RSI: {analysis.get('rsi', 'N/A'):.2f}")
            logging.info(f"   Signal Strength: {analysis.get('signal_strength', 'N/A')}")
        logging.info(f"{'='*60}\n")
        
        if self.dry_run:
            logging.info(f"[DRY RUN] Would buy {quantity} {symbol}")
            return True, None
        
        try:
            # Create and submit order
            market_order_data = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY
            )
            
            order = self.trading_client.submit_order(order_data=market_order_data)
            
            logging.info(f"✅ BUY order submitted for {quantity} {symbol} - Order ID: {order.id}")
            
            # Log to database if available
            if self.db_client and self.db_client.is_available():
                self._log_trade_to_db(
                    symbol=symbol,
                    signal='BUY',
                    price=price,
                    quantity=quantity,
                    reason=reason,
                    analysis=analysis
                )
            
            return True, None
            
        except Exception as e:
            error_msg = f"Buy order failed for {symbol}: {e}"
            logging.error(f"❌ {error_msg}")
            return False, error_msg
    
    def execute_sell(
        self,
        symbol: str,
        quantity: int,
        price: float,
        reason: str,
        analysis: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, Optional[str]]:
        """Execute a sell order
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares to sell
            price: Current price (for logging)
            reason: Reason for the trade
            analysis: Optional analysis data (RSI, SMA, etc.)
            
        Returns:
            Tuple of (success, error_message)
        """
        if quantity <= 0:
            return False, f"Invalid quantity: {quantity}"
        
        order_value = quantity * price
        
        # Log intent
        logging.info(f"\n{'='*60}")
        logging.info(f"💰 SELL ORDER: {symbol}")
        logging.info(f"   Quantity: {quantity} shares @ ${price:.2f}")
        logging.info(f"   Total Value: ${order_value:.2f}")
        logging.info(f"   Reason: {reason}")
        if analysis:
            logging.info(f"   RSI: {analysis.get('rsi', 'N/A'):.2f}")
            logging.info(f"   Signal Strength: {analysis.get('signal_strength', 'N/A')}")
        logging.info(f"{'='*60}\n")
        
        if self.dry_run:
            logging.info(f"[DRY RUN] Would sell {quantity} {symbol}")
            return True, None
        
        try:
            # Create and submit order
            market_order_data = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY
            )
            
            order = self.trading_client.submit_order(order_data=market_order_data)
            
            logging.info(f"✅ SELL order submitted for {quantity} {symbol} - Order ID: {order.id}")
            
            # Log to database if available
            if self.db_client and self.db_client.is_available():
                self._log_trade_to_db(
                    symbol=symbol,
                    signal='SELL',
                    price=price,
                    quantity=quantity,
                    reason=reason,
                    analysis=analysis
                )
            
            return True, None
            
        except Exception as e:
            error_msg = f"Sell order failed for {symbol}: {e}"
            logging.error(f"❌ {error_msg}")
            return False, error_msg
    
    def _log_trade_to_db(
        self,
        symbol: str,
        signal: str,
        price: float,
        quantity: int,
        reason: str,
        analysis: Optional[Dict[str, Any]] = None
    ):
        """Log trade to database
        
        Args:
            symbol: Stock symbol
            signal: BUY or SELL
            price: Trade price
            quantity: Trade quantity
            reason: Trade reason
            analysis: Optional analysis data
        """
        try:
            trade_data = {
                'symbol': symbol,
                'signal': signal,
                'price': price,
                'quantity': quantity,
                'reason': reason,
                'rsi': analysis.get('rsi', 0) if analysis else 0,
                'sma_fast': analysis.get('sma_fast', 0) if analysis else 0,
                'sma_slow': analysis.get('sma_slow', 0) if analysis else 0,
                'timestamp': datetime.now().isoformat()
            }
            
            self.db_client.insert_data('trades', trade_data)
            logging.debug(f"Trade logged to database: {symbol} {signal}")
            
        except Exception as e:
            logging.warning(f"Failed to log trade to database: {e}")
    
    def get_current_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get current position for a symbol
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dictionary with position info or None
        """
        try:
            position = self.trading_client.get_open_position(symbol)
            return {
                'symbol': symbol,
                'qty': float(position.qty),
                'market_value': float(position.market_value),
                'avg_entry_price': float(position.avg_entry_price),
                'current_price': float(position.current_price),
                'unrealized_pl': float(position.unrealized_pl),
                'unrealized_plpc': float(position.unrealized_plpc)
            }
        except Exception as e:
            logging.debug(f"Could not get position for {symbol}: {e}")
            return None
