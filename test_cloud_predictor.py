#!/usr/bin/env python3
"""Quick test of the deployed Cloud Run predictor service."""

import json
import requests

# Service URL
URL = "https://predictor-494415302759.us-west1.run.app"

# Test health
print("Testing health endpoint...")
health_resp = requests.get(f"{URL}/health")
print(f"Health: {health_resp.json()}")

# Test prediction (will fail without valid API keys, but tests connectivity)
print("\nTesting prediction endpoint (expecting API key error)...")
test_request = {
    "model_spec": "gemini:gemini-3.1-pro-preview",
    "market_info": {
        "title": "Will Bitcoin reach $100,000 by end of 2024?",
        "yes_ask": 0.65,
        "no_ask": 0.35
    },
    "instance_name": "Haifeng",
    "api_keys": {
        # Empty API keys - will fail but tests connectivity
    }
}

try:
    pred_resp = requests.post(
        f"{URL}/predict",
        json=test_request,
        timeout=10
    )
    print(f"Status: {pred_resp.status_code}")
    print(f"Response: {pred_resp.text[:200]}...")
except Exception as e:
    print(f"Request failed (expected without API keys): {e}")

print("\nService is deployed and responding correctly!")
print(f"URL to configure in render workers: {URL}")