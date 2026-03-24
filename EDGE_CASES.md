# Trading System Edge Cases & Risk Analysis

## Critical Edge Cases Identified

### 1. **Unfilled Orders Across Cycles** ⚠️ CRITICAL

**What happens:** Order placed in Cycle N doesn't fill before Cycle N+1 starts

**Current behavior:**
- Engine polls order status 5 times with 2-second intervals (10 seconds total)
- If still PENDING after polling, order is saved as "PENDING" in database
- **BUG**: Next cycle's `_live_ledger_state()` only looks at FILLED/DRY_RUN orders
  ```python
  # Line 333 in engine.py
  .filter(BettingOrder.status.in_(["FILLED", "DRY_RUN"]))
  ```
- **PROBLEM**: Pending orders are invisible to position calculation!

**Consequences:**
- Worker thinks it has 0 position when it actually has a pending 35-contract order
- Rebalancing strategy recalculates target and places DUPLICATE order
- Double-buy risk: Two 35-contract orders instead of one
- Cash is locked up but not counted, can lead to overdraft attempts

**Example scenario:**
```
Cycle 1 (00:00):
  - Target: 35 NO contracts
  - Place order for 35 NO @ 50¢ = $17.50
  - Order status: PENDING (illiquid market)

Cycle 2 (01:00):
  - _live_ledger_state() sees 0 contracts (ignores PENDING)
  - Target: Still 35 NO
  - Places ANOTHER order for 35 NO = $17.50
  - Now have 70 NO pending instead of 35!
```

**Fix needed:**
1. Include PENDING orders in position calculation:
   ```python
   .filter(BettingOrder.status.in_(["FILLED", "PENDING", "DRY_RUN"]))
   ```
2. OR cancel/replace stale PENDING orders before new cycle
3. OR skip markets with PENDING orders

---

### 2. **Partial Fills** ⚠️ HIGH

**What happens:** Order for 35 contracts only fills 20

**Current behavior:**
- Adapter returns `filled_shares=20`
- Engine saves this to database
- `_live_ledger_state()` correctly uses `filled_shares` field

