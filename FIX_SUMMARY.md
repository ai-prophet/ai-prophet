# Critical Bug Fix: Signal ID Reuse in NET Position Management

## The Bug

We discovered a critical bug in the trading system that was causing:
1. **Overselling of non-existent positions** (e.g., trying to sell 397 YES shares when 0 were owned)
2. **Wrong side being sold** (selling YES when holding NO, or vice versa)
3. **Signal contamination across markets** (signal for market A being used for orders on markets B, C, D, etc.)

## Root Cause

The bug was in the NET position management logic in `packages/core/ai_prophet_core/betting/engine.py`.

When the system needed to sell position in Market A to fund a purchase in Market B, it would:
1. Create a signal for Market B (e.g., signal #6149 to BUY YES in KXUSAIRANAGREEMENT-27-26APR)
2. Incorrectly use Market B's signal_id for the SELL order on Market A
3. This caused massive contamination where one signal was used for orders on 14+ different markets

### Example:
- Signal #6149 was created for market `KXUSAIRANAGREEMENT-27-26APR` (wanting to BUY YES)
- This same signal was incorrectly used for 164 orders across 14 different markets:
  - KXDHSFUND-26APR01
  - KXGABBARDOUT-26-APR01
  - KXALBUMRELEASEDATEUZI-APR01-26
  - And 11 others...

## The Fix

Changed line 489 in `engine.py`:
```python
# Before (BUG):
self._save_order(
    signal_id=signal_id,  # Using Market B's signal for Market A's sell order
    ...
)

# After (FIXED):
self._save_order(
    signal_id=None,  # NET sells are not driven by a signal for this market
    ...
)
```

Also updated `_save_order` to accept `signal_id=None` (previously it would skip saving if signal_id was None).

## Impact

This fix prevents:
1. Signal contamination across markets
2. Incorrect side being sold in NET management
3. Overselling of non-existent positions

## Verification

Run `python monitor_fix_deployment.py` to check if the fix has been deployed.

The fix is working when:
- New NET sell orders have `signal_id = NULL` in the database
- No new signal mismatches occur (orders on market A using signal from market B)

## Deployment

The fix has been:
1. Committed to git: commit `c7c1fba`
2. Pushed to GitHub: `anri-trading` branch
3. Awaiting deployment via Render dashboard

Once deployed, the worker services will stop creating incorrect cross-market orders.