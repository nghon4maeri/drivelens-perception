-- PII cache reuses artifact lifecycle/RLS; indexed exact owner/input identity, no new storage table.
CREATE INDEX IF NOT EXISTS idx_artifact_pii_cache
ON rav_pilot_artifact (owner_id, input_hash, created_at DESC)
WHERE kind = 'EXPORT_PREVIEW' AND variant = 'PII_REDACTED' AND state = 'READY';
