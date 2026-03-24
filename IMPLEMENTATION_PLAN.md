# Comprehensive Dashboard Improvements - Implementation Plan

## Context
User wants complete position tracking showing:
- **Target shares** (from entry edge, e.g., 29pp edge = 29 shares target)
- **Filled shares** (e.g., 17 shares filled)
- **Pending shares** (e.g., 12 shares in pending orders)
- **Entry edge** (edge when position opened, e.g., -70pp for NO position)
- **P&L calculated as if all target shares filled** at entry price

## Changes Required

### 1. API Changes (`services/api/main.py`)

#### A. Add pending orders to markets endpoint
```python
# In GET /markets, add pending orders lookup
pending_orders_by_ticker = {}
pending_orders = session.query(BettingOrder).filter(
    BettingOrder.instance_name == resolved_instance,
    BettingOrder.status == "PENDING"
).all()
for order in pending_orders:
    if order.ticker not in pending_orders_by_ticker:
        pending_orders_by_ticker[order.ticker] = []
    pending_orders_by_ticker[order.ticker].append({
        "order_id": order.order_id,
        "side": order.side,
        "count": order.count,
        "filled_shares": order.filled_shares or 0,
        "price_cents": order.price_cents,
        "created_at": order.created_at.isoformat(),
    })

# Add to each market result:
"pending_orders": pending_orders_by_ticker.get(row.ticker, [])
```

#### B. Add pending orders to positions endpoint
Similar addition to GET /positions

### 2. Frontend Data Model (`services/dashboard/src/lib/api.ts`)

#### A. Update UnifiedMarketRow interface
```typescript
export interface UnifiedMarketRow {
  // ... existing fields ...
  pending_orders?: PendingOrder[];
  target_shares?: number;  // Calculated from entry edge
  filled_shares?: number;   // Sum of filled trades
  pending_shares?: number;  // Sum of pending orders
  entry_edge?: number;      // Edge when first trade placed
}

export interface PendingOrder {
  order_id: string;
  side: string;
  count: number;
  filled_shares: number;
  price_cents: number;
  created_at: string;
}
```

#### B. Update buildUnifiedMarketRows function
```typescript
// Calculate target shares from first trade's edge
const firstTrade = sortedTrades[0];
let target_shares: number | null = null;
let entry_edge: number | null = null;

if (firstTrade?.prediction) {
  const pred = firstTrade.prediction;
  entry_edge = pred.p_yes - pred.yes_ask;
  // Target shares = abs(edge) * 100, rounded
  target_shares = Math.round(Math.abs(entry_edge) * 100);
}

// Calculate filled shares
const filled_shares = sortedTrades.reduce((sum, t) =>
  sum + (t.filled_shares || t.count), 0
);

// Get pending orders for this ticker
const pendingOrders = mkt.pending_orders || [];
const pending_shares = pendingOrders.reduce((sum, o) =>
  sum + (o.count - o.filled_shares), 0
);

// Add to row:
target_shares,
filled_shares,
pending_shares,
entry_edge,
```

### 3. Position Display (`services/dashboard/src/components/UnifiedMarketTable.tsx`)

#### Current (line 842):
```typescript
<span className="font-mono text-txt-primary">{pos.quantity}</span>
```

#### New:
```typescript
{pos ? (
  <div className="flex flex-col items-center gap-0.5">
    <div className="flex items-center gap-1">
      <span className="font-mono text-txt-primary">
        {row.filled_shares || pos.quantity}
      </span>
      {row.pending_shares > 0 && (
        <span className="text-txt-muted text-[10px]">
          +{row.pending_shares} pending
        </span>
      )}
    </div>
    {row.target_shares && row.target_shares > (row.filled_shares || 0) && (
      <span className="text-txt-muted text-[9px]">
        of {row.target_shares} target
      </span>
    )}
  </div>
) : (
  <span className="text-txt-muted">--</span>
)}
```

### 4. Edge Display Fix

#### Current (line 509 in api.ts):
```typescript
const edge = predicted != null && mkt.yes_ask != null ? predicted - mkt.yes_ask : null;
```

#### Already Fixed (shows entry edge):
```typescript
let edge: number | null = null;
if (sortedTrades.length > 0 && sortedTrades[0].prediction) {
  const pred = sortedTrades[0].prediction;
  edge = pred.p_yes - pred.yes_ask;  // Entry edge, not current
}
```

✅ **Already implemented** - shows entry edge (-70pp) not current market edge

### 5. P&L Calculation Fix

#### Current (in computePortfolioMetrics):
```typescript
// P&L calculated on actual filled shares
```

#### New: Calculate as if target shares filled
```typescript
// In liveNetPnl function
export function liveNetPnl(row: UnifiedMarketRow): number | null {
  const pos = row.position;
  if (!pos) return null;

  // Use target shares if available, otherwise use filled shares
  const shares = row.target_shares || pos.quantity;
  const entry_price = pos.avg_price;

  // Current market price
  const side = pos.contract.toLowerCase();
  const current_price = side === "yes" ? row.yes_bid : row.no_bid;
  if (current_price == null) return null;

  // P&L = (current_price - entry_price) * shares
  return (current_price - entry_price) * shares + pos.realized_pnl;
}
```

### 6. Order Monitoring Changes

#### A. Remove "Large edge detected" alerts
```typescript
// In AlertsPanel or SystemHealth component
const filteredAlerts = alerts.filter(a =>
  !a.message.includes("Large edge detected")
);
```

#### B. Minimal style (match Alerts panel)
- Remove colored backgrounds
- Use simple list with subtle borders
- Smaller font sizes
- Less visual weight

#### C. Click to open market activity
```typescript
<a
  href="#"
  onClick={(e) => {
    e.preventDefault();
    // Scroll to market and open activity tab
    focusMarket(order.ticker);
    setMarketViewTab("activity");
  }}
  className="hover:bg-bg-secondary transition-colors"
>
  {order.ticker}
</a>
```

### 7. Order Monitoring - Check Pending Before Next Cycle

In worker main.py, before running predictions:
```python
# Check pending orders and update status
from order_management import update_pending_order_status

updated_orders = update_pending_order_status(db_engine, adapter, INSTANCE_NAME)
if updated_orders > 0:
    logger.info("[CYCLE] Updated %d pending orders", updated_orders)
```

## Implementation Order

1. ✅ Entry edge display (already done)
2. API: Add pending_orders to markets endpoint
3. Frontend: Add interfaces and calculate target/filled/pending shares
4. Frontend: Update position display to show breakdown
5. Frontend: Fix P&L to use target shares
6. Frontend: Filter out edge alerts
7. Frontend: Minimal order monitoring style
8. Frontend: Click navigation to market activity
9. Worker: Check pending orders before cycle
10. Deploy all changes

## Testing Checklist

- [ ] DeRemer position shows: "16 filled, 12 pending of 29 target"
- [ ] Edge shows -70pp (entry) not current market edge
- [ ] P&L calculated as if 29 shares filled at entry price
- [ ] No "Large edge detected" alerts visible
- [ ] Order monitoring has minimal clean style
- [ ] Clicking pending order opens market activity tab
- [ ] Worker checks pending orders before running cycle
