#!/usr/bin/env python3
"""
Diagnostic script to identify and fix Kalshi API authentication issues.

This script:
1. Checks all possible environment variable configurations
2. Tests Kalshi API authentication
3. Provides clear feedback on what's missing or misconfigured
4. Suggests fixes
"""

import os
import sys
import base64
import logging
from dotenv import load_dotenv

# Add parent dirs to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "core"))

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def check_env_vars():
    """Check all possible Kalshi environment variable configurations."""
    print("\n" + "="*60)
    print("CHECKING ENVIRONMENT VARIABLES")
    print("="*60)

    instance_name = os.getenv("TRADING_INSTANCE_NAME", "Haifeng")
    print(f"\nInstance Name: {instance_name}")

    # Check direct environment variables
    direct_vars = {
        "KALSHI_API_KEY_ID": os.getenv("KALSHI_API_KEY_ID"),
        "KALSHI_PRIVATE_KEY_B64": os.getenv("KALSHI_PRIVATE_KEY_B64"),
        "KALSHI_BASE_URL": os.getenv("KALSHI_BASE_URL"),
    }

    print("\nDirect Environment Variables:")
    for key, value in direct_vars.items():
        if value:
            if "KEY" in key:
                # Mask sensitive data
                masked = value[:8] + "..." if len(value) > 8 else value
                print(f"  ✓ {key}: {masked}")
            else:
                print(f"  ✓ {key}: {value}")
        else:
            print(f"  ✗ {key}: NOT SET")

    # Check instance-specific variables
    suffix = instance_name.upper()
    instance_vars = {
        f"KALSHI_API_KEY_ID_{suffix}": os.getenv(f"KALSHI_API_KEY_ID_{suffix}"),
        f"KALSHI_PRIVATE_KEY_B64_{suffix}": os.getenv(f"KALSHI_PRIVATE_KEY_B64_{suffix}"),
        f"KALSHI_BASE_URL_{suffix}": os.getenv(f"KALSHI_BASE_URL_{suffix}"),
    }

    print(f"\nInstance-Specific Variables (for {instance_name}):")
    for key, value in instance_vars.items():
        if value:
            if "KEY" in key:
                masked = value[:8] + "..." if len(value) > 8 else value
                print(f"  ✓ {key}: {masked}")
            else:
                print(f"  ✓ {key}: {value}")
        else:
            print(f"  ✗ {key}: NOT SET")

    # Check shared variables (from kalshi-trading-shared)
    shared_vars = {
        "KALSHI_TRADING_SHARED_KALSHI_API_KEY_ID": os.getenv("KALSHI_TRADING_SHARED_KALSHI_API_KEY_ID"),
        "KALSHI_TRADING_SHARED_KALSHI_PRIVATE_KEY_B64": os.getenv("KALSHI_TRADING_SHARED_KALSHI_PRIVATE_KEY_B64"),
        "KALSHI_TRADING_SHARED_KALSHI_BASE_URL": os.getenv("KALSHI_TRADING_SHARED_KALSHI_BASE_URL"),
    }

    print("\nShared Environment Variables (kalshi-trading-shared):")
    for key, value in shared_vars.items():
        if value:
            if "KEY" in key:
                masked = value[:8] + "..." if len(value) > 8 else value
                print(f"  ✓ {key}: {masked}")
            else:
                print(f"  ✓ {key}: {value}")
        else:
            print(f"  ✗ {key}: NOT SET")

    # Determine which configuration will be used
    api_key_id = None
    private_key = None
    base_url = None

    # Check in priority order (same as get_instance_env)
    for prefix in [f"KALSHI_API_KEY_ID_{suffix}", "KALSHI_API_KEY_ID", "KALSHI_TRADING_SHARED_KALSHI_API_KEY_ID"]:
        if os.getenv(prefix):
            api_key_id = os.getenv(prefix)
            print(f"\nAPI Key will be loaded from: {prefix}")
            break

    for prefix in [f"KALSHI_PRIVATE_KEY_B64_{suffix}", "KALSHI_PRIVATE_KEY_B64", "KALSHI_TRADING_SHARED_KALSHI_PRIVATE_KEY_B64"]:
        if os.getenv(prefix):
            private_key = os.getenv(prefix)
            print(f"Private Key will be loaded from: {prefix}")
            break

    for prefix in [f"KALSHI_BASE_URL_{suffix}", "KALSHI_BASE_URL", "KALSHI_TRADING_SHARED_KALSHI_BASE_URL"]:
        if os.getenv(prefix):
            base_url = os.getenv(prefix)
            print(f"Base URL will be loaded from: {prefix}")
            break

    if not base_url:
        base_url = "https://api.markets.kalshi.com"
        print(f"Base URL will use default: {base_url}")

    return api_key_id, private_key, base_url, instance_name