**Problem:**
- Remaining 15 contracts still on order book as PENDING
- Not counted in position (see Edge Case #1)
- Can lead to double-ordering the remainder

**Example:**
```
Order: BUY 35 NO @ 50¢
Fill:  20 contracts filled, 15 pending

Cycle N:   Position = 20 NO (correct)
Cycle N+1: Position = 20 NO (but 15 pending ignored)
           Target still wants 35 total
           Places order for 15 more
           Now has: 20 filled + 15 pending + 15 pending = 50 total!
```

**Fix needed:**
- Track partial fills separately
- Cancel remainder of partially filled orders before rebalancing
- OR accept partial fills and adjust target

---

### 3. **Order Rejection After Submission** ⚠️ MEDIUM

**What happens:** Kalshi accepts order (returns 201) but later rejects it

**Current behavior:**
- Order saved as "PENDING" or initial status
- Never updated if rejection happens async
- Position calculation includes it as if it will fill

**Scenarios:**
- Insufficient funds (race condition with other orders)
- Market closed/paused after submission
- Order violates position limits
- Compliance rejection

**Fix needed:**
- Periodic reconciliation with Kalshi positions
- Webhook/polling for order status changes
- Explicit rejection handling

---

### 4. **Market Price Changes During Polling** ⚠️ MEDIUM

**What happens:** YES ask moves from 50¢ to 60¢ while order is pending

**Current behavior:**
- Order placed at 50¢ (won't fill if market moved to 60¢)
- Strategy thinks it's buying at 50¢
- Position never fills, stuck as PENDING forever

**Problem:**
- Rebalancing assumes fills happen at limit price
- If market moves away, order becomes stale
- Next cycle ignores PENDING order (Edge Case #1) and tries again

**Fix needed:**
- Use market orders (immediate fill, worse price) OR
- Cancel and replace stale limit orders OR
- Widen limit price buffer (e.g., buy at ask + 2¢)

---

### 5. **Cash Balance Staleness** ⚠️ HIGH

**What happens:** Multiple markets processed in same cycle, cash decreases after each

**Current behavior:**
- ✅ GOOD: `_live_ledger_state()` fetches live balance from Kalshi for each market
- ✅ GOOD: Cash constraint checks prevent overdraft (lines 468-498)

**Potential problem:**
- If Kalshi API returns stale balance (cache), could place overdraft order
- Multiple workers (Haifeng + Jibang) share same account → race condition

**Current protection:**
- Each order checks live cash immediately before submission
- Kalshi should reject overdraft orders

**Fix needed:**
- Sequential order processing (already done)
- Add pessimistic locking or reserve cash for pending orders

---

### 6. **Market Closure Mid-Cycle** ⚠️ MEDIUM

**What happens:** Market closes/settles while order is pending

**Scenarios:**
- Event resolves early (e.g., news breaks)
- Market suspended due to investigation
- Settlement happens between order submission and fill

**Current behavior:**
- Order likely gets rejected by Kalshi
- Saved as ERROR or PENDING in database
- Position never established

**Fix needed:**
- Check market status before each order
- Handle "CLOSED" markets gracefully
- Auto-settle positions on closed markets (worker already has this at line 612-695)

---

### 7. **Duplicate Order Prevention** ⚠️ LOW

**What happens:** Cycle runs twice due to restart/crash

**Current protection:**
- Each order has unique UUID
- Kalshi might dedupe by client_order_id
- Database has unique constraint on order_id

**Risk:**
- If worker crashes and restarts mid-cycle, might re-process same market
- Could place duplicate order if not idempotent

**Fix needed:**
- Idempotency key per cycle+market combination
- OR check database for recent orders before placing

---

### 8. **Position Sync Drift** ⚠️ CRITICAL

**What happens:** Database position doesn't match Kalshi reality

**Causes:**
- PENDING orders not counted (Edge Case #1)
- Partial fills (Edge Case #2)
- Orders rejected after save
- Manual trades outside system
- Kalshi bugs/delays

**Current mitigation:**
- Worker has position sync from Kalshi (checks fills)
- But only for FILLED orders

**Problem:**
- If DB says "0 contracts" but Kalshi says "35 contracts", rebalancing breaks
- Strategy makes wrong decisions

**Fix needed:**
- Periodic reconciliation job
- Compare DB ledger vs Kalshi positions
- Alert on discrepancies > threshold
- Manual sync endpoint

---

### 9. **Network Failures** ⚠️ MEDIUM

**What happens:** API call to Kalshi times out or fails

**Scenarios:**
- Order submission fails (exception caught, saved as ERROR)
- Order status polling fails (returns None, stops polling)
- Balance check fails (returns Decimal("0"), skips all orders)

**Current handling:**
- Try/catch blocks around API calls
- Errors logged and saved to database
- Worker continues to next market

**Problem:**
- If balance check fails, cash = $0, all orders skipped
- If position check fails, assumes 0 contracts, places wrong order

**Fix needed:**
- Retry logic with exponential backoff
- Fallback to last known good balance
- Alert on consecutive failures

---

### 10. **Race Conditions Between Instances** ⚠️ LOW

**What happens:** Haifeng and Jibang both try to trade same market at same time

**Current setup:**
- Haifeng: fetcher, polls all markets
- Jibang: mirror, only trades markets Haifeng trades
- Different Kalshi accounts

**Risk:**
- If they shared an account, could overdraft
- Currently isolated, so low risk

**Note:** Current architecture is safe

---

### 11. **Clock Skew / Timing Issues** ⚠️ LOW

**What happens:** System clock differs from Kalshi server time

**Impact:**
- API signatures include timestamp (must be within ~5min of server)
- Order replay assumes chronological order by created_at

**Current mitigation:**
- Uses system time for signatures
- Kalshi validates timestamp

**Risk:**
- If clock drifts too far, all API calls fail (401 errors)

**Fix needed:**
- NTP sync on workers
- Alert on clock drift

---

## Recommended Immediate Fixes

### Priority 1: Pending Order Handling
```python
# In _live_ledger_state(), line 333:
# BEFORE:
.filter(BettingOrder.status.in_(["FILLED", "DRY_RUN"]))

# AFTER:
.filter(BettingOrder.status.in_(["FILLED", "PENDING", "DRY_RUN"]))
```

**Rationale:** Prevents double-ordering on unfilled orders

### Priority 2: Stale Order Cancellation
```python
# At start of each cycle, cancel orders older than 1 hour
def cancel_stale_orders():
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    stale_orders = session.query(BettingOrder).filter(
        BettingOrder.status == "PENDING",
        BettingOrder.created_at < cutoff,
    ).all()

    for order in stale_orders:
        adapter.cancel_order(order.exchange_order_id)
        order.status = "CANCELLED"
```

### Priority 3: Position Reconciliation
```python
# Every cycle, verify DB positions match Kalshi
def reconcile_positions():
    db_positions = get_db_positions()
    kalshi_positions = adapter.get_all_positions()

    for ticker in set(db_positions.keys()) | set(kalshi_positions.keys()):
        db_qty = db_positions.get(ticker, 0)
        kalshi_qty = kalshi_positions.get(ticker, 0)

        if abs(db_qty - kalshi_qty) > 5:  # Allow 5 contract tolerance
            logger.error(f"DRIFT: {ticker} DB={db_qty} Kalshi={kalshi_qty}")
            # Alert or auto-sync
```

### Priority 4: Order Idempotency
```python
# Before placing order, check if already placed this cycle
cycle_key = f"{cycle_start_time}:{ticker}:{side}"
existing = session.query(BettingOrder).filter(
    BettingOrder.metadata_json.contains(f'"cycle_key": "{cycle_key}"'),
).first()

if existing:
    logger.info(f"Skipping {ticker}: already ordered this cycle")
    return
```

---

## Testing Recommendations

1. **Simulate illiquid markets**: Place orders that won't fill, verify next cycle behavior
2. **Partial fill test**: Manually fill only part of an order, check position calc
3. **Network failure test**: Mock API failures, verify error handling
4. **Clock skew test**: Set system clock wrong, verify auth fails gracefully
5. **Concurrent execution**: Run two cycles simultaneously, check for races
6. **Market closure test**: Trade on market that closes mid-cycle

---

## Monitoring Needed

1. **Pending order age** - Alert if orders pending > 5 minutes
2. **Position drift** - Alert if DB != Kalshi by > 10 contracts
3. **Cash balance changes** - Alert on unexpected drops
4. **Order rejection rate** - Alert if > 10% rejections
5. **API latency** - Alert if Kalshi responses > 5 seconds
6. **Fill rate** - Alert if < 80% of orders fill within 1 minute

---

## Current State Assessment

**What's working well:**
✅ Polling for pending orders (10 sec max)
✅ Live cash balance checks before each order
✅ NET position management (sell before buy)
✅ Exception handling on API failures
✅ Order replay for position calculation
✅ Automatic position settlement on market resolution

**What's broken:**
❌ PENDING orders ignored in position calc → **double-ordering risk**
❌ No stale order cleanup → orders sit forever
❌ No position reconciliation → drift accumulates
❌ No idempotency → crash recovery risky

**Risk level:** **MEDIUM-HIGH** for production trading
- Immediate risk: Double-ordering on illiquid markets
- Long-term risk: Position drift leading to bad decisions
