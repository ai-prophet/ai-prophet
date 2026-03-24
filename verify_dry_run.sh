#!/bin/bash
# Verify DRY_RUN is active everywhere

echo "Checking Production Status..."
echo "=============================="

echo -e "\n1. Health Endpoint:"
curl -s "https://kalshi-trading-api.onrender.com/health?instance_name=Haifeng" | \
  jq '.mode' 2>/dev/null || echo "Error"

echo -e "\n2. Balance Endpoint:"
curl -s "https://kalshi-trading-api.onrender.com/kalshi/balance?instance_name=Haifeng" | \
  jq '.dry_run' 2>/dev/null || echo "Error"

echo -e "\n3. Latest Trade:"
curl -s "https://kalshi-trading-api.onrender.com/trades?limit=1" | \
  jq '.trades[0].dry_run' 2>/dev/null || echo "Error"

echo -e "\n=============================="
echo "All should show: 'dry_run' or 'true'"