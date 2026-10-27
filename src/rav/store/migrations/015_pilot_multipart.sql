-- Additive large upload transport; old POST uploads and frozen jobs remain compatible.
ALTER TABLE rav_pilot_upload ADD COLUMN transfer_method text NOT NULL DEFAULT 'POST'
  CHECK (transfer_method IN ('POST','MULTIPART'));
ALTER TABLE rav_pilot_upload ADD COLUMN multipart_id text;
