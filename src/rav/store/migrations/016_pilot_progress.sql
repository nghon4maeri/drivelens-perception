-- Additive progress detail for job telemetry and real-time ETA (#135).
ALTER TABLE rav_pilot_job ADD COLUMN IF NOT EXISTS progress_detail JSONB NOT NULL DEFAULT '{}'::jsonb;
