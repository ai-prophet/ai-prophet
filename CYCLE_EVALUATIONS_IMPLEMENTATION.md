# Cycle Evaluations Implementation

## Summary
Implemented a new `/cycle-evaluations` API endpoint that shows ALL cycle evaluations (holds, buys, sells) for each market. This addresses the user requirement: **"In the timeline, you need to show the behavior at EVERY cycle. It is a hold, a buy, a sell, etc. It should show what is happening every cycle"**

## Changes Made

### 1. API Endpoint (`services/api/main.py`)
- Added new endpoint: `GET /cycle-evaluations` at line 1966
- Returns all predictions from `betting_predictions` table
- LEFT JOINs with `betting_orders` to identify which predictions resulted in trades
- Calculates edge as `(p_yes - yes_ask) * 100` to show percentage
- Determines action type:
  - **HOLD**: No associated order found (prediction without trade)
  - **BUY/SELL**: Order exists with FILLED status
  - **DRY_RUN**: Order exists with DRY_RUN status
  - **PENDING**: Order exists with other status
- Provides reasoning for each decision:
  - For HOLDs: "Edge X% < 3% threshold", "Edge X% (extreme probability)", "Edge X% (position/capital limit)"
  - For trades: "Edge X% → buy/sell"

### 2. Dashboard API Client (`services/dashboard/src/lib/api.ts`)
- Added `CycleEvaluation` interface (lines 421-444)
- Added `CycleEvaluationsData` interface (lines 446-451)
- Added `getCycleEvaluations` method to API client (lines 905-912)

### 3. Dashboard Timeline Component (`services/dashboard/src/components/UnifiedMarketTable.tsx`)
- Updated `TimelineTab` component to fetch cycle evaluations
- Added `apiClient` and `instanceCacheKey` props
- Added `useEffect` hook to fetch evaluations when component mounts
- Will display all cycle evaluations instead of just executed trades

## How It Works

1. **Every prediction is a cycle evaluation**: Each row in `betting_predictions` represents the system evaluating a market
2. **Correlation with orders**: Orders created within 1 minute of a prediction are considered to be from that evaluation
3. **Hold detection**: If no order exists for a prediction, it was a HOLD decision
4. **Edge calculation**: Shows the edge at the time of evaluation (prediction - market ask price)
5. **Reasoning provided**: Each evaluation includes why the action was taken or not taken

## Testing

Created test script: `test_cycle_evaluations.py`
- Tests local endpoint (if API is running)
- Tests production endpoint
- Verifies data structure and content

## Deployment Status

- ✅ API endpoint code added
- ✅ Dashboard API client updated
- ✅ Timeline component updated
- ⏳ Awaiting deployment to production

## Next Steps

1. **Deploy API changes**: Push to production to make endpoint available
2. **Deploy dashboard changes**: Update frontend to use new endpoint
3. **Verify functionality**: Check that timeline shows all cycle evaluations

## Example Response

```json
{
  "evaluations": [
    {
      "id": 12345,
      "ticker": "KXSPACEIPO-26MAR-27MAR",
      "market_id": "market-123",
      "market_title": "SpaceX IPO by March 2027",
      "timestamp": "2024-03-24T10:30:00Z",
      "model": "gemini-2.0-flash-thinking-exp",
      "prediction": {
        "p_yes": 0.42,
        "edge": 2.5,
        "yes_ask": 0.395,
        "no_ask": 0.615
      },
      "action": {
        "type": "hold",
        "description": "HOLD",
        "reason": "Edge 2.5% < 3% threshold"
      },
      "order": null
    },
    {
      "id": 12346,
      "ticker": "KXSPACEIPO-26MAR-27MAR",
      "market_id": "market-123",
      "market_title": "SpaceX IPO by March 2027",
      "timestamp": "2024-03-24T11:30:00Z",
      "model": "gemini-2.0-flash-thinking-exp",
      "prediction": {
        "p_yes": 0.48,
        "edge": 8.5,
        "yes_ask": 0.395,
        "no_ask": 0.615
      },
      "action": {
        "type": "buy",
        "description": "BUY 100 YES",
        "reason": "Edge 8.5% → buy"
      },
      "order": {
        "count": 100,
        "price_cents": 40,
        "status": "FILLED"
      }
    }
  ],
  "total": 150,
  "has_more": true,
  "ticker": "KXSPACEIPO-26MAR-27MAR"
}
```

## Benefits

1. **Complete visibility**: Shows what happened at EVERY cycle, not just trades
2. **Hold reasoning**: Explains why the system didn't trade (low edge, position limits, etc.)
3. **Historical analysis**: Can review all system decisions for any market
4. **Better debugging**: Can identify patterns in hold vs trade decisions
5. **User request fulfilled**: Directly addresses the requirement to show all cycle behavior