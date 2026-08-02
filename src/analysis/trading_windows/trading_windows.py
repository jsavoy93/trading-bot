"""
Time-of-Day Trading Optimization

Analyzes historical trade performance by time of day and day of week,
then implements trading window restrictions.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import pandas as pd

logger = logging.getLogger(__name__)


def analyze_trade_timing(db=None) -> pd.DataFrame:
    """
    Analyze historical trade performance by hour and day of week.
    
    Args:
        db: Supabase client (optional)
        
    Returns:
        DataFrame with columns: hour, day_of_week, trade_count, win_rate, 
        avg_return, avg_loss, profit_factor
    """
    if db is None:
        from src.database.simple_rest import SimpleSupabaseREST
        db = SimpleSupabaseREST()
    
    if not db.is_available():
        logger.warning("Database not available, cannot analyze trade timing")
        return pd.DataFrame()
    
    try:
        import requests
        
        # Get all closed trades
        response = requests.get(
            f"{db.rest_url}/trades?select=*&status=eq.closed",
            headers=db.headers,
            timeout=30
        )
        
        if response.status_code != 200:
            logger.warning(f"Failed to get trades: {response.status_code}")
            return pd.DataFrame()
        
        trades = response.json()
        
        if not trades:
            logger.info("No closed trades found for timing analysis")
            return pd.DataFrame()
        
        # Convert to DataFrame
        df = pd.DataFrame(trades)
        
        if 'closed_at' not in df.columns or 'pnl_percent' not in df.columns:
            logger.warning("Missing required columns for timing analysis")
            return pd.DataFrame()
        
        # Parse timestamps
        df['closed_at'] = pd.to_datetime(df['closed_at'])
        df['hour'] = df['closed_at'].dt.hour
        df['day_of_week'] = df['closed_at'].dt.dayofweek  # 0=Monday
        
        # Calculate metrics by hour
        results = []
        
        for hour in range(9, 16):  # Market hours 9AM-4PM
            hour_df = df[df['hour'] == hour]
            
            if len(hour_df) == 0:
                continue
            
            wins = hour_df[hour_df['pnl_percent'] > 0]
            losses = hour_df[hour_df['pnl_percent'] <= 0]
            
            win_rate = len(wins) / len(hour_df) if len(hour_df) > 0 else 0
            avg_return = hour_df['pnl_percent'].mean() if len(hour_df) > 0 else 0
            avg_loss = losses['pnl_percent'].mean() if len(losses) > 0 else 0
            
            gross_profit = wins['pnl_percent'].sum() if len(wins) > 0 else 0
            gross_loss = abs(losses['pnl_percent'].sum()) if len(losses) > 0 else 1
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
            
            results.append({
                'hour': hour,
                'day_of_week': None,  # Aggregate across days
                'trade_count': len(hour_df),
                'win_rate': win_rate,
                'avg_return': avg_return,
                'avg_loss': avg_loss,
                'profit_factor': profit_factor
            })
        
        # Calculate metrics by day of week
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
        
        for day in range(5):  # Monday=0 to Friday=4
            day_df = df[df['day_of_week'] == day]
            
            if len(day_df) == 0:
                continue
            
            wins = day_df[day_df['pnl_percent'] > 0]
            losses = day_df[day_df['pnl_percent'] <= 0]
            
            win_rate = len(wins) / len(day_df) if len(day_df) > 0 else 0
            avg_return = day_df['pnl_percent'].mean() if len(day_df) > 0 else 0
            avg_loss = losses['pnl_percent'].mean() if len(losses) > 0 else 0
            
            gross_profit = wins['pnl_percent'].sum() if len(wins) > 0 else 0
            gross_loss = abs(losses['pnl_percent'].sum()) if len(losses) > 0 else 1
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
            
            results.append({
                'hour': None,
                'day_of_week': day,
                'day_name': day_names[day],
                'trade_count': len(day_df),
                'win_rate': win_rate,
                'avg_return': avg_return,
                'avg_loss': avg_loss,
                'profit_factor': profit_factor
            })
        
        result_df = pd.DataFrame(results)
        
        # Save to database
        save_timing_analysis(result_df, db)
        
        # Print summary
        print("\n" + "="*60)
        print("TRADE TIMING ANALYSIS")
        print("="*60)
        
        print("\n📊 BY HOUR:")
        hour_df = result_df[result_df['hour'].notna()].sort_values('hour')
        print(f"{'Hour':<8} {'Trades':<10} {'Win%':<10} {'Avg%':<10} {'Profit Factor':<15}")
        print("-"*55)
        for _, row in hour_df.iterrows():
            print(f"{row['hour']:02d}:00    {row['trade_count']:<10} {row['win_rate']*100:.1f}%{'':<5} {row['avg_return']:+.2f}%{'':<5} {row['profit_factor']:.2f}")
        
        print("\n📊 BY DAY:")
        day_df = result_df[result_df['day_of_week'].notna()].sort_values('day_of_week')
        print(f"{'Day':<12} {'Trades':<10} {'Win%':<10} {'Avg%':<10} {'Profit Factor':<15}")
        print("-"*55)
        for _, row in day_df.iterrows():
            print(f"{row['day_name']:<12} {row['trade_count']:<10} {row['win_rate']*100:.1f}%{'':<5} {row['avg_return']:+.2f}%{'':<5} {row['profit_factor']:.2f}")
        
        return result_df
        
    except Exception as e:
        logger.error(f"Error analyzing trade timing: {e}")
        return pd.DataFrame()


def save_timing_analysis(df: pd.DataFrame, db=None) -> bool:
    """Save timing analysis to database."""
    if df.empty:
        return False
    
    if db is None:
        from src.database.simple_rest import SimpleSupabaseREST
        db = SimpleSupabaseREST()
    
    if not db.is_available():
        return False
    
    try:
        import requests
        
        # Delete old analysis and insert new
        requests.delete(
            f"{db.rest_url}/trade_timing_analysis",
            headers={**db.headers, "Prefer": "return=minimal"},
            timeout=10
        )
        
        # Insert new data
        records = df.to_dict('records')
        # Handle NaN values
        for r in records:
            for k, v in r.items():
                if pd.isna(v):
                    r[k] = None
        
        response = requests.post(
            f"{db.rest_url}/trade_timing_analysis",
            headers={**db.headers, "Prefer": "resolution=merge-duplicates"},
            json=records,
            timeout=10
        )
        
        return response.status_code in [200, 201]
        
    except Exception as e:
        logger.debug(f"Error saving timing analysis: {e}")
        return False


def get_trading_windows(timing_data: pd.DataFrame = None) -> Dict:
    """
    Get allowed/blocked trading windows based on historical performance.
    
    Args:
        timing_data: Optional DataFrame from analyze_trade_timing()
        
    Returns:
        Dict with blocked_times, blocked_days, optimal_hours, reason
    """
    # Always blocked: first 15 min after open (9:30-9:45 ET)
    blocked_times = [
        {'start': '09:30', 'end': '09:45', 'reason': 'First 15 min - too volatile, wide spreads'}
    ]
    
    # Always blocked: last 15 min before close (3:45-4:00 ET)
    blocked_times.append(
        {'start': '15:45', 'end': '16:00', 'reason': 'Last 15 min - thin liquidity, erratic moves'}
    )
    
    # Conditionally block based on historical data
    optimal_hours = []
    reasons = {}
    
    if timing_data is not None and not timing_data.empty:
        hour_df = timing_data[timing_data['hour'].notna()]
        
        for _, row in hour_df.iterrows():
            hour = int(row['hour'])
            
            # Block if win rate < 40% and sample size > 20
            if row['win_rate'] < 0.40 and row['trade_count'] > 20:
                blocked_times.append({
                    'start': f'{hour:02d}:00',
                    'end': f'{hour+1:02d}:00',
                    'reason': f'Historical win rate {row["win_rate"]*100:.1f}% < 40%'
                })
                reasons[f'hour_{hour}'] = f"win_rate={row['win_rate']:.2f}"
            # Mark as optimal if win rate > 55% and sample > 10
            elif row['win_rate'] > 0.55 and row['trade_count'] > 10:
                optimal_hours.append(hour)
        
        # Check days
        day_df = timing_data[timing_data['day_of_week'].notna()]
        blocked_days = []
        
        for _, row in day_df.iterrows():
            day = int(row['day_of_week'])
            # Block if profit factor < 0.8 and sample > 30
            if row['profit_factor'] < 0.8 and row['trade_count'] > 30:
                day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
                blocked_days.append({
                    'day': day,
                    'name': day_names[day],
                    'reason': f'Profit factor {row["profit_factor"]:.2f} < 0.8'
                })
                reasons[f'day_{day}'] = f"profit_factor={row['profit_factor']:.2f}"
    else:
        blocked_days = []
    
    # Default optimal hours if no data
    if not optimal_hours:
        optimal_hours = [10, 11, 12, 13, 14]  # Mid-day typically best
    
    return {
        'blocked_times': blocked_times,
        'blocked_days': blocked_days if 'blocked_days' in locals() else [],
        'optimal_hours': optimal_hours,
        'reasons': reasons
    }


def is_trading_allowed(current_time_et: datetime = None) -> Tuple[bool, str]:
    """
    Check if trading is allowed at the current time.
    
    Args:
        current_time_et: Time to check (default: now in ET)
        
    Returns:
        (allowed: bool, reason: str)
    """
    from datetime import time
    
    if current_time_et is None:
        # Get current time in ET
        now_utc = datetime.now(timezone.utc)
        # ET is UTC-5 or UTC-4 depending on DST
        offset = -5  # Simplified, ignoring DST
        now_et = now_utc + timedelta(hours=offset)
        current_time_et = now_et
    
    # Extract time and day
    current_time = current_time_et.time()
    current_day = current_time_et.weekday()  # 0=Monday
    
    # Weekend check
    if current_day >= 5:  # Saturday=5, Sunday=6
        return False, "Weekend - market closed"
    
    # First 15 min after open (9:30-9:45 ET)
    if time(9, 30) <= current_time < time(9, 45):
        return False, "First 15 minutes - too volatile"
    
    # Last 15 min before close (3:45-4:00 ET)
    if time(15, 45) <= current_time < time(16, 0):
        return False, "Last 15 minutes - thin liquidity"
    
    # Get trading windows to check conditional blocks
    # For now, use defaults (this would use cached timing data in production)
    windows = get_trading_windows()
    
    # Check blocked days
    for blocked in windows.get('blocked_days', []):
        if blocked['day'] == current_day:
            return False, f"Historical underperformance on {blocked['name']}"
    
    # Check blocked hours
    current_hour = current_time_et.hour
    for blocked in windows.get('blocked_times', []):
        start_hour = int(blocked['start'].split(':')[0])
        end_hour = int(blocked['end'].split(':')[0])
        if start_hour <= current_hour < end_hour:
            # Skip if it's one of the always-blocked times we already checked
            if 'volatile' not in blocked.get('reason', '').lower():
                return False, blocked.get('reason', 'Blocked hour')
    
    return True, "Trading allowed"


# Re-evaluation queue for skipped signals
_skipped_signals_queue = []


def add_to_reevaluation_queue(ticker: str, signal_data: Dict):
    """Add a signal to the re-evaluation queue."""
    global _skipped_signals_queue
    
    entry = {
        'ticker': ticker,
        'signal_data': signal_data,
        'skipped_at': datetime.now(timezone.utc),
        'signal_strength': signal_data.get('total_score', 0)
    }
    
    _skipped_signals_queue.append(entry)
    logger.info(f"📋 Added {ticker} to re-evaluation queue (strength: {entry['signal_strength']})")


def get_queued_signals() -> List[Dict]:
    """Get all signals waiting for re-evaluation."""
    return _skipped_signals_queue.copy()


def clear_reevaluation_queue():
    """Clear the re-evaluation queue."""
    global _skipped_signals_queue
    _skipped_signals_queue = []


def log_skipped_signal(ticker: str, reason: str, signal_data: Dict, db=None) -> bool:
    """Log a skipped signal to the database."""
    if db is None:
        from src.database.simple_rest import SimpleSupabaseREST
        db = SimpleSupabaseREST()
    
    if not db.is_available():
        return False
    
    try:
        import requests
        
        record = {
            'ticker': ticker,
            'signal_time': datetime.now(timezone.utc).isoformat(),
            'signal_strength': signal_data.get('total_score', 0),
            'reason': reason,
            'price_at_signal': signal_data.get('price'),
            'rsi_at_signal': signal_data.get('rsi'),
        }
        
        response = requests.post(
            f"{db.rest_url}/skipped_signals",
            headers=db.headers,
            json=record,
            timeout=10
        )
        
        return response.status_code in [200, 201]
        
    except Exception as e:
        logger.debug(f"Error logging skipped signal: {e}")
        return False


def check_and_execute_queued_signals(bot, max_trades: int = 2) -> List[Dict]:
    """
    Re-check queued signals when trading window opens.
    
    Args:
        bot: SmartTradingBot instance
        max_trades: Max trades to execute
        
    Returns:
        List of execution results
    """
    global _skipped_signals_queue
    
    allowed, reason = is_trading_allowed()
    if not allowed:
        return []
    
    if not _skipped_signals_queue:
        return []
    
    results = []
    
    # Re-evaluate each queued signal
    for entry in list(_skipped_signals_queue):  # Copy list to modify while iterating
        ticker = entry['ticker']
        
        # Re-analyze with fresh data
        analysis = bot.analyze_symbol(ticker)
        
        if analysis is None:
            _skipped_signals_queue.remove(entry)
            continue
        
        signal = analysis.get('signal')
        score = analysis.get('total_score', 0)
        
        # Check if still valid
        if signal == 'BUY' and score >= 65:
            # Still valid - execute
            if len(results) < max_trades:
                success = bot.execute_trade(analysis)
                results.append({
                    'ticker': ticker,
                    'executed': success,
                    'reason': 'Re-evaluated and executed'
                })
                _skipped_signals_queue.remove(entry)
            else:
                break  # Max trades reached
        else:
            # Signal expired
            logger.info(f"⏱️ Signal expired for {ticker} (score: {score})")
            _skipped_signals_queue.remove(entry)
    
    return results
