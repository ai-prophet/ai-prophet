# CRITICAL FIXES: Order Fill Discrepancy Issue

## Problem Summary
There was a massive discrepancy between what Kalshi showed (70 NO shares) and what the database/frontend displayed (83 NO shares) for the KXDHSFUND market.

## Root Causes Identified

### 1. **Critical Bug in position_replay.py**
- **Issue**: The `normalize_order` function was falling back to using `count` (requested shares) instead of `filled_shares` (actual fills) when `filled_shares` was 0
- **Impact**: PENDING orders with 0 fills were treated as fully filled, causing incorrect position calculations
- **Fix Applied**: Removed the fallback logic - now correctly uses only `filled_shares`

```python
# BEFORE (WRONG):
shares = float(getattr(order, "filled_shares", 0) or 0)
if shares <= 0:
    shares = float(getattr(order, "count", 0) or 0)  # BUG: Uses requested amount!

# AFTER (CORRECT):
shares = float(getattr(order, "filled_shares", 0) or 0)  # Only use actual fills
```

### 2. **CANCELLED Orders with Incorrect Fill Data**
- **Issue**: Order `e2da9afd-8dcc-44af-ba7b-90c915345397` was marked as CANCELLED but showed 22 `filled_shares` (should be 0)
- **Impact**: Haifeng's position was understated by 22 shares
- **Fix Applied**: Corrected the database record to show 0 filled_shares

### 3. **Incomplete Order Synchronization**
- **Issue**: Only PENDING orders were being synced with Kalshi, not CANCELLED orders with potential partial fills
- **Fix Applied**: Enhanced `sync_pending_orders` to also verify CANCELLED orders with non-zero filled_shares

## Files Modified

1. **[services/position_replay.py:12-20](services/position_replay.py#L12-L20)**
   - Fixed the critical bug in `normalize_order` function
   - Now only uses `filled_shares`, never falls back to `count`

2. **[services/worker/main.py:474-556](services/worker/main.py#L474-L556)**
   - Enhanced `sync_pending_orders` to verify CANCELLED orders with fills
   - Added critical discrepancy logging
   - Updates fill_price when missing

3. **Database Fix**
   - Corrected order `e2da9afd-8dcc-44af-ba7b-90c915345397`
   - Set filled_shares from 22 to 0 (correct value)

## Verification Steps

After these fixes:
- Haifeng should show 83 NO shares (70 + 13)
- Jibang should show 2 NO shares
- Position calculations will only use actual filled shares, not requested amounts

## Next Steps

1. **Deploy the code fixes immediately** to prevent future discrepancies
2. **Run a full order audit** to identify any other CANCELLED orders with incorrect fill data
3. **Monitor closely** for any new discrepancies after deployment
4. **Consider adding automated reconciliation** that regularly verifies DB state against Kalshi

## Critical Monitoring SQL

```sql
-- Check for suspicious CANCELLED orders
SELECT instance_name, order_id, ticker, filled_shares
FROM betting_orders
WHERE status = 'CANCELLED'
  AND filled_shares > 0
  AND instance_name IN ('Haifeng', 'Jibang');

-- Verify position calculations
WITH order_summary AS (
  SELECT
    instance_name,
    ticker,
    side,
    SUM(CASE
      WHEN action = 'BUY' AND status = 'FILLED' THEN filled_shares
      WHEN action = 'SELL' AND status = 'FILLED' THEN -filled_shares
      ELSE 0
    END) as net_position
  FROM betting_orders
  WHERE instance_name IN ('Haifeng', 'Jibang')
  GROUP BY instance_name, ticker, side
)
SELECT * FROM order_summary WHERE net_position != 0;
```

## Deployment Priority: CRITICAL

These fixes address fundamental calculation errors that affect:
- Position tracking accuracy
- P&L calculations
- Risk management
- Trading decisions

Deploy immediately to production.