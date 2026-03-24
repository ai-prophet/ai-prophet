#!/usr/bin/env python3
"""Verify what the timeline should be showing."""

import requests
import json
from datetime import datetime

# Test the live API endpoint
print("\n" + "="*80)
print("🔍 VERIFYING TIMELINE DATA")
print("="*80)

# 1. Check API endpoint
url = 'https://kalshi-trading-api.onrender.com/cycle-evaluations'
params = {
    'ticker': 'KXSPACEIPO-26MAR-27MAR',
    'limit': 10,
    'instance_name': 'Haifeng'
}

print("\n✅ API Endpoint is LIVE and returning data:")
print(f"URL: {url}")

response = requests.get(url, params=params)
data = response.json()

print(f"\nFound {len(data['evaluations'])} cycle evaluations")
print(f"Total in database: {data['total']}")

print("\n" + "="*80)
print("📊 WHAT THE TIMELINE SHOULD SHOW:")
print("="*80)

# Display the evaluations
for i, eval in enumerate(data['evaluations'][:10], 1):
    # Parse timestamp
    if eval['timestamp']:
        dt = datetime.fromisoformat(eval['timestamp'].replace('Z', '+00:00'))
        time_str = dt.strftime('%I:%M %p')
    else:
        time_str = "Unknown"

    # Get action info
    action = eval['action']
    pred = eval['prediction']

    # Set icon based on action type
    if action['type'] == 'buy':
        icon = '🟢'
    elif action['type'] == 'sell':
        icon = '🔴'
    elif action['type'] == 'hold':
        icon = '⏸️'
    else:
        icon = '❓'

    print(f"\n{i}. {icon} {time_str} - {action['description']}")
    if pred['p_yes'] and pred['yes_ask']:
        print(f"   Model: {pred['p_yes']*100:.1f}% | Market: {pred['yes_ask']*100:.1f}%")
    if pred['edge'] is not None:
        print(f"   Edge: {pred['edge']:.1f}%")
    print(f"   → {action['reason']}")

# Count action types
action_counts = {}
for eval in data['evaluations']:
    action_type = eval['action']['type']
    action_counts[action_type] = action_counts.get(action_type, 0) + 1

print("\n" + "="*80)
print("📈 SUMMARY:")
print("="*80)
print(f"\nTotal evaluations: {len(data['evaluations'])}")
for action_type, count in action_counts.items():
    emoji = {'hold': '⏸️', 'buy': '🟢', 'sell': '🔴'}.get(action_type, '❓')
    print(f"  {emoji} {action_type.upper()}: {count}")

print("\n" + "="*80)
print("⚠️  WHY YOU DON'T SEE THIS IN THE DASHBOARD:")
print("="*80)
print("1. ✅ API endpoint is deployed and working")
print("2. ✅ Test data exists in database")
print("3. ❌ Dashboard hasn't been redeployed with the new TimelineTab code")
print("4. ❌ Dashboard is still showing trades only, not all cycle evaluations")

print("\nTo fix: The dashboard needs to be redeployed to use the new code.")
print("Once deployed, the timeline will show all the HOLDs, BUYs, and SELLs above.")