def validate_private_key(private_key_b64):
    """Validate the private key format."""
    print("\n" + "="*60)
    print("VALIDATING PRIVATE KEY")
    print("="*60)

    if not private_key_b64:
        print("✗ No private key provided")
        return False

    try:
        # Decode from base64
        decoded = base64.b64decode(private_key_b64)
        key_str = decoded.decode('utf-8')

        # Check if it's a valid PEM format
        if "-----BEGIN" in key_str and "-----END" in key_str:
            print("✓ Private key is valid PEM format")
            print(f"  Key type: {key_str.split('-----')[1].strip()}")
            return True
        else:
            print("✗ Private key is not in PEM format")
            print("  Expected format: -----BEGIN RSA PRIVATE KEY-----")
            return False

    except Exception as e:
        print(f"✗ Failed to decode private key: {e}")
        return False


def test_kalshi_connection(api_key_id, private_key, base_url):
    """Test actual connection to Kalshi API."""
    print("\n" + "="*60)
    print("TESTING KALSHI API CONNECTION")
    print("="*60)

    if not api_key_id or not private_key:
        print("✗ Cannot test - missing credentials")
        return False

    try:
        from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter

        print(f"\nConnecting to: {base_url}")
        print(f"Using API Key: {api_key_id[:8]}...")

        # Initialize adapter
        adapter = KalshiAdapter(
            api_key_id=api_key_id,
            private_key_base64=private_key,
            base_url=base_url,
            dry_run=False
        )

        # Test authentication by getting balance
        print("\nTesting /portfolio/balance endpoint...")
        try:
            balance = adapter.get_balance()
            print(f"✓ Authentication successful!")
            print(f"  Account balance: ${balance:.2f}")

            # Test positions endpoint
            print("\nTesting /portfolio/positions endpoint...")
            positions = adapter.get_positions()
            print(f"✓ Positions endpoint working!")
            print(f"  Active positions: {len(positions)}")

            return True

        except Exception as e:
            if "401" in str(e):
                print(f"✗ Authentication failed: {e}")
                print("\nPossible causes:")
                print("  1. API key or private key is incorrect")
                print("  2. API key has been revoked or expired")
                print("  3. Private key doesn't match the API key")
            else:
                print(f"✗ API call failed: {e}")
            return False

    except ImportError as e:
        print(f"✗ Failed to import KalshiAdapter: {e}")
        print("  Make sure ai_prophet_core is installed")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False


def suggest_fixes(api_key_id, private_key, base_url, instance_name):
    """Suggest fixes based on the diagnosis."""
    print("\n" + "="*60)
    print("SUGGESTED FIXES")
    print("="*60)

    fixes = []

    if not api_key_id:
        fixes.append(f"""
Missing Kalshi API Key ID. Set one of these environment variables:
  1. KALSHI_API_KEY_ID_{instance_name.upper()}  (instance-specific, highest priority)
  2. KALSHI_API_KEY_ID                          (general)
  3. KALSHI_TRADING_SHARED_KALSHI_API_KEY_ID    (shared config)
        """)

    if not private_key:
        fixes.append(f"""
Missing Kalshi Private Key. Set one of these environment variables:
  1. KALSHI_PRIVATE_KEY_B64_{instance_name.upper()}  (instance-specific, highest priority)
  2. KALSHI_PRIVATE_KEY_B64                          (general)
  3. KALSHI_TRADING_SHARED_KALSHI_PRIVATE_KEY_B64    (shared config)

The private key should be base64-encoded. To encode your key:
  cat your_private_key.pem | base64 | tr -d '\n'
        """)

    if fixes:
        print("\nRequired fixes:")
        for i, fix in enumerate(fixes, 1):
            print(f"{i}. {fix}")
    else:
        print("\n✓ All environment variables are configured")

    print("\nTo set environment variables on Render:")
    print("  1. Go to your Render dashboard")
    print("  2. Select the kalshi-sync-service")
    print("  3. Go to Environment tab")
    print("  4. Add the missing variables")
    print("  5. Click 'Save Changes' to trigger a redeploy")

    print("\nFor local testing, add to your .env file:")
    if not api_key_id:
        print(f"  KALSHI_API_KEY_ID=your_api_key_here")
    if not private_key:
        print(f"  KALSHI_PRIVATE_KEY_B64=your_base64_encoded_key_here")


def main():
    print("\n" + "="*60)
    print("KALSHI AUTHENTICATION DIAGNOSTIC")
    print("="*60)

    # Check environment variables
    api_key_id, private_key, base_url, instance_name = check_env_vars()

    # Validate private key format if present
    if private_key:
        validate_private_key(private_key)

    # Test actual connection
    success = test_kalshi_connection(api_key_id, private_key, base_url)

    # Suggest fixes if needed
    if not success:
        suggest_fixes(api_key_id, private_key, base_url, instance_name)
    else:
        print("\n" + "="*60)
        print("✓ KALSHI AUTHENTICATION IS WORKING CORRECTLY")
        print("="*60)
        print("\nThe 401 errors in your logs suggest the environment variables")
        print("are not properly configured on Render. Please check the Render")
        print("dashboard and ensure the variables are set correctly.")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())