# Fix Kalshi API Authentication Issues

## Problem Identified

The dashboard top bars are not syncing because the Kalshi API is returning 401 Unauthorized errors with the message "INCORRECT_API_KEY_SIGNATURE". This means:

**The API key and private key in your environment don't match each other.**

## Root Cause

- API Key ID: `dd1b94a9...` (from KALSHI_API_KEY_ID_HAIFENG)
- Private Key: Present but doesn't match the API key
- Error: `INCORRECT_API_KEY_SIGNATURE`

This is preventing:
1. Live position syncing from Kalshi
2. Balance updates
3. Real-time order status polling
4. Dashboard metrics from updating

## How to Fix

### Step 1: Get Correct Credentials from Kalshi

1. Log in to your Kalshi account at https://kalshi.com
2. Go to Settings → API Keys
3. Either:
   - Use an existing API key and download its private key again, OR
   - Create a new API key pair

4. You should have:
   - API Key ID (looks like: `dd1b94a9-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)
   - Private Key file (`.pem` file)

### Step 2: Encode the Private Key

The private key needs to be base64-encoded for the environment variable:

```bash
# On Mac/Linux:
cat your_private_key.pem | base64 | tr -d '\n' > private_key_b64.txt

# The result should be one long line starting with something like:
# LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQ...
```

### Step 3: Update Environment Variables

#### For Local Testing

Update your `.env` file:

```bash
# Use the matching API key and private key
KALSHI_API_KEY_ID_HAIFENG=your_actual_api_key_id_here
KALSHI_PRIVATE_KEY_B64_HAIFENG=your_base64_encoded_private_key_here
KALSHI_BASE_URL=https://api.elections.kalshi.com
```

#### For Render Deployment

1. Go to https://dashboard.render.com
2. Select the `kalshi-sync-service`
3. Go to the **Environment** tab
4. Update these variables:
   - `KALSHI_API_KEY_ID_HAIFENG` = your API key ID
   - `KALSHI_PRIVATE_KEY_B64_HAIFENG` = your base64-encoded private key
   - `KALSHI_BASE_URL` = https://api.elections.kalshi.com

5. Click **Save Changes** - this will trigger a redeploy

6. Repeat for the `api-service` if it also needs Kalshi access

### Step 4: Verify the Fix

After updating, test locally:

```bash
cd services
python3 test_kalshi_auth.py
```

You should see:
```
✓ Authentication successful!
  Balance: $xxx.xx
✓ Positions endpoint working!
  Active positions: X
```

### Step 5: Monitor Render Logs

After Render redeploys:

1. Check the kalshi-sync-service logs
2. Look for successful sync messages instead of 401 errors
3. The dashboard should start showing correct metrics

## Alternative: Shared Credentials

If you want all services to use the same Kalshi account, you can use the shared variables instead:

```bash
# In Render, set these for all services:
KALSHI_TRADING_SHARED_KALSHI_API_KEY_ID=your_api_key
KALSHI_TRADING_SHARED_KALSHI_PRIVATE_KEY_B64=your_base64_key
KALSHI_TRADING_SHARED_KALSHI_BASE_URL=https://api.elections.kalshi.com
```

## Dashboard Fallback

While fixing the credentials, the dashboard can still show data from the database:

```bash
# Calculate metrics from database (doesn't need Kalshi API)
python3 services/calculate_dashboard_metrics_offline.py
```

This will:
- Calculate positions from filled orders
- Compute win rate and P&L
- Update dashboard metrics
- Work without Kalshi API access

## Checklist

- [ ] Get matching API key and private key from Kalshi
- [ ] Base64-encode the private key
- [ ] Update environment variables (local .env)
- [ ] Update Render environment variables
- [ ] Test authentication locally
- [ ] Wait for Render to redeploy
- [ ] Verify dashboard metrics are updating
- [ ] Check that 401 errors have stopped in logs