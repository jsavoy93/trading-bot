"""
Position Scaling Module

Handles scaled entry and exit for positions.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class EntryTranche:
    """An entry tranche definition"""
    tranche_num: int
    qty: int
    trigger: str
    status: str  # pending, filled, cancelled, skipped
    filled_price: Optional[float] = None
    filled_at: Optional[datetime] = None


@dataclass  
class ExitTranche:
    """An exit tranche definition"""
    tranche_num: int
    qty: int
    trigger: str
    trigger_price: float  # Price level that triggers exit
    status: str  # pending, filled, cancelled
    filled_price: Optional[float] = None
    filled_at: Optional[datetime] = None


def calculate_entry_tranches(ticker: str, total_qty: int, 
                           signal_strength: int = 75) -> Dict:
    """
    Split total quantity into entry tranches.
    
    Args:
        ticker: Stock symbol
        total_qty: Total shares to buy
        signal_strength: Signal score (0-100)
        
    Returns:
        Dict with tranches list and metadata
    """
    if total_qty <= 0:
        return {'tranches': [], 'total_qty': 0, 'ticker': ticker}
    
    # Calculate tranche sizes
    tranche_1_pct = 0.40  # 40% immediate
    tranche_2_pct = 0.35  # 35% on pullback
    tranche_3_pct = 0.25  # 25% on hold confirmation
    
    qty_1 = int(total_qty * tranche_1_pct)
    qty_2 = int(total_qty * tranche_2_pct)
    qty_3 = total_qty - qty_1 - qty_2  # Remainder
    
    # Ensure at least 1 share per tranche
    if qty_1 == 0 and total_qty >= 3:
        qty_1 = 1
        qty_2 = 1
        qty_3 = total_qty - 2
    
    tranches = [
        EntryTranche(
            tranche_num=1,
            qty=qty_1,
            trigger='immediate',
            status='pending'
        ),
        EntryTranche(
            tranche_num=2,
            qty=qty_2,
            trigger='pullback_to_vwap_or_confirm',
            status='pending'
        ),
        EntryTranche(
            tranche_num=3,
            qty=qty_3,
            trigger='hold_above_entry_4h',
            status='pending'
        )
    ]
    
    logger.info(f"📊 Entry tranches for {ticker}: {qty_1}/{qty_2}/{qty_3} ({total_qty} total)")
    
    return {
        'tranches': [asdict(t) for t in tranches],
        'total_qty': total_qty,
        'ticker': ticker,
        'signal_strength': signal_strength,
        'created_at': datetime.now(timezone.utc)
    }


def calculate_exit_tranches(ticker: str, total_qty: int, 
                          entry_price: float) -> Dict:
    """
    Split position into exit tranches.
    
    Args:
        ticker: Stock symbol
        total_qty: Total shares to sell
        entry_price: Average entry price
        
    Returns:
        Dict with exit tranches
    """
    if total_qty <= 0:
        return {'tranches': [], 'total_qty': 0, 'ticker': ticker}
    
    # Calculate exit prices
    # Exit 1: 15% take profit
    exit_1_price = entry_price * 1.15
    
    # Exit 2: RSI > 75 OR 3% trailing stop
    exit_2_trailing = 0.03  # 3% trailing
    
    # Exit 3: 5% trailing stop OR sell signal
    exit_3_trailing = 0.05  # 5% trailing
    
    # Split quantities: 33%, 33%, 34%
    qty_1 = int(total_qty * 0.33)
    qty_2 = int(total_qty * 0.33)
    qty_3 = total_qty - qty_1 - qty_2
    
    # Ensure at least 1 share per tranche
    if qty_1 == 0 and total_qty >= 3:
        qty_1 = 1
        qty_2 = 1
        qty_3 = total_qty - 2
    
    tranches = [
        ExitTranche(
            tranche_num=1,
            qty=qty_1,
            trigger='take_profit_15%',
            trigger_price=exit_1_price,
            status='pending'
        ),
        ExitTranche(
            tranche_num=2,
            qty=qty_2,
            trigger='rsi_overbought_or_trailing_3%',
            trigger_price=entry_price * (1 + exit_2_trailing),  # Initial trailing stop
            status='pending'
        ),
        ExitTranche(
            tranche_num=3,
            qty=qty_3,
            trigger='trailing_5pct_or_sell_signal',
            trigger_price=entry_price * (1 + exit_3_trailing),  # Initial trailing stop
            status='pending'
        )
    ]
    
    logger.info(f"📊 Exit tranches for {ticker} @ ${entry_price}: "
                f"T1=${exit_1_price:.2f}, T2=trailing3%, T3=trailing5%")
    
    return {
        'tranches': [asdict(t) for t in tranches],
        'total_qty': total_qty,
        'ticker': ticker,
        'entry_price': entry_price,
        'created_at': datetime.now(timezone.utc)
    }


def check_tranche_triggers(entry_plan: Dict, current_data: Dict) -> List[Dict]:
    """
    Check which entry tranches should trigger.
    
    Args:
        entry_plan: Output from calculate_entry_tranches()
        current_data: Dict with price, vwap, volume, rsi, hours_since_entry
        
    Returns:
        List of tranches to execute now
    """
    to_execute = []
    
    current_price = current_data.get('price', 0)
    vwap = current_data.get('vwap', current_price)
    rsi = current_data.get('rsi', 50)
    volume_ratio = current_data.get('volume_ratio', 1.0)
    hours_since_entry = current_data.get('hours_since_entry', 0)
    entry_price = current_data.get('entry_price', current_price)
    
    for tranche in entry_plan.get('tranches', []):
        if tranche['status'] != 'pending':
            continue
        
        trigger = tranche['trigger']
        should_execute = False
        
        if trigger == 'immediate':
            # Always execute immediately
            should_execute = True
            
        elif trigger == 'pullback_to_vwap_or_confirm':
            # Execute if price pulls back to VWAP or within 1% of entry
            # OR if RSI shows confirmation (not overbought)
            pullback_to_vwap = current_price <= vwap * 1.01
            pullback_to_entry = current_price <= entry_price * 1.01
            rsi_confirmation = rsi < 65
            
            if (pullback_to_vwap or pullback_to_entry) and rsi_confirmation:
                should_execute = True
                
        elif trigger == 'hold_above_entry_4h':
            # Execute if price held above entry for 4+ hours AND volume strong
            above_entry = current_price > entry_price
            time_confirmed = hours_since_entry >= 4
            volume_confirmed = volume_ratio >= 1.0
            
            if above_entry and time_confirmed and volume_confirmed:
                should_execute = True
        
        if should_execute:
            to_execute.append(tranche)
    
    return to_execute


def update_trailing_stops(exit_plan: Dict, current_price: float, 
                         highest_price: float) -> Dict:
    """
    Update trailing stop prices for exit tranches.
    
    Args:
        exit_plan: Output from calculate_exit_tranches()
        current_price: Current market price
        highest_price: Highest price since entry
        
    Returns:
        Updated exit plan
    """
    entry_price = exit_plan.get('entry_price', current_price)
    
    for tranche in exit_plan.get('tranches', []):
        if tranche['status'] != 'pending':
            continue
        
        trigger = tranche['trigger']
        
        if 'trailing_3%' in trigger:
            # 3% trailing stop from highest
            new_stop = highest_price * 0.97
            # Never go below entry price
            tranche['trigger_price'] = max(new_stop, entry_price)
            
        elif 'trailing_5%' in trigger:
            # 5% trailing stop from highest
            new_stop = highest_price * 0.95
            tranche['trigger_price'] = max(new_stop, entry_price)
    
    return exit_plan


def check_exit_triggers(exit_plan: Dict, current_price: float, 
                       current_rsi: float, highest_price: float,
                       sell_signal: bool = False) -> List[Dict]:
    """
    Check which exit tranches should trigger.
    
    Args:
        exit_plan: Output from calculate_exit_tranches()
        current_price: Current market price
        current_rsi: Current RSI
        highest_price: Highest price since entry
        sell_signal: Whether a sell signal exists
        
    Returns:
        List of tranches to exit now
    """
    to_exit = []
    
    for tranche in exit_plan.get('tranches', []):
        if tranche['status'] != 'pending':
            continue
        
        trigger = tranche['trigger']
        trigger_price = tranche['trigger_price']
        
        should_exit = False
        
        if trigger == 'take_profit_15%':
            # Exit if price hits target
            if current_price >= trigger_price:
                should_exit = True
                
        elif trigger == 'rsi_overbought_or_trailing_3%':
            # Exit if RSI overbought OR trailing stop hit
            if current_rsi >= 75:
                should_exit = True
            elif current_price <= trigger_price:
                should_exit = True
                
        elif trigger == 'trailing_5pct_or_sell_signal':
            # Exit if trailing stop hit OR sell signal
            if current_price <= trigger_price:
                should_exit = True
            elif sell_signal:
                should_exit = True
        
        if should_exit:
            to_exit.append(tranche)
    
    return to_exit


def cancel_pending_entry_tranches(position_id: str, db=None) -> bool:
    """
    Cancel pending entry tranches when stop-loss is hit.
    
    Args:
        position_id: Position identifier
        db: Supabase client
    """
    # In production, this would update the position_tranches table
    logger.info(f"🚫 Cancelling pending entry tranches for position {position_id}")
    return True


# Database functions
def save_position_tranches(position_id: str, ticker: str, 
                          entry_plan: Dict, exit_plan: Dict,
                          db=None) -> bool:
    """Save position tranches to database."""
    if db is None:
        from src.database.simple_rest import SimpleSupabaseREST
        db = SimpleSupabaseREST()
    
    if not db.is_available():
        return False
    
    try:
        import requests
        
        record = {
            'position_id': position_id,
            'ticker': ticker,
            'entry_tranches': entry_plan.get('tranches', []),
            'exit_tranches': exit_plan.get('tranches', []),
            'total_qty': entry_plan.get('total_qty', 0),
            'entry_price': exit_plan.get('entry_price'),
            'status': 'active',
        }
        
        response = requests.post(
            f"{db.rest_url}/position_tranches",
            headers=db.headers,
            json=record,
            timeout=10
        )
        
        return response.status_code in [200, 201]
        
    except Exception as e:
        logger.debug(f"Error saving position tranches: {e}")
        return False


def update_tranche_status(position_id: str, tranche_type: str, 
                         tranche_num: int, status: str, 
                         fill_price: float = None, db=None) -> bool:
    """Update tranche status in database."""
    # In production, this would update the existing record
    logger.debug(f"Updated {tranche_type} tranche {tranche_num} to {status}")
    return True
