# ODD context, CLIP text cache and measured phases — §21.40

Candidate tested locally on 2026-10-17, subsequently deployed with user authorization. No production schema/policy changes.

## Observed live symptom (read-only investigation)

Job `1ef06cf0-e48e-4eda-87c0-72cf6e7d5b5f`, ODD without detector, pool of 86/6710/5752 units:
created13:51:08UTC, claimed13:51:12, cancellation13:53:51, CANCELLED13:55:16.
All six DINO/CLIP NPZ files were already cached. Two raw video cache entries existed;
the third only had a 297795584-byte partial download. Pinned CLIP model/tokenizer files existed and
HF_HUB_OFFLINE was set. The trace had not reached ODD_CLIP_GATE. No object receipts indicated export/dataset cleanup.
This supports unnecessary raw-context preparation as a blocker, not an exact attribution of every second.

## Candidate behavior

- Frozen fps/n_frames/duration metadata reconstructs the same units/spec for ODD without model; NPZ ordered IDs
  still checked. No raw S3 download, probe/hash or pixel decode. Older payloads and detector paths keep raw fallback.
- Exact per-prompt/model/revision vectors persist as atomic validated float32 L2 .npy files, max2048 entries.
  Reordered/repeated prompts reuse cache; a partial miss only encodes missing prompts. Full hits skip model loading.
  Model RAM cache remains process-local; invalid files are recomputed. Scoring/selection unchanged.
- Up to24 phase records survive job polling/reload through progress_detail. Same-phase writes throttled to1Hz;
  phase transitions/final flush also persist. Existing fencing/owner/heartbeat guards remain. Logs contain numeric
  timing/cache status, not prompt/media keys. Phase-boundary updates still have DB overhead; this is not a full
  reduction of proposal DB round trips.
- Cancellation checked before context/S3 work and around each chunk. An already blocked socket read still waits
  for the existing network timeout; no instantaneous-stop guarantee. UI says waiting for safe stop.
- UI displays phase names, real counters, cache state and measured durations; unfinished terminal stages stay
  unfinished. Publication has a current-stage label; precise publish duration remains in existing admin telemetry.

## Verification

- PostgreSQL16 localhost:55433, fresh disposable databases/migrations, S3 moto; no production test data.
- `pytest tests/test_proposal_phase_cache.py tests/test_odd_coverage.py tests/test_odd_coverage_jobs.py
  tests/test_pilot_curation.py tests/test_pilot_progress_eta.py tests/test_pilot_queue.py
  tests/test_pilot_cache_evict.py -q`: **78 passed**,67.05s.
  Includes hard failures on any raw fetch/probe in ODD without detector, optional model path, stage persistence,
  per-phase reset vs same-phase regression, heartbeat preservation, prompt/revision/corrupt cache and chunk cancellation.
- Ruff src/tests, TypeScript noEmit: passed. Vitest: **65 passed** in12 files. Pilot Vite build passed;
  existing bundle-size warning and React Timeline title warning remain outside this slice.
- IAB rendered real ProposalProgress component with explicitly labeled illustrative data in isolated QA Vite5188;
  expanded live counters/timer/cache, simulated stop, collapsed/reopened unfinished state verified.
  Screenshot: gitignored `output/phase-timeline-browser.png`. User's Vite3000/cloud test session untouched.

At the local candidate gate there was no real-model latency benchmark, live job mutation or mixed-load capacity claim.
Local success alone did not prove the candidate was live; the later authorized release is recorded below.

## Authorized release and cloud smoke (2026-10-17)

- Source `fc53ed8807ddd1653ba9a6b20a62c80c46cccd77`; Render `dep-db9ogom1egvs7381p51g` live.
  Combined `docker.io/tonmeu/rav26@sha256:bf001aba96a25bcec4d80d51b79c80a1f7906bcc2ecaa78c139e34ad2a8e73fb`;
  worker `sha256:7f3b2bd79579887e22793622ac972ed901db8aebee52b15f4a86b9b12c34fa2b`, healthy.
  Idle jobs/slots/uploads checked before switch; rollback images retained. Schema, service env and policy unchanged.
- Candidate container on VPS, network disabled,2 CPUs/512MiB,OpenBLAS2 threads: synthetic12548-frame benchmark,
  512 shortlist/100 kept/16 picks:5 branches0.092–0.152s total,72 branches0.291–0.483s total.
  This excludes model/feature loading, DB/S3 and progress writes; no inference or policy-setting changes.
- Authenticated QA API session `40f4833e-298a-4272-ac94-569b7406af79`, two actual ODD jobs on published86-frame
  sample: both SUCCEEDED, persisted timings, metadata-only path, no LOAD_RAW. Second prompt cache hit skipped
  CLIP_MODEL/CLIP_TEXT_ENCODE; both returned3 of requested4 picks under unchanged hard spacing, not forced fill.
- Measured stage sums: cold50.149s, warm22.766s. These include synchronous progress DB transactions at boundaries;
  they are not pure algorithm CPU timings or exact click-to-grid time. Cold model phase17.047s, text encode6.169s;
  warm phases generally2–3s each, showing substantial remaining orchestration/DB overhead. Do not claim subsecond
  proposals from the isolated arithmetic benchmark. Further latency work is separate from this release.
- Local FE3000 proxying real API/media/runtime config verified in browser with cloud thumbnails/readiness.
  User requested a new ordinary account for demo screenshots; `DriveLens Demo` has operations_read=false,
  models_write=false, admin routes403, CPU unmetered/GPU50min per4h, access only to published samples.
  Its browser origin localhost:3000 is separate from the admin's127.0.0.1:3000 origin. No publication of screenshots
  to landing yet. User explicitly approved a one-time local token file; removed immediately after UI entry.
