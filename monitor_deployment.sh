#!/bin/bash
# Real-time monitoring script for Kalshi sync deployment

echo "======================================"
echo "KALSHI SYNC MONITORING DASHBOARD"
echo "Started at: $(date)"
echo "======================================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to check for critical errors
check_critical_errors() {
    echo -e "\n${YELLOW}=== Checking for Critical Errors ===${NC}"

    # Check system logs for emergencies
    if command -v psql &> /dev/null; then
        CRITICAL_COUNT=$(psql $DATABASE_URL -t -c "
            SELECT COUNT(*) FROM system_logs
            WHERE level IN ('CRITICAL', 'EMERGENCY')
            AND created_at > NOW() - INTERVAL '1 hour';" 2>/dev/null | xargs)

        if [ "$CRITICAL_COUNT" -gt "0" ]; then
            echo -e "${RED}⚠️  FOUND $CRITICAL_COUNT CRITICAL EVENTS!${NC}"
            psql $DATABASE_URL -c "
                SELECT level, component, message, created_at
                FROM system_logs
                WHERE level IN ('CRITICAL', 'EMERGENCY')
                AND created_at > NOW() - INTERVAL '1 hour'
                ORDER BY created_at DESC LIMIT 5;"
        else
            echo -e "${GREEN}✓ No critical errors in last hour${NC}"
        fi
    fi

    # Check log files for errors
    if [ -f logs/kalshi_sync.log ]; then
        RECENT_ERRORS=$(tail -100 logs/kalshi_sync.log | grep -c "ERROR\|CRITICAL\|EMERGENCY")
        if [ "$RECENT_ERRORS" -gt "0" ]; then
            echo -e "${YELLOW}Found $RECENT_ERRORS error lines in recent logs${NC}"
            tail -100 logs/kalshi_sync.log | grep "ERROR\|CRITICAL\|EMERGENCY" | tail -5
        fi
    fi
}

# Function to check sync status
check_sync_status() {
    echo -e "\n${YELLOW}=== Sync Status ===${NC}"

    if command -v psql &> /dev/null; then
        LAST_SYNC=$(psql $DATABASE_URL -t -c "
            SELECT MAX(created_at) FROM system_logs
            WHERE component = 'kalshi_sync';" 2>/dev/null)

        if [ ! -z "$LAST_SYNC" ]; then
            echo "Last sync: $LAST_SYNC"
        fi

        # Check pending orders
        PENDING_ORDERS=$(psql $DATABASE_URL -t -c "
            SELECT COUNT(*) FROM betting_orders
            WHERE status = 'PENDING';" 2>/dev/null | xargs)

        echo "Pending orders: $PENDING_ORDERS"
    fi
}

# Function to check position consistency
check_positions() {
    echo -e "\n${YELLOW}=== Position Consistency ===${NC}"

    if command -v psql &> /dev/null; then
        # Check for auto-corrections
        AUTO_CORRECT_COUNT=$(psql $DATABASE_URL -t -c "
            SELECT COUNT(*) FROM system_logs
            WHERE message LIKE '%AUTO-CORRECT%'
            AND created_at > NOW() - INTERVAL '1 hour';" 2>/dev/null | xargs)

        if [ "$AUTO_CORRECT_COUNT" -gt "0" ]; then
            echo -e "${YELLOW}⚠️  $AUTO_CORRECT_COUNT position corrections in last hour${NC}"
            psql $DATABASE_URL -c "
                SELECT message, created_at
                FROM system_logs
                WHERE message LIKE '%AUTO-CORRECT%'
                AND created_at > NOW() - INTERVAL '1 hour'
                ORDER BY created_at DESC LIMIT 3;"
        else
            echo -e "${GREEN}✓ No position corrections needed${NC}"
        fi
    fi
}

# Function to check if trading is enabled
check_trading_status() {
    echo -e "\n${YELLOW}=== Trading Status ===${NC}"

    # Check for emergency stops
    if command -v psql &> /dev/null; then
        EMERGENCY_STOPS=$(psql $DATABASE_URL -t -c "
            SELECT COUNT(*) FROM system_logs
            WHERE component = 'emergency_stop'
            AND created_at > NOW() - INTERVAL '24 hours';" 2>/dev/null | xargs)

        if [ "$EMERGENCY_STOPS" -gt "0" ]; then
            echo -e "${RED}🚨 EMERGENCY STOP TRIGGERED!${NC}"
            psql $DATABASE_URL -c "
                SELECT message, created_at
                FROM system_logs
                WHERE component = 'emergency_stop'
                ORDER BY created_at DESC LIMIT 1;"
        else
            echo -e "${GREEN}✓ Trading enabled (no emergency stops)${NC}"
        fi

        # Check last trade
        LAST_TRADE=$(psql $DATABASE_URL -t -c "
            SELECT MAX(created_at) FROM betting_orders;" 2>/dev/null)

        if [ ! -z "$LAST_TRADE" ]; then
            echo "Last trade: $LAST_TRADE"
        fi
    fi
}

# Main monitoring loop
monitor_loop() {
    while true; do
        clear
        echo "======================================"
        echo "KALSHI SYNC MONITORING - $(date)"
        echo "======================================"

        check_critical_errors
        check_sync_status
        check_positions
        check_trading_status

        echo -e "\n${YELLOW}=== Live Log Stream ===${NC}"
        if [ -f logs/kalshi_sync.log ]; then
            tail -10 logs/kalshi_sync.log | grep -E "\[SYNC\]|\[REALTIME\]|\[BETTING\]|ERROR|CRITICAL" || echo "No recent relevant logs"
        fi

        echo -e "\n${YELLOW}Refreshing in 30 seconds... (Ctrl+C to exit)${NC}"
        sleep 30
    done
}

# Check if we should run once or loop
if [ "$1" == "--once" ]; then
    check_critical_errors
    check_sync_status
    check_positions
    check_trading_status
else
    echo "Starting continuous monitoring (Ctrl+C to exit)..."
    echo "Tip: Run with --once for single check"
    sleep 2
    monitor_loop
fi