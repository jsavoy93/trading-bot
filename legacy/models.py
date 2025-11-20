"""
Database models for trading bot data storage and analysis.
"""
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from pydantic import BaseModel
import json

Base = declarative_base()

class TradingSession(Base):
    """Track overall trading sessions and bot runs"""
    __tablename__ = "trading_sessions"
    
    id = Column(Integer, primary_key=True)
    session_start = Column(DateTime, nullable=False, default=datetime.utcnow)
    session_end = Column(DateTime, nullable=True)
    bot_version = Column(String(50), nullable=True)
    configuration = Column(JSON, nullable=True)  # Store bot config as JSON
    total_symbols_processed = Column(Integer, default=0)
    total_trades_executed = Column(Integer, default=0)
    session_pnl = Column(Float, default=0.0)
    error_count = Column(Integer, default=0)
    is_paper_trading = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    
    # Relationships
    trades = relationship("Trade", back_populates="session")
    market_data = relationship("MarketData", back_populates="session")
    errors = relationship("ErrorLog", back_populates="session")

class Trade(Base):
    """Record all trade executions and their outcomes"""
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("trading_sessions.id"), nullable=False)
    
    # Trade identification
    alpaca_order_id = Column(String(100), unique=True, nullable=False)
    symbol = Column(String(10), nullable=False)
    
    # Trade details
    side = Column(String(10), nullable=False)  # BUY/SELL
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=True)  # Fill price
    order_price = Column(Float, nullable=True)  # Requested price
    
    # Timing
    signal_time = Column(DateTime, nullable=False)
    order_time = Column(DateTime, nullable=False)
    fill_time = Column(DateTime, nullable=True)
    
    # Technical indicators at time of trade
    sma_fast = Column(Float, nullable=True)
    sma_slow = Column(Float, nullable=True)
    rsi = Column(Float, nullable=True)
    signal_strength = Column(String(20), nullable=True)  # BUY/SELL/HOLD
    
    # Trade outcome tracking
    status = Column(String(20), nullable=False)  # PENDING/FILLED/CANCELLED/REJECTED
    pnl = Column(Float, nullable=True)  # Profit/Loss when closed
    hold_duration_minutes = Column(Integer, nullable=True)
    
    # Learning features
    success_score = Column(Float, nullable=True)  # 0-1 score for learning
    market_conditions = Column(JSON, nullable=True)  # Market context
    
    # Relationships
    session = relationship("TradingSession", back_populates="trades")

class MarketData(Base):
    """Store market data snapshots for analysis"""
    __tablename__ = "market_data"
    
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("trading_sessions.id"), nullable=False)
    
    symbol = Column(String(10), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    
    # OHLCV data
    open_price = Column(Float, nullable=False)
    high_price = Column(Float, nullable=False)
    low_price = Column(Float, nullable=False)
    close_price = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False)
    
    # Technical indicators
    sma_fast = Column(Float, nullable=True)
    sma_slow = Column(Float, nullable=True)
    rsi = Column(Float, nullable=True)
    
    # Market conditions
    volatility = Column(Float, nullable=True)
    trend_strength = Column(Float, nullable=True)
    
    # Relationships
    session = relationship("TradingSession", back_populates="market_data")

class ErrorLog(Base):
    """Track errors and issues for learning and debugging"""
    __tablename__ = "error_logs"
    
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("trading_sessions.id"), nullable=False)
    
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    error_type = Column(String(50), nullable=False)
    symbol = Column(String(10), nullable=True)
    error_message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    severity = Column(String(20), default="ERROR")  # INFO/WARNING/ERROR/CRITICAL
    
    # Context for learning
    context_data = Column(JSON, nullable=True)
    resolution_status = Column(String(20), default="OPEN")  # OPEN/RESOLVED/IGNORED
    
    # Relationships
    session = relationship("TradingSession", back_populates="errors")

class PerformanceMetrics(Base):
    """Track performance metrics for learning and optimization"""
    __tablename__ = "performance_metrics"
    
    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False)
    
    # Daily metrics
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    total_pnl = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)
    avg_win = Column(Float, default=0.0)
    avg_loss = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    
    # Strategy metrics
    avg_hold_time_minutes = Column(Float, default=0.0)
    symbols_traded = Column(Integer, default=0)
    strategy_effectiveness = Column(Float, default=0.0)
    
    # Market conditions
    market_volatility = Column(Float, nullable=True)
    market_trend = Column(String(20), nullable=True)  # BULL/BEAR/SIDEWAYS
    
    # Learning features
    pattern_success_rates = Column(JSON, nullable=True)  # Store pattern analysis
    indicator_effectiveness = Column(JSON, nullable=True)

# Pydantic models for API interactions and validation
class TradingSessionCreate(BaseModel):
    bot_version: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None
    is_paper_trading: bool = True
    notes: Optional[str] = None

class TradeCreate(BaseModel):
    session_id: int
    alpaca_order_id: str
    symbol: str
    side: str
    quantity: int
    price: Optional[float] = None
    order_price: Optional[float] = None
    signal_time: datetime
    order_time: datetime
    sma_fast: Optional[float] = None
    sma_slow: Optional[float] = None
    rsi: Optional[float] = None
    signal_strength: Optional[str] = None
    status: str
    market_conditions: Optional[Dict[str, Any]] = None

class MarketDataCreate(BaseModel):
    session_id: int
    symbol: str
    timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    sma_fast: Optional[float] = None
    sma_slow: Optional[float] = None
    rsi: Optional[float] = None
    volatility: Optional[float] = None
    trend_strength: Optional[float] = None

class ErrorLogCreate(BaseModel):
    session_id: int
    error_type: str
    symbol: Optional[str] = None
    error_message: str
    stack_trace: Optional[str] = None
    severity: str = "ERROR"
    context_data: Optional[Dict[str, Any]] = None