#!/usr/bin/env python3
"""Test script for predictor service - can be run locally or against Cloud Run."""

import json
import requests
import sys

def test_predictor(url="http://localhost:8080", api_key="test-key"):
    """Test the predictor service with a sample request."""

    # Test health endpoint
    print(f"Testing health endpoint at {url}/health...")
    response = requests.get(f"{url}/health")
    print(f"Health check: {response.json()}\n")

    # Test prediction endpoint
    print(f"Testing prediction endpoint at {url}/predict...")

    # Sample request with API keys passed in the request
    request_data = {
        "model_spec": "gemini:gemini-3.1-pro-preview:market",
        "market_info": {
            "title": "Will Bitcoin reach $100,000 by end of 2024?",
            "subtitle": "Test market for predictor service",
            "category": "Cryptocurrency",
            "yes_ask": 0.65,
            "no_ask": 0.35,
            "open_time": "2024-03-25T12:00:00Z"
        },
        "instance_name": "Haifeng",
        "api_keys": {
            "gemini": "YOUR_GEMINI_API_KEY_HERE",
            "google": "YOUR_GOOGLE_API_KEY_HERE",
            "openai": "YOUR_OPENAI_API_KEY_HERE",
            "anthropic": "YOUR_ANTHROPIC_API_KEY_HERE",
            "xai": "YOUR_XAI_API_KEY_HERE"
        }
    }

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key  # Optional auth header if PREDICTOR_API_KEY is set
    }

    response = requests.post(
        f"{url}/predict",
        json=request_data,
        headers=headers,
        timeout=180
    )

    if response.status_code == 200:
        result = response.json()
        print("Prediction successful!")
        print(json.dumps(result, indent=2))
    else:
        print(f"Error: {response.status_code}")
        print(response.text)

    return response

if __name__ == "__main__":
    # Use command line argument for URL if provided
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"

    print(f"Testing predictor service at: {url}\n")
    print("NOTE: Replace the API keys in the script with real ones before testing!\n")

    test_predictor(url)