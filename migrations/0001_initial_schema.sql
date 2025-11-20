-- Migration 0001
-- Generated on 2025-11-20T03:45:04.690833
-- Run this script in Supabase SQL Editor

-- Create trading_sessions table
CREATE TABLE IF NOT EXISTS trading_sessions (
    id SERIAL PRIMARY KEY,
    session_start TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    session_end TIMESTAMP,
    bot_version VARCHAR(50),
    configuration JSON,
    total_symbols_processed INTEGER NOT NULL DEFAULT 0,
    total_trades_executed INTEGER NOT NULL DEFAULT 0,
    session_pnl FLOAT NOT NULL DEFAULT 0.0,
    error_count INTEGER NOT NULL DEFAULT 0,
    is_paper_trading BOOLEAN NOT NULL DEFAULT true,
    notes TEXT
);

-- Indexes for trading_sessions
CREATE INDEX idx_trading_sessions_start ON trading_sessions(session_start DESC);
CREATE INDEX idx_trading_sessions_paper ON trading_sessions(is_paper_trading);

-- Create trades table
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL,
    alpaca_order_id VARCHAR(100) UNIQUE,
    symbol VARCHAR(10) NOT NULL,
    side VARCHAR(10) NOT NULL,
    quantity INTEGER NOT NULL,
    price FLOAT,
    order_price FLOAT,
    signal_time TIMESTAMP NOT NULL,
    order_time TIMESTAMP NOT NULL,
    fill_time TIMESTAMP,
    sma_fast FLOAT,
    sma_slow FLOAT,
    rsi FLOAT,
    signal_strength VARCHAR(20),
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    pnl FLOAT,
    market_conditions JSON,
    FOREIGN KEY (session_id) REFERENCES trading_sessions(id)
);

-- Indexes for trades
CREATE INDEX idx_trades_session ON trades(session_id);
CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_signal_time ON trades(signal_time DESC);
CREATE UNIQUE INDEX idx_trades_alpaca_order ON trades(alpaca_order_id) WHERE alpaca_order_id IS NOT NULL;

-- Create market_data table
CREATE TABLE IF NOT EXISTS market_data (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    open_price FLOAT NOT NULL,
    high_price FLOAT NOT NULL,
    low_price FLOAT NOT NULL,
    close_price FLOAT NOT NULL,
    volume BIGINT NOT NULL,
    sma_fast FLOAT,
    sma_slow FLOAT,
    rsi FLOAT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for market_data
CREATE UNIQUE INDEX idx_market_data_symbol_time ON market_data(symbol, timestamp);
CREATE INDEX idx_market_data_symbol ON market_data(symbol);
CREATE INDEX idx_market_data_timestamp ON market_data(timestamp DESC);

-- Create error_logs table
CREATE TABLE IF NOT EXISTS error_logs (
    id SERIAL PRIMARY KEY,
    session_id INTEGER,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error_type VARCHAR(50) NOT NULL,
    error_message TEXT NOT NULL,
    symbol VARCHAR(10),
    context JSON,
    stack_trace TEXT,
    FOREIGN KEY (session_id) REFERENCES trading_sessions(id)
);

-- Indexes for error_logs
CREATE INDEX idx_error_logs_session ON error_logs(session_id);
CREATE INDEX idx_error_logs_timestamp ON error_logs(timestamp DESC);
CREATE INDEX idx_error_logs_type ON error_logs(error_type);

-- Create performance_metrics table
CREATE TABLE IF NOT EXISTS performance_metrics (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL,
    metric_date DATE NOT NULL,
    total_trades INTEGER NOT NULL DEFAULT 0,
    profitable_trades INTEGER NOT NULL DEFAULT 0,
    daily_pnl FLOAT NOT NULL DEFAULT 0.0,
    win_rate FLOAT,
    avg_profit_per_trade FLOAT,
    max_drawdown FLOAT,
    sharpe_ratio FLOAT,
    volatility FLOAT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES trading_sessions(id)
);

-- Indexes for performance_metrics
CREATE UNIQUE INDEX idx_performance_session_date ON performance_metrics(session_id, metric_date);
CREATE INDEX idx_performance_date ON performance_metrics(metric_date DESC);

-- Enable Row Level Security
ALTER TABLE trading_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE error_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE performance_metrics ENABLE ROW LEVEL SECURITY;

-- Create policies (allow all operations for authenticated users)
CREATE POLICY "Allow all operations" ON trading_sessions FOR ALL USING (true);
CREATE POLICY "Allow all operations" ON trades FOR ALL USING (true);
CREATE POLICY "Allow all operations" ON market_data FOR ALL USING (true);
CREATE POLICY "Allow all operations" ON error_logs FOR ALL USING (true);
CREATE POLICY "Allow all operations" ON performance_metrics FOR ALL USING (true);

-- Version tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all operations" ON schema_migrations FOR ALL USING (true);

INSERT INTO schema_migrations (version, description) VALUES (1, 'Initial schema creation') ON CONFLICT (version) DO NOTHING;