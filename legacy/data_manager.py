"""
Data access layer for trading bot database operations.
Provides high-level functions with proper error handling and logging.
"""
import logging
import traceback
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import desc, func, and_, or_

from database import db_manager
from models import (
    TradingSession, Trade, MarketData, ErrorLog, PerformanceMetrics,
    TradingSessionCreate, TradeCreate, MarketDataCreate, ErrorLogCreate
)

class TradingDataManager:
    """High-level interface for trading data operations"""
    
    def __init__(self):
        self.current_session_id: Optional[int] = None
        self.database_available = False
    
    def _check_database_availability(self) -> bool:
        """Check if database is available"""
        try:
            self.database_available = db_manager.is_available()
            return self.database_available
        except Exception as e:
            logging.warning(f"Database availability check failed: {e}")
            self.database_available = False
            return False
    
    def start_trading_session(self, session_data: TradingSessionCreate) -> Optional[int]:
        """Start a new trading session and return session ID"""
        if not self._check_database_availability():
            logging.warning("Database not available - trading session will not be logged")
            return None
            
        try:
            with db_manager.get_session() as db:
                session = TradingSession(
                    bot_version=session_data.bot_version,
                    configuration=session_data.configuration,
                    is_paper_trading=session_data.is_paper_trading,
                    notes=session_data.notes,
                    session_start=datetime.utcnow()
                )
                
                db.add(session)
                db.commit()
                db.refresh(session)
                
                self.current_session_id = session.id
                logging.info(f"Started trading session {session.id}")
                return session.id
                
        except SQLAlchemyError as e:
            logging.error(f"Failed to start trading session: {e}")
            return None
    
    def end_trading_session(self, session_id: Optional[int] = None, 
                           total_symbols: int = 0, total_trades: int = 0,
                           session_pnl: float = 0.0, error_count: int = 0) -> bool:
        """End a trading session with summary statistics"""
        target_session_id = session_id or self.current_session_id
        if not target_session_id:
            logging.warning("No active session to end")
            return False
        
        try:
            with db_manager.get_session() as db:
                session = db.query(TradingSession).filter(
                    TradingSession.id == target_session_id
                ).first()
                
                if session:
                    session.session_end = datetime.utcnow()
                    session.total_symbols_processed = total_symbols
                    session.total_trades_executed = total_trades
                    session.session_pnl = session_pnl
                    session.error_count = error_count
                    
                    db.commit()
                    logging.info(f"Ended trading session {target_session_id}")
                    
                    if target_session_id == self.current_session_id:
                        self.current_session_id = None
                    
                    return True
                else:
                    logging.warning(f"Session {target_session_id} not found")
                    return False
                    
        except SQLAlchemyError as e:
            logging.error(f"Failed to end trading session: {e}")
            return False
    
    def log_trade(self, trade_data: TradeCreate) -> Optional[int]:
        """Log a trade execution"""
        try:
            with db_manager.get_session() as db:
                trade = Trade(**trade_data.dict())
                
                db.add(trade)
                db.commit()
                db.refresh(trade)
                
                logging.debug(f"Logged trade {trade.id} for {trade.symbol}")
                return trade.id
                
        except SQLAlchemyError as e:
            logging.error(f"Failed to log trade: {e}")
            return None
    
    def update_trade_outcome(self, alpaca_order_id: str, status: str, 
                           fill_price: Optional[float] = None,
                           fill_time: Optional[datetime] = None,
                           pnl: Optional[float] = None) -> bool:
        """Update trade with execution outcome"""
        try:
            with db_manager.get_session() as db:
                trade = db.query(Trade).filter(
                    Trade.alpaca_order_id == alpaca_order_id
                ).first()
                
                if trade:
                    trade.status = status
                    if fill_price:
                        trade.price = fill_price
                    if fill_time:
                        trade.fill_time = fill_time
                    if pnl is not None:
                        trade.pnl = pnl
                    
                    db.commit()
                    logging.debug(f"Updated trade outcome for order {alpaca_order_id}")
                    return True
                else:
                    logging.warning(f"Trade with order ID {alpaca_order_id} not found")
                    return False
                    
        except SQLAlchemyError as e:
            logging.error(f"Failed to update trade outcome: {e}")
            return False
    
    def log_market_data(self, market_data: MarketDataCreate) -> Optional[int]:
        """Log market data snapshot"""
        try:
            with db_manager.get_session() as db:
                data = MarketData(**market_data.dict())
                
                db.add(data)
                db.commit()
                db.refresh(data)
                
                return data.id
                
        except SQLAlchemyError as e:
            logging.error(f"Failed to log market data: {e}")
            return None
    
    def log_error(self, error_data: ErrorLogCreate) -> Optional[int]:
        """Log an error with context"""
        try:
            with db_manager.get_session() as db:
                error = ErrorLog(**error_data.dict())
                
                db.add(error)
                db.commit()
                db.refresh(error)
                
                logging.debug(f"Logged error {error.id}")
                return error.id
                
        except SQLAlchemyError as e:
            logging.error(f"Failed to log error: {e}")
            return None
    
    def log_error_from_exception(self, session_id: int, error_type: str, 
                                symbol: Optional[str], exception: Exception,
                                context_data: Optional[Dict[str, Any]] = None) -> Optional[int]:
        """Log an error from an exception with full context"""
        error_data = ErrorLogCreate(
            session_id=session_id,
            error_type=error_type,
            symbol=symbol,
            error_message=str(exception),
            stack_trace=traceback.format_exc(),
            severity="ERROR",
            context_data=context_data
        )
        return self.log_error(error_data)
    
    def get_trading_performance(self, days: int = 30) -> Dict[str, Any]:
        """Get trading performance summary for the last N days"""
        try:
            with db_manager.get_session() as db:
                start_date = datetime.utcnow() - timedelta(days=days)
                
                # Get trade statistics
                trades = db.query(Trade).filter(
                    Trade.signal_time >= start_date,
                    Trade.status == "FILLED"
                ).all()
                
                if not trades:
                    return {"error": "No trades found in the specified period"}
                
                total_trades = len(trades)
                winning_trades = len([t for t in trades if (t.pnl or 0) > 0])
                losing_trades = len([t for t in trades if (t.pnl or 0) < 0])
                total_pnl = sum([t.pnl or 0 for t in trades])
                
                win_rate = winning_trades / total_trades if total_trades > 0 else 0
                avg_win = sum([t.pnl for t in trades if (t.pnl or 0) > 0]) / winning_trades if winning_trades > 0 else 0
                avg_loss = sum([t.pnl for t in trades if (t.pnl or 0) < 0]) / losing_trades if losing_trades > 0 else 0
                
                # Get unique symbols traded
                symbols_traded = len(set([t.symbol for t in trades]))
                
                return {
                    "period_days": days,
                    "total_trades": total_trades,
                    "winning_trades": winning_trades,
                    "losing_trades": losing_trades,
                    "win_rate": round(win_rate * 100, 2),
                    "total_pnl": round(total_pnl, 2),
                    "avg_win": round(avg_win, 2),
                    "avg_loss": round(avg_loss, 2),
                    "symbols_traded": symbols_traded,
                    "profit_factor": round(abs(avg_win * winning_trades / (avg_loss * losing_trades)), 2) if avg_loss != 0 and losing_trades > 0 else 0
                }
                
        except SQLAlchemyError as e:
            logging.error(f"Failed to get trading performance: {e}")
            return {"error": str(e)}
    
    def get_symbol_performance(self, symbol: str, days: int = 30) -> Dict[str, Any]:
        """Get performance data for a specific symbol"""
        try:
            with db_manager.get_session() as db:
                start_date = datetime.utcnow() - timedelta(days=days)
                
                trades = db.query(Trade).filter(
                    Trade.symbol == symbol,
                    Trade.signal_time >= start_date,
                    Trade.status == "FILLED"
                ).all()
                
                if not trades:
                    return {"symbol": symbol, "trades": 0, "message": "No trades found"}
                
                total_trades = len(trades)
                total_pnl = sum([t.pnl or 0 for t in trades])
                winning_trades = len([t for t in trades if (t.pnl or 0) > 0])
                
                return {
                    "symbol": symbol,
                    "trades": total_trades,
                    "total_pnl": round(total_pnl, 2),
                    "win_rate": round(winning_trades / total_trades * 100, 2),
                    "avg_pnl_per_trade": round(total_pnl / total_trades, 2)
                }
                
        except SQLAlchemyError as e:
            logging.error(f"Failed to get symbol performance: {e}")
            return {"error": str(e)}
    
    def get_recent_errors(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Get recent errors for monitoring"""
        try:
            with db_manager.get_session() as db:
                start_time = datetime.utcnow() - timedelta(hours=hours)
                
                errors = db.query(ErrorLog).filter(
                    ErrorLog.timestamp >= start_time
                ).order_by(desc(ErrorLog.timestamp)).limit(50).all()
                
                return [{
                    "timestamp": error.timestamp,
                    "error_type": error.error_type,
                    "symbol": error.symbol,
                    "message": error.error_message,
                    "severity": error.severity
                } for error in errors]
                
        except SQLAlchemyError as e:
            logging.error(f"Failed to get recent errors: {e}")
            return []
    
    def cleanup_old_data(self, days_to_keep: int = 90) -> bool:
        """Clean up old data to manage database size"""
        try:
            with db_manager.get_session() as db:
                cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
                
                # Delete old market data (most voluminous)
                market_data_deleted = db.query(MarketData).filter(
                    MarketData.timestamp < cutoff_date
                ).delete()
                
                # Delete old error logs
                error_logs_deleted = db.query(ErrorLog).filter(
                    ErrorLog.timestamp < cutoff_date
                ).delete()
                
                db.commit()
                
                logging.info(f"Cleaned up old data: {market_data_deleted} market data records, "
                           f"{error_logs_deleted} error log records")
                return True
                
        except SQLAlchemyError as e:
            logging.error(f"Failed to cleanup old data: {e}")
            return False

# Global data manager instance
data_manager = TradingDataManager()