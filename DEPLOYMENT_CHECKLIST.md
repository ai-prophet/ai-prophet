# Deployment Checklist for Enhanced Kalshi Sync

## Pre-Deployment Validation

### 1. Code Changes Summary
- [x] Fixed race condition in position snapshot comparison
- [x] Fixed division by zero bugs in fill price calculations
- [x] Fixed fee extraction error handling
- [x] Added pre-trade position verification from Kalshi
- [x] Added enhanced post-order polling with exponential backoff
- [x] Added real-time pending order status updates
- [x] Added balance verification after fills with auto-correction

### 2. Critical Features Added
- **Position Auto-Correction**: Positions are now immediately corrected when mismatches detected
- **Balance Verification**: 3 retries with automatic reconciliation on discrepancy
- **Emergency Stop**: Trading automatically disabled if balance discrepancy > $10
- **Real-time Polling**: Pending orders polled every sync cycle (10 minutes)

## Deployment Steps

### Step 1: Backup Current State (CRITICAL)
```bash
# 1. Backup database
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d_%H%M%S).sql

# 2. Record current Kalshi positions
python3 -c "
from services.kalshi_sync_service import *
from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter
adapter = KalshiAdapter()
print('Current Positions:', adapter.get_positions())
print('Current Balance:', adapter.get_balance())
"

# 3. Check for pending orders
psql $DATABASE_URL -c "SELECT * FROM betting_orders WHERE status='PENDING';"
```

### Step 2: Test in Dry-Run Mode First
```bash
# 1. Set dry-run mode
export BETTING_DRY_RUN=true

# 2. Run sync service once to test
python3 services/kalshi_sync_service.py --once --verbose

# 3. Check logs for errors
grep -E "ERROR|CRITICAL" logs/kalshi_sync.log
```

### Step 3: Deploy Code Changes
```bash
# 1. Commit changes
git add -A
git commit -m "Enhanced Kalshi sync with auto-correction and emergency stops"

# 2. Push to your deployment branch
git push origin anri-trading

# 3. Deploy (adjust for your deployment method)
# If using systemd:
sudo systemctl stop kalshi-sync
sudo systemctl stop ai-prophet-worker
# Update code
git pull
# Restart services
sudo systemctl start kalshi-sync
sudo systemctl start ai-prophet-worker
```

### Step 4: Monitor Initial Sync
```bash
# Watch logs in real-time
tail -f logs/kalshi_sync.log | grep -E "\[SYNC\]|\[REALTIME\]|\[ORDER_MGMT\]|\[BETTING\]"

# Monitor for critical events
watch -n 5 "psql $DATABASE_URL -c \"SELECT * FROM system_logs WHERE level IN ('CRITICAL', 'ERROR', 'EMERGENCY') ORDER BY created_at DESC LIMIT 10;\""
```

### Step 5: Validation Checks

#### A. Position Verification Working
```sql
-- Check if positions are being auto-corrected
SELECT * FROM system_logs
WHERE component = 'position_sync'
AND message LIKE 'AUTO-CORRECTED%'
ORDER BY created_at DESC;
```

#### B. Balance Verification Working
```sql
-- Check balance verifications
SELECT * FROM system_logs
WHERE component IN ('balance_verify', 'balance_reconciliation')
ORDER BY created_at DESC;
```

#### C. Real-time Polling Working
```sql
-- Check pending order updates
SELECT * FROM system_logs
WHERE message LIKE 'Real-time polling updated%'
ORDER BY created_at DESC;
```

#### D. No Position Drifts
```sql
-- Should be empty or show corrections
SELECT * FROM system_logs
WHERE message LIKE '%position drift%'
OR message LIKE '%position mismatch%'
ORDER BY created_at DESC;
```

## Post-Deployment Monitoring

### First Hour
- [ ] Check logs every 10 minutes for CRITICAL/ERROR messages
- [ ] Verify positions match between DB and Kalshi
- [ ] Confirm balance is correct after each trade
- [ ] Ensure no emergency stops triggered

### First Day
- [ ] Monitor sync service is running every 10 minutes
- [ ] Check that pending orders are being polled
- [ ] Verify no position drifts accumulating
- [ ] Confirm trades executing correctly

### Ongoing
- Set up alerts for:
  - `level = 'EMERGENCY'` - Trading has been disabled
  - `level = 'CRITICAL'` - Position/balance corrections happening
  - `component = 'emergency_stop'` - System stopped trading

## Rollback Plan

If issues occur:
```bash
# 1. Stop services immediately
sudo systemctl stop kalshi-sync
sudo systemctl stop ai-prophet-worker

# 2. Revert code
git checkout HEAD~1

# 3. Restore database if needed
psql $DATABASE_URL < backup_TIMESTAMP.sql

# 4. Restart with old code
sudo systemctl start kalshi-sync
sudo systemctl start ai-prophet-worker
```

## Configuration Recommendations

### Environment Variables to Set
```bash
# Sync interval (10 minutes = 600 seconds)
SYNC_INTERVAL_SEC=600

# Stale order threshold (2 hours)
STALE_ORDER_THRESHOLD_MINUTES=120

# Logging level
LOG_LEVEL=INFO

# Enable detailed logging initially
VERBOSE_LOGGING=true
```

### Database Indexes to Add (for performance)
```sql
-- Speed up pending order queries
CREATE INDEX idx_betting_orders_pending
ON betting_orders(instance_name, status)
WHERE status = 'PENDING';

-- Speed up system log queries
CREATE INDEX idx_system_logs_critical
ON system_logs(instance_name, level, created_at)
WHERE level IN ('CRITICAL', 'ERROR', 'EMERGENCY');

-- Speed up position snapshot queries
CREATE INDEX idx_kalshi_position_snapshots_latest
ON kalshi_position_snapshots(instance_name, ticker, snapshot_ts DESC);
```

## Success Criteria

The deployment is successful when:
1. ✅ No position mismatches for > 1 hour
2. ✅ No balance discrepancies detected
3. ✅ All pending orders updating within 10 minutes
4. ✅ No CRITICAL or ERROR logs (except auto-corrections)
5. ✅ Trades executing successfully
6. ✅ Sync service running stable for 24 hours

## Support Contacts

If emergency stop triggered or critical issues:
1. Check system_logs table for details
2. Review recent trades and positions
3. Manually verify with Kalshi dashboard
4. If needed, disable trading: `UPDATE config SET enabled=false WHERE component='betting_engine';`

---

**IMPORTANT**: This system now auto-corrects discrepancies aggressively. If you see frequent corrections, investigate the root cause rather than relying on auto-correction.