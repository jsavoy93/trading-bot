"""
Alternative database implementation using Supabase REST API.
This works around network restrictions by using HTTPS instead of direct PostgreSQL connections.
"""
import os
import logging
import json
from datetime import datetime
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict
from supabase import create_client, Client

@dataclass
class TradingSessionREST:
    """Trading session data for REST API"""
    id: Optional[int] = None
    session_start: str = None
    session_end: Optional[str] = None
    bot_version: Optional[str] = None
    configuration: Optional[Dict] = None
    total_symbols_processed: int = 0
    total_trades_executed: int = 0
    session_pnl: float = 0.0
    error_count: int = 0
    is_paper_trading: bool = True
    notes: Optional[str] = None

@dataclass
class TradeREST:
    """Trade data for REST API"""
    id: Optional[int] = None
    session_id: int = None
    alpaca_order_id: str = None
    symbol: str = None
    side: str = None
    quantity: int = None
    price: Optional[float] = None
    order_price: Optional[float] = None
    signal_time: str = None
    order_time: str = None
    fill_time: Optional[str] = None
    sma_fast: Optional[float] = None
    sma_slow: Optional[float] = None
    rsi: Optional[float] = None
    signal_strength: Optional[str] = None
    status: str = None
    pnl: Optional[float] = None
    market_conditions: Optional[Dict] = None

