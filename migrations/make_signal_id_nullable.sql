-- Migration: Make signal_id nullable in betting_orders table
-- This is needed to support NET sells which don't have an associated signal
-- Date: 2026-03-26

-- Make the signal_id column nullable
ALTER TABLE betting_orders
ALTER COLUMN signal_id DROP NOT NULL;

-- Add a comment explaining why this field can be null
COMMENT ON COLUMN betting_orders.signal_id IS
'Foreign key to betting_signals. Can be NULL for NET sells (position flips) that are not driven by a signal for the specific market.';