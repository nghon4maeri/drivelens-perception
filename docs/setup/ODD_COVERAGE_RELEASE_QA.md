# ODD coverage — release 2026-10-17

User authorized deployment after local implementation. Source `065ff890dc102952b8a0baeee2159e289a8049ac`.
[Local tests and limits](ODD_COVERAGE_QA.md), CONTRACTS §21.37. No Git push.

## Rollout

- Preflight: no active jobs/uploads, resource slots free; old worker healthy, VPS 37.12 GiB free.
- Archived committed source only. Built combined and worker images in a separate VPS candidate directory.
  Build runs frontend tests, TypeScript and Vite; candidate checks worker registry/catalog parity,
  API catalog and compiled FE query fields. Live migration checksums matched; no migration applied.
- Published combined image to the existing authorized `tonmeu/rav26` registry. Backed up worker
  release.env/worker.env/compose before image replacement; saved previous Render image/config.
  Idle rechecked, worker replaced first, waited healthy, then pinned Render to the new digest.
  Rollback scripts retain previous images/config. No policy, quota, credentials or ingress changes.
- Render deploy `dep-db9m0a61egvs73fpar3g` live:
  `docker.io/tonmeu/rav26@sha256:6748f60f66b4e65d3a9a6ecab7a74cd77fee0c920ad4ba0902da199710de1131`.
- Worker `sha256:7b2b24846482a39a9e32f4d0d330d0d670af56ea7463f2bc77f476e5f45c4e8c` healthy.
  Final readiness HTTP200; live image identities matched release records.

## Live functional evidence

Used an existing dedicated QA account and new QA session over published SYSTEM sample data:
86 units, prepared real CLIP/DINO. No production user decisions modified; no email sent or GPU job run.

- Catalog exposes available native `odd_coverage`, background execution and optional model.
- Cars × {daytime, night} plus snowfall scenario: eligible branch counts [46,0,0], selected2/4.
  Round-trip smoke31.74s including submit/idempotency replay, queue/poll, result/readback.
- Kept one frame and dropped another in QA session: next proposal excluded both, eligible[44,0,0],
  kept coverage[1,0,0], selected3/4; round-trip20.75s.
- Frozen params/trace survived readback; score/evidence IDs stayed within the selected batch.
- Margin2 gate returned zero candidates/picks; round-trip20.26s. No forced fallback topK.
- Switched to existing interactive Random recipe successfully with decisions preserved.
- Optional real YOLO11n COCO checkpoint SHA `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`:
  only3 shortlisted frames measured (limit12),1 observed and1 selected,27.92s total. Frozen model SHA
  and per-frame observed/matching evidence verified. Both-empty detections excluded.
- Chrome on live site displayed the new Vietnamese strategy title, three text inputs and
  optional no-model mode. Preview configuration changed only in a separate browser tab; no proposal
  submitted or keep/drop performed in the user's session.

Artifacts (gitignored): `output/odd-coverage-release/release.json`, `smoke.json`, individual proposal
records, `optional-smoke.json`, `strategy-live.png`; rollback backup under worker output/pre-odd-20261017-065ff890.

## Limits

Total cloud jobs remain around20–32s in this small smoke. These are end-to-end observations, not pure
inference benchmarks; DB/artifact/network/queue overhead was not isolated. No latency improvement claimed.
Default CLIP thresholds remain heuristics. Coverage is a weighted preference, not a per-branch quota;
short batches are expected under hard spacing/observed-signal gates. No labeled semantic evaluation,
large-pool or multi-user load acceptance added by this release.
