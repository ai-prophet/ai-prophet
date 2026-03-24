#!/bin/bash
# Clear ALL data from ALL tables

echo "============================================================"
echo "CLEARING ALL DATABASE TABLES"
echo "============================================================"

# Load DATABASE_URL from .env if needed
if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL not set"
    exit 1
fi

# List of tables in deletion order (children first)
tables=(
    "betting_orders"
    "betting_signals"
    "betting_predictions"
    "trading_positions"
    "trading_markets"
    "model_runs"
    "system_logs"
)

for table in "${tables[@]}"; do
    count=$(psql "$DATABASE_URL" -t -c "SELECT COUNT(*) FROM $table" 2>/dev/null | xargs)
    if [ -n "$count" ] && [ "$count" -gt 0 ]; then
        psql "$DATABASE_URL" -c "DELETE FROM $table" > /dev/null 2>&1
        echo "✓ Deleted $count rows from $table"
    else
        echo "  $table: already empty"
    fi
done

echo ""
echo "============================================================"
echo "DATABASE COMPLETELY CLEARED"
echo "============================================================"
echo ""
echo "Verification:"

for table in "${tables[@]}"; do
    count=$(psql "$DATABASE_URL" -t -c "SELECT COUNT(*) FROM $table" 2>/dev/null | xargs)
    if [ "$count" -eq 0 ]; then
        echo "✓ $table: $count rows"
    else
        echo "✗ $table: $count rows (STILL HAS DATA!)"
    fi
done

echo ""
echo "✅ DONE - Fresh database ready!"
