-- Migration to add skip_reason column to betting_predictions table
-- This tracks why trades were skipped due to constraints

ALTER TABLE betting_predictions
ADD COLUMN IF NOT EXISTS skip_reason VARCHAR(255);

-- Add index for faster queries on skip reasons
CREATE INDEX IF NOT EXISTS idx_betting_pred_skip_reason
ON betting_predictions(instance_name, skip_reason)
WHERE skip_reason IS NOT NULL;

-- Comment on the column for documentation
COMMENT ON COLUMN betting_predictions.skip_reason IS
'Reason why a trade was skipped (e.g. "4-hour cooldown", "Market unchanged: 5¢ movement")';