-- Migration 0003 - Cooldown Persistence Only
-- Generated on 2025-11-22T02:34:23.038570
-- Run this script in Supabase SQL Editor
-- This migration only creates the new cooldown tables

-- Create research_cooldowns table
CREATE TABLE IF NOT EXISTS research_cooldowns (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    last_research_time TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for research_cooldowns
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_cooldowns_symbol ON research_cooldowns(symbol);
CREATE INDEX IF NOT EXISTS idx_research_cooldowns_time ON research_cooldowns(last_research_time DESC);

-- Create position_sell_cooldowns table
CREATE TABLE IF NOT EXISTS position_sell_cooldowns (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    last_analysis_time TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for position_sell_cooldowns
CREATE UNIQUE INDEX IF NOT EXISTS idx_position_sell_cooldowns_symbol ON position_sell_cooldowns(symbol);
CREATE INDEX IF NOT EXISTS idx_position_sell_cooldowns_time ON position_sell_cooldowns(last_analysis_time DESC);

-- Create trade_cooldowns table
CREATE TABLE IF NOT EXISTS trade_cooldowns (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    last_trade_time TIMESTAMP NOT NULL,
    trade_type VARCHAR(10),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for trade_cooldowns
CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_cooldowns_symbol ON trade_cooldowns(symbol);
CREATE INDEX IF NOT EXISTS idx_trade_cooldowns_time ON trade_cooldowns(last_trade_time DESC);

-- Enable Row Level Security
ALTER TABLE research_cooldowns ENABLE ROW LEVEL SECURITY;
ALTER TABLE position_sell_cooldowns ENABLE ROW LEVEL SECURITY;
ALTER TABLE trade_cooldowns ENABLE ROW LEVEL SECURITY;

-- Create policies (allow all operations for authenticated users)
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'research_cooldowns' 
        AND policyname = 'Allow all operations'
    ) THEN
        CREATE POLICY "Allow all operations" ON research_cooldowns FOR ALL USING (true);
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'position_sell_cooldowns' 
        AND policyname = 'Allow all operations'
    ) THEN
        CREATE POLICY "Allow all operations" ON position_sell_cooldowns FOR ALL USING (true);
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'trade_cooldowns' 
        AND policyname = 'Allow all operations'
    ) THEN
        CREATE POLICY "Allow all operations" ON trade_cooldowns FOR ALL USING (true);
    END IF;
END $$;

-- Update version tracking
INSERT INTO schema_migrations (version, description) 
VALUES (3, 'Add cooldown persistence tables') 
ON CONFLICT (version) DO NOTHING;