class SupabaseRESTManager:
    """Database manager using Supabase REST API instead of direct PostgreSQL"""
    
    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_ANON_KEY")
        self.supabase_service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        self.supabase: Optional[Client] = None
        self.current_session_id: Optional[int] = None
        self.available = False
        
        self._initialize()
    
    def _initialize(self):
        """Initialize Supabase client"""
        try:
            if not self.supabase_url or not self.supabase_key:
                logging.warning("Missing Supabase credentials for REST API")
                return False
            
            # Use service role key if available for full access, otherwise anon key
            key_to_use = self.supabase_service_key if self.supabase_service_key else self.supabase_key
            self.supabase = create_client(self.supabase_url, key_to_use)
            
            # Test connection with a simple query
            result = self.supabase.table('trading_sessions').select("id").limit(1).execute()
            
            self.available = True
            logging.info("✅ Supabase REST API connection successful")
            return True
            
        except Exception as e:
            logging.warning(f"Supabase REST API initialization failed: {e}")
            logging.info("Database tables may not exist yet - will try to create them")
            self.available = False
            return False
    
    def is_available(self) -> bool:
        """Check if REST API is available"""
        return self.available and self.supabase is not None
    
    def create_tables_via_sql(self):
        """Create tables using Supabase SQL editor functionality"""
        if not self.is_available():
            return False
        
        try:
            # Create tables using Supabase's RPC (Remote Procedure Call) functionality
            # This requires the tables to be created manually in Supabase dashboard first
            logging.info("⚠️  Tables need to be created manually in Supabase dashboard")
            logging.info("Go to https://supabase.com/dashboard → SQL Editor and run:")
            
            sql_commands = """
-- Create trading_sessions table
CREATE TABLE IF NOT EXISTS trading_sessions (
    id SERIAL PRIMARY KEY,
    session_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_end TIMESTAMP,
    bot_version VARCHAR(50),
    configuration JSON,
    total_symbols_processed INTEGER DEFAULT 0,
    total_trades_executed INTEGER DEFAULT 0,
    session_pnl FLOAT DEFAULT 0.0,
    error_count INTEGER DEFAULT 0,
    is_paper_trading BOOLEAN DEFAULT true,
    notes TEXT
);

-- Create trades table
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES trading_sessions(id),
    alpaca_order_id VARCHAR(100) UNIQUE,
    symbol VARCHAR(10),
    side VARCHAR(10),
    quantity INTEGER,
    price FLOAT,
    order_price FLOAT,
    signal_time TIMESTAMP,
    order_time TIMESTAMP,
    fill_time TIMESTAMP,
    sma_fast FLOAT,
    sma_slow FLOAT,
    rsi FLOAT,
    signal_strength VARCHAR(20),
    status VARCHAR(20),
    pnl FLOAT,
    market_conditions JSON
);

-- Enable Row Level Security and allow all operations for authenticated users
ALTER TABLE trading_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all operations for authenticated users" ON trading_sessions FOR ALL USING (true);
CREATE POLICY "Allow all operations for authenticated users" ON trades FOR ALL USING (true);
"""
            
            print("\n" + "="*60)
            print("📋 SQL COMMANDS TO RUN IN SUPABASE DASHBOARD:")
            print("="*60)
            print(sql_commands)
            print("="*60)
            
            return False  # Manual step required
            
        except Exception as e:
            logging.error(f"Failed to create tables: {e}")
            return False
    
    def start_trading_session(self, bot_version: str = "1.0.0", 
                            configuration: Dict = None,
                            is_paper_trading: bool = True,
                            notes: str = None) -> Optional[int]:
        """Start a new trading session"""
        if not self.is_available():
            logging.warning("Supabase REST API not available")
            return None
        
        try:
            session_data = {
                "session_start": datetime.utcnow().isoformat(),
                "bot_version": bot_version,
                "configuration": configuration,
                "is_paper_trading": is_paper_trading,
                "notes": notes
            }
            
            result = self.supabase.table('trading_sessions').insert(session_data).execute()
            
            if result.data and len(result.data) > 0:
                session_id = result.data[0]['id']
                self.current_session_id = session_id
                logging.info(f"✅ Started trading session {session_id} via REST API")
                return session_id
            else:
                logging.error("Failed to create trading session - no data returned")
                return None
                
        except Exception as e:
            logging.error(f"Failed to start trading session via REST API: {e}")
            return None
    
    def log_trade(self, session_id: int, alpaca_order_id: str, symbol: str,
                  side: str, quantity: int, order_price: float,
                  signal_time: datetime, order_time: datetime,
                  sma_fast: float = None, sma_slow: float = None,
                  rsi: float = None, signal_strength: str = None,
                  status: str = "PENDING", market_conditions: Dict = None) -> Optional[int]:
        """Log a trade via REST API"""
        if not self.is_available():
            return None
        
        try:
            trade_data = {
                "session_id": session_id,
                "alpaca_order_id": alpaca_order_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_price": order_price,
                "signal_time": signal_time.isoformat(),
                "order_time": order_time.isoformat(),
                "sma_fast": sma_fast,
                "sma_slow": sma_slow,
                "rsi": rsi,
                "signal_strength": signal_strength,
                "status": status,
                "market_conditions": market_conditions
            }
            
            result = self.supabase.table('trades').insert(trade_data).execute()
            
            if result.data and len(result.data) > 0:
                trade_id = result.data[0]['id']
                logging.debug(f"✅ Logged trade {trade_id} for {symbol}")
                return trade_id
            else:
                logging.warning(f"Failed to log trade for {symbol}")
                return None
                
        except Exception as e:
            logging.warning(f"Failed to log trade via REST API: {e}")
            return None
    
    def end_trading_session(self, session_id: int = None,
                          total_symbols: int = 0, total_trades: int = 0,
                          session_pnl: float = 0.0, error_count: int = 0) -> bool:
        """End a trading session"""
        target_session_id = session_id or self.current_session_id
        if not target_session_id or not self.is_available():
            return False
        
        try:
            update_data = {
                "session_end": datetime.utcnow().isoformat(),
                "total_symbols_processed": total_symbols,
                "total_trades_executed": total_trades,
                "session_pnl": session_pnl,
                "error_count": error_count
            }
            
            result = self.supabase.table('trading_sessions').update(update_data).eq('id', target_session_id).execute()
            
            if result.data:
                logging.info(f"✅ Ended trading session {target_session_id}")
                if target_session_id == self.current_session_id:
                    self.current_session_id = None
                return True
            else:
                logging.warning(f"Failed to end trading session {target_session_id}")
                return False
                
        except Exception as e:
            logging.error(f"Failed to end trading session: {e}")
            return False
    
    def get_performance_summary(self, days: int = 30) -> Dict[str, Any]:
        """Get basic performance summary"""
        if not self.is_available():
            return {"error": "Database not available"}
        
        try:
            # Get recent sessions
            result = self.supabase.table('trading_sessions').select('*').order('session_start', desc=True).limit(10).execute()
            
            if result.data:
                sessions = result.data
                total_trades = sum([s.get('total_trades_executed', 0) for s in sessions])
                total_pnl = sum([s.get('session_pnl', 0) for s in sessions])
                
                return {
                    "recent_sessions": len(sessions),
                    "total_trades": total_trades,
                    "total_pnl": round(total_pnl, 2),
                    "sessions": sessions[:5]  # Last 5 sessions
                }
            else:
                return {"message": "No sessions found"}
                
        except Exception as e:
            logging.error(f"Failed to get performance summary: {e}")
            return {"error": str(e)}

# Global REST manager instance
rest_manager = SupabaseRESTManager()