-- COMPLETE DATABASE CLEANUP SCRIPT
-- WARNING: This will DELETE ALL DATA from all tables
-- Run with caution!

-- Disable foreign key checks temporarily for easier deletion
-- (Note: PostgreSQL doesn't have a global foreign_key_checks like MySQL)

-- Start transaction
BEGIN;

-- Delete from all tables in dependency order (child tables first)

-- 1. Delete all betting/trading related data
DELETE FROM betting_fills;
DELETE FROM betting_orders;
DELETE FROM betting_signals;
DELETE FROM betting_predictions;

-- 2. Delete all model runs and snapshots
DELETE FROM model_runs;
DELETE FROM market_snapshots;
DELETE FROM price_snapshots;

-- 3. Delete alerts and logs
DELETE FROM system_alerts;
DELETE FROM alert_history;

-- 4. Delete positions
DELETE FROM positions;

-- 5. Delete markets
DELETE FROM markets;

-- 6. Delete any other tables that might exist
DELETE FROM comparison_results;
DELETE FROM model_calibration;

-- Reset sequences (auto-increment counters) to 1
-- This ensures new records start from ID 1

-- Reset betting-related sequences
ALTER SEQUENCE IF EXISTS betting_predictions_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS betting_signals_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS betting_orders_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS betting_fills_id_seq RESTART WITH 1;

-- Reset model and snapshot sequences
ALTER SEQUENCE IF EXISTS model_runs_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS market_snapshots_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS price_snapshots_id_seq RESTART WITH 1;

-- Reset position and market sequences
ALTER SEQUENCE IF EXISTS positions_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS markets_id_seq RESTART WITH 1;

-- Reset alert sequences
ALTER SEQUENCE IF EXISTS system_alerts_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS alert_history_id_seq RESTART WITH 1;

-- Reset comparison sequences
ALTER SEQUENCE IF EXISTS comparison_results_id_seq RESTART WITH 1;

-- Commit the transaction
COMMIT;

-- Vacuum and analyze to reclaim space and update statistics
VACUUM ANALYZE;

-- Verify cleanup
DO $$
DECLARE
    total_count INTEGER;
BEGIN
    -- Count all remaining records
    SELECT
        (SELECT COUNT(*) FROM betting_predictions) +
        (SELECT COUNT(*) FROM betting_signals) +
        (SELECT COUNT(*) FROM betting_orders) +
        (SELECT COUNT(*) FROM betting_fills) +
        (SELECT COUNT(*) FROM model_runs) +
        (SELECT COUNT(*) FROM market_snapshots) +
        (SELECT COUNT(*) FROM price_snapshots) +
        (SELECT COUNT(*) FROM positions) +
        (SELECT COUNT(*) FROM markets) +
        (SELECT COUNT(*) FROM system_alerts) +
        (SELECT COUNT(*) FROM alert_history)
    INTO total_count;

    RAISE NOTICE 'Database cleanup complete. Total remaining records: %', total_count;

    IF total_count = 0 THEN
        RAISE NOTICE 'SUCCESS: All tables are now empty!';
    ELSE
        RAISE WARNING 'WARNING: Some records may still remain. Count: %', total_count;
    END IF;
END $$;

-- Show table record counts
SELECT
    'betting_predictions' as table_name, COUNT(*) as record_count FROM betting_predictions
UNION ALL
SELECT 'betting_signals', COUNT(*) FROM betting_signals
UNION ALL
SELECT 'betting_orders', COUNT(*) FROM betting_orders
UNION ALL
SELECT 'betting_fills', COUNT(*) FROM betting_fills
UNION ALL
SELECT 'model_runs', COUNT(*) FROM model_runs
UNION ALL
SELECT 'market_snapshots', COUNT(*) FROM market_snapshots
UNION ALL
SELECT 'price_snapshots', COUNT(*) FROM price_snapshots
UNION ALL
SELECT 'positions', COUNT(*) FROM positions
UNION ALL
SELECT 'markets', COUNT(*) FROM markets
UNION ALL
SELECT 'system_alerts', COUNT(*) FROM system_alerts
UNION ALL
SELECT 'alert_history', COUNT(*) FROM alert_history
ORDER BY table_name;