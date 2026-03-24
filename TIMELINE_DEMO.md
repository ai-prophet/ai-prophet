# Timeline Demo: Cycle Evaluations

## What the Timeline Will Show

The new `/cycle-evaluations` endpoint shows **EVERY** cycle evaluation, not just executed trades.

### Example Timeline View (DHS Funding Market)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏸️  HOLD 6:25 PM
├─ Model: gemini:gemini-3.1-pro-preview
├─ Prediction: 82.0% | Market: 83.0%
├─ Edge: -1.0%
└─ Edge -1.0% below 3% threshold

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 BUY 6:01 PM | BUY 6 YES (dry run)
├─ Model: gemini:gemini-3.1-pro-preview
├─ Prediction: 88.0% | Market: 81.0%
├─ Edge: 7.0%
└─ Edge 7.0% exceeded threshold → buy

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 SELL 3:07 PM | SELL 3 YES (dry run)
├─ Model: gemini:gemini-3.1-pro-preview
├─ Prediction: 10.0% | Market: 85.0%
├─ Edge: -75.0%
└─ Edge -75.0% exceeded threshold → sell
```

## Key Features

### 1. Complete Visibility
- **EVERY** prediction is shown as a cycle evaluation
- No more missing cycles or gaps in understanding

### 2. Hold Decisions Explained
HOLDs are shown with specific reasons:
- `Edge 1.5% below 3% threshold` - Edge too small
- `Edge 4.5% but at position limit` - Good edge but constrained
- `Edge 8.0% but probability too extreme` - P(YES) > 95% or < 5%

### 3. Action Types
- **🟢 BUY** - System bought shares
- **🔴 SELL** - System sold shares
- **⏸️ HOLD** - System evaluated but didn't trade
- **DRY_RUN** - Test trades (not executed on exchange)

### 4. Real Data Example

From actual database (DHS Funding market):
- **20 evaluations** in last day
- **1 HOLD** (5%) - Edge below threshold
- **18 DRY_RUN trades** (90%) - Test mode
- **1 REAL trade** (5%) - Executed on exchange

## Benefits

1. **Debugging**: See why system didn't trade during certain cycles
2. **Optimization**: Identify patterns in hold decisions
3. **Transparency**: Complete audit trail of all decisions
4. **Analysis**: Understand edge distribution and thresholds

## Database Query

The endpoint queries all predictions and correlates with orders:

```sql
SELECT predictions and orders
FROM betting_predictions
LEFT JOIN trading_markets (for ticker/title)
LEFT JOIN betting_orders (within 1 minute window)
WHERE instance = 'Haifeng'
ORDER BY created_at DESC
```

## Response Format

```json
{
  "evaluations": [
    {
      "id": 12345,
      "ticker": "KXDHSFUND-26APR01",
      "timestamp": "2024-03-24T18:25:31Z",
      "action": {
        "type": "hold",
        "description": "HOLD",
        "reason": "Edge -1.0% below 3% threshold"
      },
      "prediction": {
        "p_yes": 0.82,
        "edge": -1.0,
        "yes_ask": 0.83
      }
    }
  ],
  "total": 20,
  "has_more": false
}
```

## Status

✅ **Implementation Complete**
- API endpoint added and tested
- Dashboard client updated
- Timeline component modified
- Real data verified

🚀 **Ready for Deployment**
- Push to production will enable timeline
- Shows all cycle behavior as requested