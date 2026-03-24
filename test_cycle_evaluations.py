#!/usr/bin/env python3
"""Test the /cycle-evaluations API endpoint locally and on production."""

import os
import sys
import json
import requests
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "services"))

def test_local_endpoint():
    """Test the endpoint locally (if API is running)."""
    print("Testing local endpoint...")
    try:
        # Try to import and test locally
        from api.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/cycle-evaluations?limit=3&instance_name=Haifeng")

        if response.status_code == 200:
            data = response.json()
            print(f"✓ Local test successful")
            print(f"  Total evaluations: {data.get('total', 0)}")
            print(f"  Evaluations returned: {len(data.get('evaluations', []))}")

            if data.get('evaluations'):
                eval = data['evaluations'][0]
                print(f"\nFirst evaluation:")
                print(f"  Ticker: {eval.get('ticker')}")
                print(f"  Action: {eval['action']['type']} - {eval['action']['description']}")
                print(f"  Reason: {eval['action']['reason']}")
                if eval['prediction']['edge'] is not None:
                    print(f"  Edge: {eval['prediction']['edge']:.1f}%")
        else:
            print(f"✗ Local test failed: {response.status_code}")
            print(response.text[:500])
    except Exception as e:
        print(f"✗ Could not test locally: {e}")

def test_production_endpoint():
    """Test the endpoint on production."""
    print("\nTesting production endpoint...")

    url = 'https://kalshi-trading-api.onrender.com/cycle-evaluations'
    params = {
        'limit': 5,
        'instance_name': 'Haifeng',
    }

    try:
        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 404:
            print("✓ Endpoint not yet deployed (expected)")
            print("  The endpoint has been added to the code but needs deployment")
            return False
        elif response.status_code == 200:
            data = response.json()
            print("✓ Production endpoint working!")
            print(f"  Total evaluations: {data.get('total', 0)}")
            print(f"  Evaluations returned: {len(data.get('evaluations', []))}")

            # Show sample evaluations
            for i, eval in enumerate(data.get('evaluations', [])[:3], 1):
                print(f"\nEvaluation {i}:")
                print(f"  Ticker: {eval.get('ticker')}")
                print(f"  Timestamp: {eval.get('timestamp')}")
                print(f"  Action: {eval['action']['type']} - {eval['action']['description']}")
                print(f"  Reason: {eval['action']['reason']}")
                if eval['prediction']['edge'] is not None:
                    print(f"  Edge: {eval['prediction']['edge']:.1f}%")
                if eval['order']:
                    print(f"  Order: {eval['order']['count']} @ {eval['order']['price_cents']}¢")
            return True
        else:
            print(f"✗ Unexpected status: {response.status_code}")
            print(response.text[:500])
            return False
    except Exception as e:
        print(f"✗ Error testing production: {e}")
        return False

def test_specific_market():
    """Test fetching evaluations for a specific market."""
    print("\nTesting specific market (SpaceX IPO)...")

    url = 'https://kalshi-trading-api.onrender.com/cycle-evaluations'
    params = {
        'ticker': 'KXSPACEIPO-26MAR-27MAR',
        'limit': 10,
        'instance_name': 'Haifeng',
    }

    try:
        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()
            print(f"✓ Found {data.get('total', 0)} evaluations for SpaceX IPO")

            # Count action types
            action_counts = {}
            for eval in data.get('evaluations', []):
                action_type = eval['action']['type']
                action_counts[action_type] = action_counts.get(action_type, 0) + 1

            if action_counts:
                print("\nAction breakdown:")
                for action, count in action_counts.items():
                    print(f"  {action}: {count}")
        elif response.status_code == 404:
            print("  Endpoint not deployed yet")
        else:
            print(f"✗ Error: {response.status_code}")
    except Exception as e:
        print(f"✗ Error: {e}")

if __name__ == "__main__":
    print("=" * 50)
    print("Testing /cycle-evaluations Endpoint")
    print("=" * 50)

    # Test local first
    test_local_endpoint()

    # Test production
    deployed = test_production_endpoint()

    # If deployed, test specific market
    if deployed:
        test_specific_market()

    print("\n" + "=" * 50)
    print("Summary:")
    print("- API endpoint has been added to services/api/main.py")
    print("- Dashboard API client has been updated in services/dashboard/src/lib/api.ts")
    print("- Timeline component has been updated to fetch cycle evaluations")

    if not deployed:
        print("\nNext steps:")
        print("1. Deploy the API changes to production")
        print("2. Deploy the dashboard changes")
        print("3. Verify timeline shows ALL cycle evaluations (holds, buys, sells)")