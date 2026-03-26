#!/usr/bin/env python3
"""
Simple test script to verify Kalshi API authentication.
"""

import os
import sys
import httpx
import time
import hashlib
import base64
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()


def sign_request(method, path, body, timestamp, api_key_id, private_key_b64):
    """Sign a request for Kalshi API."""
    # Decode the base64 private key
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    private_key_pem = base64.b64decode(private_key_b64)
    private_key = serialization.load_pem_private_key(
        private_key_pem, password=None, backend=default_backend()
    )

    # Create the message to sign
    body_hash = hashlib.sha256((body or "").encode()).hexdigest()
    message = f"{timestamp}{method}{path}{body_hash}"

    # Sign the message
    signature = private_key.sign(
        message.encode(),
        padding.PKCS1v15(),
        hashes.SHA256()
    )

    return base64.b64encode(signature).decode()


def test_kalshi_auth():
    """Test Kalshi authentication directly."""
    print("\n" + "="*60)
    print("TESTING KALSHI AUTHENTICATION")
    print("="*60)

    # Get credentials - check instance-specific first
    instance_name = os.getenv("TRADING_INSTANCE_NAME", "Haifeng").upper()

    # Try instance-specific variables first
    api_key_id = os.getenv(f"KALSHI_API_KEY_ID_{instance_name}")
    private_key_b64 = os.getenv(f"KALSHI_PRIVATE_KEY_B64_{instance_name}")

    # Fall back to general variables
    if not api_key_id:
        api_key_id = os.getenv("KALSHI_API_KEY_ID")
    if not private_key_b64:
        private_key_b64 = os.getenv("KALSHI_PRIVATE_KEY_B64")

    base_url = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com")

    print(f"\nInstance: {instance_name}")
    print(f"Base URL: {base_url}")

    if not api_key_id:
        print("✗ No API key found")
        print(f"  Checked: KALSHI_API_KEY_ID_{instance_name}, KALSHI_API_KEY_ID")
        return False

    if not private_key_b64:
        print("✗ No private key found")
        print(f"  Checked: KALSHI_PRIVATE_KEY_B64_{instance_name}, KALSHI_PRIVATE_KEY_B64")
        return False

    print(f"API Key: {api_key_id[:8]}...")
    print(f"Private Key: Present ({len(private_key_b64)} chars)")

    try:
        # Test the balance endpoint
        path = "/trade-api/v2/portfolio/balance"
        method = "GET"
        timestamp = str(int(time.time() * 1000))

        print(f"\nTesting: GET {base_url}{path}")

        # Sign the request
        signature = sign_request(method, path, "", timestamp, api_key_id, private_key_b64)

        # Make the request
        headers = {
            "KALSHI-ACCESS-KEY": api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

        with httpx.Client() as client:
            response = client.get(f"{base_url}{path}", headers=headers)

            if response.status_code == 200:
                data = response.json()
                balance = data.get("balance", 0) / 100  # Convert cents to dollars
                print(f"✓ Authentication successful!")
                print(f"  Balance: ${balance:.2f}")

                # Now test positions
                print("\nTesting positions endpoint...")
                path = "/trade-api/v2/portfolio/positions"
                timestamp = str(int(time.time() * 1000))
                signature = sign_request(method, path, "", timestamp, api_key_id, private_key_b64)
                headers["KALSHI-ACCESS-SIGNATURE"] = signature
                headers["KALSHI-ACCESS-TIMESTAMP"] = timestamp

                response = client.get(f"{base_url}{path}", headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    positions = data.get("market_positions", [])
                    print(f"✓ Positions endpoint working!")
                    print(f"  Active positions: {len(positions)}")

                    # Show first few positions if any
                    for pos in positions[:3]:
                        ticker = pos.get("ticker", "unknown")
                        qty = pos.get("position", 0)
                        print(f"    - {ticker}: {qty} contracts")

                    if len(positions) > 3:
                        print(f"    ... and {len(positions) - 3} more")

                return True

            elif response.status_code == 401:
                print(f"✗ Authentication failed: 401 Unauthorized")
                print(f"  Response: {response.text}")
                print("\nPossible causes:")
                print("  1. API key or private key is incorrect")
                print("  2. API key has been revoked")
                print("  3. Private key doesn't match the API key")
                print("  4. Timestamp is too far off (check system time)")
                return False
            else:
                print(f"✗ Request failed: {response.status_code}")
                print(f"  Response: {response.text}")
                return False

    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_render_vars():
    """Check if we're running on Render and what vars are available."""
    print("\n" + "="*60)
    print("ENVIRONMENT CHECK")
    print("="*60)

    is_render = os.getenv("RENDER") == "true"
    print(f"\nRunning on Render: {is_render}")

    if is_render:
        print("\nRender environment variables:")
        print(f"  Service: {os.getenv('RENDER_SERVICE_NAME', 'unknown')}")
        print(f"  Instance: {os.getenv('RENDER_INSTANCE_ID', 'unknown')[:8]}...")

    # Check for shared variables that might be set on Render
    shared_prefix = "KALSHI_TRADING_SHARED_"
    shared_vars = []
    for key in os.environ:
        if key.startswith(shared_prefix):
            shared_vars.append(key)

    if shared_vars:
        print(f"\nFound {len(shared_vars)} shared variables:")
        for var in shared_vars[:5]:
            value = os.getenv(var, "")
            if "KEY" in var or "SECRET" in var or "PRIVATE" in var:
                masked = value[:8] + "..." if len(value) > 8 else value
                print(f"  {var}: {masked}")
            else:
                print(f"  {var}: {value}")
        if len(shared_vars) > 5:
            print(f"  ... and {len(shared_vars) - 5} more")


def main():
    print("\nKALSHI API AUTHENTICATION TEST")
    print("="*60)

    # Check environment
    check_render_vars()

    # Test authentication
    success = test_kalshi_auth()

    if not success:
        print("\n" + "="*60)
        print("AUTHENTICATION FAILED")
        print("="*60)
        print("\nTo fix on Render:")
        print("1. Go to https://dashboard.render.com")
        print("2. Select your kalshi-sync-service")
        print("3. Go to Environment tab")
        print("4. Ensure these are set:")
        print("   - KALSHI_API_KEY_ID_HAIFENG (or KALSHI_API_KEY_ID)")
        print("   - KALSHI_PRIVATE_KEY_B64_HAIFENG (or KALSHI_PRIVATE_KEY_B64)")
        print("   - KALSHI_BASE_URL (should be https://api.elections.kalshi.com)")
        print("5. Save changes to trigger redeploy")

        print("\nFor local testing, add to .env:")
        print("  KALSHI_API_KEY_ID_HAIFENG=your_api_key")
        print("  KALSHI_PRIVATE_KEY_B64_HAIFENG=your_base64_key")
    else:
        print("\n" + "="*60)
        print("✓ AUTHENTICATION WORKING")
        print("="*60)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())