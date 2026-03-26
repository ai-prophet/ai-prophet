#!/usr/bin/env python3
"""
Script to update Kalshi API credentials in .env file.

Usage:
    python3 services/update_kalshi_credentials.py

This will:
1. Prompt for your Kalshi API key and private key file
2. Validate they work together
3. Update your .env file with the correct credentials
"""

import os
import sys
import base64
import httpx
import time
import hashlib
from pathlib import Path
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend


def sign_request(method, path, body, timestamp, api_key_id, private_key_b64):
    """Sign a request for Kalshi API."""
    private_key_pem = base64.b64decode(private_key_b64)
    private_key = serialization.load_pem_private_key(
        private_key_pem, password=None, backend=default_backend()
    )

    body_hash = hashlib.sha256((body or "").encode()).hexdigest()
    message = f"{timestamp}{method}{path}{body_hash}"

    signature = private_key.sign(
        message.encode(),
        padding.PKCS1v15(),
        hashes.SHA256()
    )

    return base64.b64encode(signature).decode()


def test_credentials(api_key_id, private_key_b64, base_url):
    """Test if the credentials work."""
    try:
        path = "/trade-api/v2/portfolio/balance"
        method = "GET"
        timestamp = str(int(time.time() * 1000))

        signature = sign_request(method, path, "", timestamp, api_key_id, private_key_b64)

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
                balance = data.get("balance", 0) / 100
                return True, f"Success! Balance: ${balance:.2f}"
            elif response.status_code == 401:
                error = response.json().get("error", {})
                return False, f"Authentication failed: {error.get('details', 'Unknown error')}"
            else:
                return False, f"Request failed: {response.status_code}"
    except Exception as e:
        return False, f"Error: {e}"


def encode_private_key(key_path):
    """Read and encode a private key file."""
    try:
        with open(key_path, 'rb') as f:
            key_content = f.read()

        # If it's already base64, decode and re-encode to ensure it's clean
        try:
            # Try to decode it first
            decoded = base64.b64decode(key_content)
            # If successful, it was already base64
            return key_content.decode('utf-8').replace('\n', '').replace('\r', '')
        except:
            # Not base64, encode it
            return base64.b64encode(key_content).decode('utf-8')
    except Exception as e:
        return None, str(e)


def update_env_file(api_key_id, private_key_b64, instance_name="HAIFENG"):
    """Update the .env file with new credentials."""
    env_file = Path(".env")

    # Read existing content
    lines = []
    if env_file.exists():
        with open(env_file, 'r') as f:
            lines = f.readlines()

    # Keys to update
    api_key_var = f"KALSHI_API_KEY_ID_{instance_name}"
    private_key_var = f"KALSHI_PRIVATE_KEY_B64_{instance_name}"

    # Update or add the variables
    api_key_found = False
    private_key_found = False

    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{api_key_var}="):
            new_lines.append(f"{api_key_var}={api_key_id}\n")
            api_key_found = True
        elif line.strip().startswith(f"{private_key_var}="):
            new_lines.append(f"{private_key_var}={private_key_b64}\n")
            private_key_found = True
        else:
            new_lines.append(line)

    # Add missing variables
    if not api_key_found:
        new_lines.append(f"\n{api_key_var}={api_key_id}\n")
    if not private_key_found:
        new_lines.append(f"{private_key_var}={private_key_b64}\n")

    # Write back
    with open(env_file, 'w') as f:
        f.writelines(new_lines)

    print(f"\n✓ Updated .env file with new credentials")
    print(f"  {api_key_var}={api_key_id[:8]}...")
    print(f"  {private_key_var}=<encoded>")


def main():
    print("\nKALSHI CREDENTIALS UPDATER")
    print("="*60)

    base_url = "https://api.elections.kalshi.com"
    instance_name = "HAIFENG"

    print("\nThis script will help you update your Kalshi API credentials.")
    print(f"Target environment: {instance_name}")
    print(f"API URL: {base_url}")

    # Get API key
    print("\n1. Enter your Kalshi API Key ID")
    print("   (Get this from https://kalshi.com → Settings → API Keys)")
    api_key_id = input("   API Key ID: ").strip()

    if not api_key_id:
        print("✗ No API key provided")
        return 1

    # Get private key
    print("\n2. Enter the path to your private key file")
    print("   (This is the .pem file you downloaded when creating the API key)")
    key_path = input("   Private key file path: ").strip()

    if not key_path:
        print("✗ No private key file provided")
        return 1

    if not os.path.exists(key_path):
        print(f"✗ File not found: {key_path}")
        return 1

    # Encode the private key
    print("\n3. Encoding private key...")
    result = encode_private_key(key_path)
    if isinstance(result, tuple):
        print(f"✗ Failed to encode private key: {result[1]}")
        return 1

    private_key_b64 = result
    print(f"✓ Private key encoded ({len(private_key_b64)} chars)")

    # Test the credentials
    print("\n4. Testing credentials with Kalshi API...")
    success, message = test_credentials(api_key_id, private_key_b64, base_url)

    if not success:
        print(f"✗ {message}")
        print("\nThe credentials don't work. Please check:")
        print("  1. The API key ID is correct")
        print("  2. The private key file matches this API key")
        print("  3. The API key hasn't been revoked")
        return 1

    print(f"✓ {message}")

    # Update .env file
    print("\n5. Updating .env file...")
    update_env_file(api_key_id, private_key_b64, instance_name)

    print("\n" + "="*60)
    print("SUCCESS! Credentials updated and verified.")
    print("="*60)

    print("\nNext steps:")
    print("1. The .env file has been updated with working credentials")
    print("2. Restart any running services to pick up the new credentials")
    print("3. For Render deployment:")
    print("   a. Go to https://dashboard.render.com")
    print("   b. Select your kalshi-sync-service")
    print("   c. Go to Environment tab")
    print(f"   d. Update KALSHI_API_KEY_ID_{instance_name} = {api_key_id}")
    print(f"   e. Update KALSHI_PRIVATE_KEY_B64_{instance_name} = <paste the encoded key>")
    print("   f. Save Changes to trigger redeploy")

    # Show the encoded key for easy copying
    print("\n" + "="*60)
    print("ENCODED PRIVATE KEY (for Render):")
    print("="*60)
    print(private_key_b64)
    print("="*60)

    return 0


if __name__ == "__main__":
    sys.exit(main())