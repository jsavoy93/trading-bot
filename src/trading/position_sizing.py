"""Position sizing logic for risk management

This module handles calculating appropriate position sizes based on:
- Portfolio value
- Risk parameters
- Cash reserves
- Existing positions
"""

from typing import Dict, Optional
import logging


class PositionSizer:
    """Calculate position sizes based on portfolio and risk parameters"""
    
    def __init__(
        self,
        max_position_pct: float = 0.15,
        reserve_cash_pct: float = 0.20,
        min_order_value: float = 100.0
    ):
        """Initialize position sizer
        
        Args:
            max_position_pct: Maximum % of portfolio per position (default 15%)
            reserve_cash_pct: % of cash to keep in reserve (default 20%)
            min_order_value: Minimum order value in dollars (default $100)
        """
        self.max_position_pct = max_position_pct
        self.reserve_cash_pct = reserve_cash_pct
        self.min_order_value = min_order_value
    
    def calculate_buy_quantity(
        self,
        symbol: str,
        price: float,
        available_cash: float,
        portfolio_value: float,
        existing_position_value: float = 0.0,
        reserved_cash: float = 0.0
    ) -> tuple[int, Optional[str]]:
        """Calculate appropriate buy quantity for a symbol
        
        Args:
            symbol: Stock symbol
            price: Current price
            available_cash: Total cash available
            portfolio_value: Total portfolio value
            existing_position_value: Current value of existing position in this symbol
            reserved_cash: Cash already reserved for open orders
            
        Returns:
            Tuple of (quantity, reason_if_zero)
        """
        # Calculate effective cash (excluding reserves and open order commitments)
        effective_cash = available_cash - reserved_cash
        cash_reserve = available_cash * self.reserve_cash_pct
        usable_cash = max(0, effective_cash - cash_reserve)
        
        if usable_cash < self.min_order_value:
            return 0, f"Insufficient cash after reserves (${usable_cash:.2f} < ${self.min_order_value})"
        
        # Calculate max position value based on portfolio
        max_position_value = portfolio_value * self.max_position_pct
        
        # Calculate how much more we can buy
        remaining_allocation = max_position_value - existing_position_value
        
        if remaining_allocation <= 0:
            return 0, f"Position already at max allocation (${existing_position_value:.2f} >= ${max_position_value:.2f})"
        
        # Use the lesser of: usable cash or remaining allocation
        buy_value = min(usable_cash, remaining_allocation)
        
        if buy_value < self.min_order_value:
            return 0, f"Buy value too small (${buy_value:.2f} < ${self.min_order_value})"
        
        # Calculate quantity (floor to whole shares)
        quantity = int(buy_value / price)
        
        if quantity == 0:
            return 0, f"Price too high - can only afford {buy_value/price:.2f} shares"
        
        # Verify final order value meets minimum
        order_value = quantity * price
        if order_value < self.min_order_value:
            return 0, f"Order value ${order_value:.2f} below minimum ${self.min_order_value}"
        
        return quantity, None
    
    def calculate_sell_quantity(
        self,
        symbol: str,
        current_position_qty: float,
        sell_pct: float = 1.0
    ) -> tuple[int, Optional[str]]:
        """Calculate sell quantity for a position
        
        Args:
            symbol: Stock symbol
            current_position_qty: Current position quantity
            sell_pct: Percentage of position to sell (0.0-1.0, default 1.0)
            
        Returns:
            Tuple of (quantity, reason_if_zero)
        """
        if current_position_qty <= 0:
            return 0, "No position to sell"
        
        if not (0.0 < sell_pct <= 1.0):
            logging.warning(f"Invalid sell_pct {sell_pct}, defaulting to 1.0")
            sell_pct = 1.0
        
        quantity = int(current_position_qty * sell_pct)
        
        if quantity == 0:
            return 0, f"Position too small to sell {sell_pct:.0%} ({current_position_qty * sell_pct:.2f} shares)"
        
        return quantity, None
    
    def get_portfolio_metrics(
        self,
        cash: float,
        portfolio_value: float,
        positions: Dict[str, float]
    ) -> Dict[str, any]:
        """Calculate portfolio risk metrics
        
        Args:
            cash: Available cash
            portfolio_value: Total portfolio value
            positions: Dict of symbol -> position_value
            
        Returns:
            Dictionary with portfolio metrics
        """
        total_position_value = sum(positions.values())
        cash_pct = (cash / portfolio_value * 100) if portfolio_value > 0 else 0
        invested_pct = (total_position_value / portfolio_value * 100) if portfolio_value > 0 else 0
        
        # Calculate concentration (largest position as % of portfolio)
        max_position_value = max(positions.values()) if positions else 0
        concentration = (max_position_value / portfolio_value * 100) if portfolio_value > 0 else 0
        
        # Check if any position exceeds max allocation
        max_allocation = portfolio_value * self.max_position_pct
        oversized_positions = {
            symbol: value 
            for symbol, value in positions.items() 
            if value > max_allocation
        }
        
        return {
            'cash_pct': cash_pct,
            'invested_pct': invested_pct,
            'concentration': concentration,
            'position_count': len(positions),
            'max_position_value': max_position_value,
            'oversized_positions': oversized_positions,
            'usable_cash': max(0, cash - (cash * self.reserve_cash_pct)),
            'reserved_cash': cash * self.reserve_cash_pct
        }
