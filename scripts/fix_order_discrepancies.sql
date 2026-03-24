-- Fix Order Fill Discrepancies
-- This script addresses the critical issue where CANCELLED orders may have incorrect filled_shares
-- causing position calculation errors

-- 1. First, let's identify the problematic orders
SELECT
    instance_name,
    order_id,
    ticker,
    side,
    action,
    status,
    count,
    filled_shares,
    fill_price,
    created_at
FROM betting_orders
WHERE status = 'CANCELLED'
    AND filled_shares > 0
    AND instance_name IN ('Haifeng', 'Jibang')
ORDER BY created_at DESC;

-- 2. The specific problematic order we identified
-- Order e2da9afd-8dcc-44af-ba7b-90c915345397 shows 22 filled but likely has 0
-- This is causing Haifeng to show wrong position count

-- TEMPORARY FIX: Set the known incorrect order to 0 filled shares
-- This should be verified against Kalshi API first!
BEGIN;

-- Fix the specific order we know is wrong
UPDATE betting_orders
SET
    filled_shares = 0,
    fill_price = 0
WHERE order_id = 'e2da9afd-8dcc-44af-ba7b-90c915345397'
    AND status = 'CANCELLED';

-- Show what we're updating
SELECT
    'AFTER UPDATE' as state,
    instance_name,
    order_id,
    ticker,
    status,
    count,
    filled_shares
FROM betting_orders
WHERE order_id = 'e2da9afd-8dcc-44af-ba7b-90c915345397';

-- Recalculate positions for KXDHSFUND-26APR01
-- Expected: Haifeng should have 83 NO (70 + 13), Jibang should have 2 NO
WITH order_summary AS (
    SELECT
        instance_name,
        ticker,
        side,
        SUM(CASE
            WHEN action = 'BUY' AND status IN ('FILLED', 'DRY_RUN') THEN filled_shares
            WHEN action = 'SELL' AND status IN ('FILLED', 'DRY_RUN') THEN -filled_shares
            ELSE 0
        END) as net_position
    FROM betting_orders
    WHERE ticker = 'KXDHSFUND-26APR01'
        AND instance_name IN ('Haifeng', 'Jibang')
    GROUP BY instance_name, ticker, side
)
SELECT
    instance_name,
    ticker,
    side,
    net_position,
    'Expected: Haifeng=83 NO, Jibang=2 NO' as note
FROM order_summary
WHERE net_position != 0
ORDER BY instance_name;

-- COMMIT; -- Uncomment to apply the fix
ROLLBACK; -- Remove this and uncomment COMMIT when ready to apply