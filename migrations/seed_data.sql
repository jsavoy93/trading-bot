-- Seed data for testing
-- Run after initial migration

-- Insert test trading session
INSERT INTO trading_sessions (bot_version, configuration, is_paper_trading, notes) 
VALUES (
    '2.1.0',
    '{"sma_fast": 10, "sma_slow": 30, "rsi_period": 14, "trade_amount": 1000}',
    true,
    'Test session for migration validation'
) ON CONFLICT DO NOTHING;

-- You can add more seed data here as needed
