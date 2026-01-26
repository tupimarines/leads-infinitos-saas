-- Add scheduled_start column to campaigns table
-- This enables scheduling campaigns for future start times

ALTER TABLE campaigns 
ADD COLUMN IF NOT EXISTS scheduled_start TIMESTAMP DEFAULT NULL;

-- Create index for performance (worker queries this frequently)
CREATE INDEX IF NOT EXISTS idx_campaigns_scheduled 
ON campaigns(status, scheduled_start);

-- Add helpful comment
COMMENT ON COLUMN campaigns.scheduled_start IS 
'NULL = start immediately (status will be set to running), datetime = start at that specific time (status pending until then)';

-- Update existing campaigns to have status='running' if they are currently pending
-- (backwards compatibility: existing pending campaigns should start immediately)
UPDATE campaigns 
SET status = 'running' 
WHERE status = 'pending' 
AND scheduled_start IS NULL;
