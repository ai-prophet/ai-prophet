# Fix Kalshi Authentication on Render

## Current Problem

Your dashboard is not updating because the Kalshi API is returning 401 errors with "INCORRECT_API_KEY_SIGNATURE". This means your API key and private key don't match.

## Quick Fix Instructions

### Step 1: Get Your Correct Credentials

1. Go to https://kalshi.com and log in
2. Navigate to Settings → API Keys
3. Either:
   - **Option A**: Create a NEW API key (recommended)
     - Click "Create API Key"
     - Download the private key file (`.pem`)
     - Save the API Key ID shown

   - **Option B**: Use an existing key
     - You MUST have the original private key file
     - If you lost it, delete the key and create a new one

### Step 2: Prepare the Credentials

Run this locally to encode your private key:

```bash
# If you have a .pem file:
cat your_private_key.pem | base64 | tr -d '\n'

# Copy the output - it should be one long line starting with:
# LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQ...
```

### Step 3: Update Render Environment Variables

1. Go to https://dashboard.render.com
2. Find and select **kalshi-sync-service**
3. Click on **Environment** tab
4. Update these variables:

   ```
   KALSHI_API_KEY_ID_HAIFENG = [Your API Key ID from Kalshi]
   KALSHI_PRIVATE_KEY_B64_HAIFENG = [Your base64-encoded private key]
   KALSHI_BASE_URL = https://api.elections.kalshi.com
   ```

5. Click **Save Changes** (this triggers a redeploy)

6. Repeat for **api-service** if it also needs Kalshi access

### Step 4: Verify the Fix

Watch the logs after redeployment:
- The 401 errors should stop
- You should see successful sync messages
- Dashboard metrics should start updating

## Testing Locally First (Optional)

Before updating Render, you can test locally:

```bash
# Update your .env file with the new credentials
echo "KALSHI_API_KEY_ID_HAIFENG=your_api_key_here" >> .env
echo "KALSHI_PRIVATE_KEY_B64_HAIFENG=your_base64_key_here" >> .env

# Test the authentication
python3 services/test_kalshi_auth.py
```

If it shows "✓ Authentication successful!" then the credentials are correct.

## What This Will Fix

Once the credentials are corrected:
- ✅ Live balance will sync from Kalshi
- ✅ Position data will be accurate
- ✅ Order status will update in real-time
- ✅ Dashboard top bars will show correct metrics
- ✅ No more 401 authentication errors in logs

## Need Help?

If you're still seeing issues after updating:

1. Check the exact error in Render logs
2. Ensure the private key was base64-encoded correctly
3. Verify you're using the matching API key and private key pair
4. Try creating a fresh API key pair on Kalshi

## Command Summary

```bash
# 1. Get your private key file from Kalshi

# 2. Encode it
cat kalshi_private_key.pem | base64 | tr -d '\n' > encoded_key.txt

# 3. Update Render environment:
#    KALSHI_API_KEY_ID_HAIFENG = [from Kalshi website]
#    KALSHI_PRIVATE_KEY_B64_HAIFENG = [contents of encoded_key.txt]

# 4. Save and let Render redeploy
```