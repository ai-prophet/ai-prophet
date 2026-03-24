# Order Monitoring Frontend Demo

This document shows what the Order Monitoring panel looks like in the dashboard with example data.

## 📍 Location
Navigate to: **Dashboard** → **Order Monitoring Tab** (right panel, next to Risk & Performance)

---

## 🎨 Visual Layout

### 1. Alert Banner (Top - appears when issues detected)

```
╔═══════════════════════════════════════════════════════════════════════════════════╗
║ ⚠️  2 stale order(s) detected (>60min). Will be cancelled and reordered next     ║
║     cycle. 2 system alert(s).                                                     ║
╚═══════════════════════════════════════════════════════════════════════════════════╝
```
- 🔴 **Red background** when critical issues (stale orders or critical alerts)
- 🟡 **Yellow background** for warnings only

---

### 2. Stats Grid (4 Cards)

```
┌─────────────────────┬─────────────────────┬─────────────────────┬─────────────────────┐
│ Pending Orders      │ Stale Orders        │ System Alerts       │ Errors (24h)        │
│                     │                     │                     │                     │
│       4             │       2  🔴         │       2  🔴         │       1  🟡         │
└─────────────────────┴─────────────────────┴─────────────────────┴─────────────────────┘
```

- Each card shows a metric count
- 🟢 **Green text** when count = 0
- 🔴 **Red text** when count > 0 (for stale/alerts/errors)
- 🔵 **Blue text** for informational counts

---

### 3. Order Status Breakdown

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ Order Status                                                                        │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                     │
│  [FILLED] 2    [PENDING] 4    [CANCELLED] 2    [ERROR] 1    [DRY_RUN] 1           │
│                                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

- Color-coded status chips:
  - 🟢 **FILLED** - Green
  - 🔵 **PENDING** - Blue
  - 🟡 **CANCELLED** - Yellow
  - 🔴 **ERROR** - Red
  - ⚪ **DRY_RUN** - Gray

---

### 4. Pending Orders Table (Main Feature)

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ Pending Orders (4)                                                                  │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                     │
│ 🔴 RED BACKGROUND - STALE ORDER                                                    │
│ ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│ │ KXDEREMEROUT-26-APR01  [ 50 YES ]  🟠 WILL REORDER  @ 38¢                     │ │
│ │                                                                                 │ │
│ │ 75 minutes ago  [STALE]                                                         │ │
│ └─────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                     │
│ 🔴 RED BACKGROUND - STALE ORDER                                                    │
│ ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│ │ KXTRUMP-26APR  [ 40 NO ]  🟠 WILL REORDER  @ 61¢                              │ │
│ │                                                                                 │ │
│ │ 90 minutes ago  [STALE]                                                         │ │
│ └─────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                     │
│ ⚪ NORMAL BACKGROUND - FRESH PENDING (partially filled!)                           │
│ ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│ │ KXMARKET-26MAR25  [ 30 NO ]  @ 52¢                                             │ │
│ │                                                                                 │ │
│ │ 20 minutes ago  • Filled: 10/30 shares                                          │ │
│ └─────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                     │
│ ⚪ NORMAL BACKGROUND - FRESH PENDING                                               │
│ ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│ │ KXDHSFUND-26APR01  [ 25 YES ]  @ 45¢                                           │ │
│ │                                                                                 │ │
│ │ 15 minutes ago                                                                  │ │
│ └─────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

**Key Visual Elements:**
- **Stale Orders (>60 min):**
  - 🔴 Red background with red border
  - 🟠 Orange "WILL REORDER" chip (bold)
  - [STALE] label in gray

- **Fresh Pending Orders:**
  - ⚪ Light gray background
  - No special badges

- **Side Badges:**
  - 🟢 Green background for YES
  - 🔴 Red background for NO

- **Price:** Shows in cents (¢)

- **Age:** "X minutes ago" or "X hours ago"

---

### 5. Recent Cancellations

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ Recent Cancellations (2)                                                            │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                     │
│ • KXOLD-26MAR20: Cancelled stale order (65 minutes old)                            │
│   2 minutes ago                                                                     │
│                                                                                     │
│ • KXSTALE-26MAR18: Cancelled by system                                             │
│   35 minutes ago                                                                    │
│                                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

### 6. System Alerts (Last 24 hours)

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ System Alerts (3)                                                                   │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                     │
│ 🔴 ALERT | Position drift detected: KXMARKET-26MAR25 (DB: 30, Kalshi: 25)         │
│          | worker • 2025-03-23 14:32:15                                            │
│                                                                                     │
│ 🔴 ALERT | Cancelled 2 stale orders (>60 min pending)                              │
│          | order_management • 2025-03-23 14:35:22                                  │
│                                                                                     │
│ 🔴 ERROR | Order submission failed: Insufficient balance for KXFAILED-26MAR22      │
│          | engine • 2025-03-23 14:37:45                                            │
│                                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Auto-Refresh Behavior

- Panel refreshes **every 30 seconds** automatically
- No page reload needed
- Age timers update in real-time
- Small loading indicator when fetching

---

## 📊 Color Scheme Summary

| Element | Color | Meaning |
|---------|-------|---------|
| 🟢 Green | Good | No issues, healthy state |
| 🔵 Blue | Info | General information |
| 🟡 Yellow | Warning | Needs attention |
| 🟠 Orange | Action Required | Will be automatically handled |
| 🔴 Red | Critical | Immediate attention needed |
| ⚪ Gray | Normal | Standard state |

---

## 🎯 Real Example Scenario

**When a partial fill happens and position changes:**

### Before Rebalancing:
```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ KXMARKET-26MAR25  [ 30 NO ]  @ 52¢                                                 │
│                                                                                     │
│ 5 minutes ago  • Filled: 10/30 shares (20 unfilled still pending)                  │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### After Model Changes Target (system detects need to rebalance):
1. **Backend:** `cancel_partially_filled_orders()` cancels the 20 unfilled shares
2. **Frontend updates in 30 seconds to show:**

```
Recent Cancellations:
• KXMARKET-26MAR25: Cancelled partially filled order (10/30 filled, cancelled 20 unfilled)
  Just now

System Alerts:
🔴 INFO | Cancelled pending order for KXMARKET-26MAR25 before rebalancing
        | order_management • 2025-03-23 14:40:12
```

3. **New order placed with correct position calculation:**
```
Pending Orders:
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ KXMARKET-26MAR25  [ 70 NO ]  @ 54¢                                                 │
│                                                                                     │
│ Just now                                                                            │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

**Result:** Clean rebalancing with 10 filled + 70 new = 80 total (matching target)

---

## 🚀 How to View This

1. **Push the code** (already done - deployed on Render)
2. **Navigate to dashboard**
3. **Select your instance** (Haifeng or Jibang)
4. **Click "Order Monitoring" tab** in the right panel
5. **Watch it auto-refresh** every 30 seconds

The panel will show real-time data from your live trading system!
