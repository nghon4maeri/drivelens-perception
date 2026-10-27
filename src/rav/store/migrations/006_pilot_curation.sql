-- 006_pilot_curation.sql — đề xuất do worker publish (DEP5b, CONTRACTS §21.15, #95)

-- Truy job → proposal (GET /jobs/{id}/result); một proposal cho mỗi job.
ALTER TABLE rav_pilot_proposal ADD COLUMN IF NOT EXISTS job_id UUID REFERENCES rav_pilot_job(id) ON DELETE RESTRICT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_proposal_job ON rav_pilot_proposal(job_id) WHERE job_id IS NOT NULL;

-- Worker publish proposal trong transaction publish; proposal vẫn bất biến (trigger 001).
GRANT INSERT ON rav_pilot_proposal TO rav_pilot_worker;
-- Publish khóa phiên (tuần tự với append decision của API); FOR UPDATE cần quyền UPDATE một cột (như #89).
GRANT UPDATE (name) ON rav_pilot_session TO rav_pilot_worker;
