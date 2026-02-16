CREATE TABLE IF NOT EXISTS symbols (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) UNIQUE NOT NULL,
    name VARCHAR(255),
    sector VARCHAR(100),
    industry VARCHAR(100),
    market_cap BIGINT,
    exchange VARCHAR(20),
    active BOOLEAN DEFAULT true,
    ipo_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Daily OHLCV data
CREATE TABLE IF NOT EXISTS ohlcv_daily (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    open NUMERIC(10, 4),
    high NUMERIC(10, 4),
    low NUMERIC(10, 4),
    close NUMERIC(10, 4),
    adjusted_close NUMERIC(10, 4),
    volume BIGINT,
    dividends NUMERIC(10, 6),
    stock_splits NUMERIC(10, 6),
    typical_price NUMERIC(10, 4),
    traded_value NUMERIC(20, 2),
    source VARCHAR(20) DEFAULT 'yahoo',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_daily_symbol_date ON ohlcv_daily(symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_daily_date ON ohlcv_daily(date);

-- Hourly OHLCV data
CREATE TABLE IF NOT EXISTS ohlcv_hourly (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    open NUMERIC(10, 4),
    high NUMERIC(10, 4),
    low NUMERIC(10, 4),
    close NUMERIC(10, 4),
    volume BIGINT,
    source VARCHAR(20) DEFAULT 'alpaca',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_hourly_symbol_timestamp ON ohlcv_hourly(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_hourly_timestamp ON ohlcv_hourly(timestamp);

-- Data sync log
CREATE TABLE IF NOT EXISTS data_sync_log (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(20) NOT NULL,
    start_date DATE,
    end_date DATE,
    rows_synced INTEGER DEFAULT 0,
    status VARCHAR(20) DEFAULT 'pending',
    error_message TEXT,
    source VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_data_sync_log_symbol ON data_sync_log(symbol, timeframe);

