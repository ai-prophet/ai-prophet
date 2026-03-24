#!/bin/bash
# Insert demo orders directly into database via psql

if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL environment variable not set"
    exit 1
fi

echo "Inserting demo orders into database..."

# Extract connection details from DATABASE_URL
# Format: postgresql://user:password@host:port/database
DB_URL=$DATABASE_URL

# Generate UUIDs for orders
ORDER_ID_1=$(uuidgen)
ORDER_ID_2=$(uuidgen)
ORDER_ID_3=$(uuidgen)
ORDER_ID_4=$(uuidgen)
EXCHANGE_ID_1="kalshi_$(openssl rand -hex 8)"
EXCHANGE_ID_2="kalshi_$(openssl rand -hex 8)"
EXCHANGE_ID_3="kalshi_$(openssl rand -hex 8)"
EXCHANGE_ID_4="kalshi_$(openssl rand -hex 8)"

# Current timestamp
NOW=$(date -u +"%Y-%m-%d %H:%M:%S")

# Calculate timestamps for different ages
FRESH_TIME=$(date -u -v-15M +"%Y-%m-%d %H:%M:%S" 2>/dev/null || date -u -d '15 minutes ago' +"%Y-%m-%d %H:%M:%S")
PARTIAL_TIME=$(date -u -v-20M +"%Y-%m-%d %H:%M:%S" 2>/dev/null || date -u -d '20 minutes ago' +"%Y-%m-%d %H:%M:%S")
STALE_TIME_1=$(date -u -v-75M +"%Y-%m-%d %H:%M:%S" 2>/dev/null || date -u -d '75 minutes ago' +"%Y-%m-%d %H:%M:%S")
STALE_TIME_2=$(date -u -v-90M +"%Y-%m-%d %H:%M:%S" 2>/dev/null || date -u -d '90 minutes ago' +"%Y-%m-%d %H:%M:%S")
ALERT_TIME=$(date -u -v-25M +"%Y-%m-%d %H:%M:%S" 2>/dev/null || date -u -d '25 minutes ago' +"%Y-%m-%d %H:%M:%S")

# Use Haifeng instance (change if needed)
INSTANCE="Haifeng"

psql "$DB_URL" <<EOF

-- Clear old demo orders (optional - comment out if you want to keep existing data)
-- DELETE FROM betting_orders WHERE ticker LIKE 'KXDEMO-%';
-- DELETE FROM system_logs WHERE message LIKE '%DEMO%';

-- First create signals (required by foreign key constraint)
INSERT INTO betting_signals (id, market_id, side, shares, price, reason, created_at, instance_name)
VALUES
(999901, 'kalshi:KXDEMO-FRESH-01', 'yes', 0.25, 0.45, 'DEMO: Fresh pending order', '$FRESH_TIME', '$INSTANCE'),
(999902, 'kalshi:KXDEMO-PARTIAL-02', 'no', 0.30, 0.52, 'DEMO: Partially filled order', '$PARTIAL_TIME', '$INSTANCE'),
(999903, 'kalshi:KXDEMO-STALE-03', 'yes', 0.50, 0.38, 'DEMO: Stale order 1', '$STALE_TIME_1', '$INSTANCE'),
(999904, 'kalshi:KXDEMO-STALE-04', 'no', 0.40, 0.61, 'DEMO: Stale order 2', '$STALE_TIME_2', '$INSTANCE');

-- Insert fresh pending order (15 minutes old)
INSERT INTO betting_orders
(signal_id, order_id, instance_name, ticker, side, action, count, price_cents, status, filled_shares, fill_price, exchange_order_id, dry_run, created_at)
VALUES
(999901, '$ORDER_ID_1', '$INSTANCE', 'KXDEMO-FRESH-01', 'yes', 'BUY', 25, 45, 'PENDING', 0.0, 0.0, '$EXCHANGE_ID_1', false, '$FRESH_TIME');

-- Insert partially filled order (20 minutes old, 10/30 filled)
INSERT INTO betting_orders
(signal_id, order_id, instance_name, ticker, side, action, count, price_cents, status, filled_shares, fill_price, exchange_order_id, dry_run, created_at)
VALUES
(999902, '$ORDER_ID_2', '$INSTANCE', 'KXDEMO-PARTIAL-02', 'no', 'BUY', 30, 52, 'PENDING', 10.0, 0.52, '$EXCHANGE_ID_2', false, '$PARTIAL_TIME');

-- Insert STALE pending order (75 minutes old - will show WILL REORDER)
INSERT INTO betting_orders
(signal_id, order_id, instance_name, ticker, side, action, count, price_cents, status, filled_shares, fill_price, exchange_order_id, dry_run, created_at)
VALUES
(999903, '$ORDER_ID_3', '$INSTANCE', 'KXDEMO-STALE-03', 'yes', 'BUY', 50, 38, 'PENDING', 0.0, 0.0, '$EXCHANGE_ID_3', false, '$STALE_TIME_1');

-- Insert another STALE order (90 minutes old)
INSERT INTO betting_orders
(signal_id, order_id, instance_name, ticker, side, action, count, price_cents, status, filled_shares, fill_price, exchange_order_id, dry_run, created_at)
VALUES
(999904, '$ORDER_ID_4', '$INSTANCE', 'KXDEMO-STALE-04', 'no', 'BUY', 40, 61, 'PENDING', 0.0, 0.0, '$EXCHANGE_ID_4', false, '$STALE_TIME_2');

-- Insert demo system alert
INSERT INTO system_logs (level, message, component, instance_name, created_at)
VALUES
('ALERT', 'DEMO: Position drift detected for KXDEMO-PARTIAL-02 (DB: 30, Kalshi: 25)', 'worker', '$INSTANCE', '$ALERT_TIME');

-- Insert demo error
INSERT INTO system_logs (level, message, component, instance_name, created_at)
VALUES
('ERROR', 'DEMO: Order placement failed - this is just a test', 'engine', '$INSTANCE', '$NOW');

EOF

echo ""
echo "✅ Demo orders inserted successfully!"
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "DEMO ORDERS CREATED"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "📊 What was created:"
echo "  • 1 Fresh pending order (15 min old)"
echo "  • 1 Partially filled order (10/30 filled, 20 min old)"
echo "  • 2 STALE orders (75 and 90 min old) 🔴"
echo "  • 2 System logs (1 ALERT, 1 ERROR)"
echo ""
echo "🎯 To view in dashboard:"
echo "  1. Go to your dashboard URL"
echo "  2. Select instance: '$INSTANCE'"
echo "  3. Click 'Order Monitoring' tab (right panel)"
echo ""
echo "You should see:"
echo "  🔴 Red alert banner: '2 stale order(s) detected'"
echo "  📊 Stats: Pending: 4, Stale: 2, Alerts: 1, Errors: 1"
echo "  🟠 Two orders with 'WILL REORDER' chips (red background)"
echo "  ⚪ Fresh order with normal background"
echo "  📝 Partial fill: 'KXDEMO-PARTIAL-02: 10/30 shares filled'"
echo ""
echo "Panel auto-refreshes every 30 seconds!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
