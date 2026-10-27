# ODD coverage PoC — local QA (2026-10-17)

Scope: CONTRACTS §21.37. Initial local verification before deployment; subsequent
[live release evidence](ODD_COVERAGE_RELEASE_QA.md). No migration, policy change,
new service or dependency. Existing recipes and training provenance gates remain intact.

## Behavior checked

- Cross product Object × ODD plus independent scenario union; duplicate prompts/frames count once.
- Raw cosine and contrast margin gate each branch independently. No pool min-max, competing softmax
  or forced topK when no frame qualifies. Thresholds are heuristics, not calibrated probabilities.
- Round-robin bounded shortlist retains small branches. Final coverage is a weighted gain, **not a
  guaranteed quota per branch**. Novelty and coverage update after every pick and after kept decisions.
- Exact dropped IDs are excluded; dropped decisions do not train a negative classifier.
- Hard temporal/DINO spacing may produce a shorter batch. Optional frozen verified model is scored
  only on min(96,3K) candidates; both-empty detections do not count as confident observations.
- PostgreSQL/moto worker integration exercises optional model binding, frozen model SHA/params,
  idempotent enqueue, persisted trace/reload, batch-only scores/evidence and cross-owner denial.
  Detector calls in those integration tests are mocked; they do not benchmark YOLO on the VPS.
- Local background job without model, legacy adapters and existing perception/response/API behavior
  have regression coverage. Cancellation before inference and bounded requested-only inference are checked.

## Frontend and browser

Backend acceptance: **72 passed** across `test_odd_coverage`, `test_strategy`,
`test_perception_instability`, `test_proposal_response`, `test_odd_coverage_jobs`,
`test_pilot_curation`, `test_pilot_api_v2`. Pilot tests used isolated PostgreSQL on localhost:55433
and moto; basetemp `output/pytest-odd-acceptance-final`. No production DB used.

Three multiline inputs, optional model, advanced contrast/gates/budget; summary reads the displayed
proposal trace rather than next-batch settings. Optional detection funnel shows measured vs observed counts.
Vitest: **61 passed**; TypeScript and pilot Vite build passed. Existing SVG title warning and large bundle
warning remain unrelated to this slice. Ruff passed across src/tests.

Browser acceptance used the actual RecipePanel/SchemaForm/ODDCoverageSummary components with catalog
metadata and an explicitly illustrative frozen trace in a temporary local fixture. Entered two objects,
two ODD lines and a scenario; verified the generated request retained all inputs without a model.
Expanding advanced settings exposed the expected defaults. This is component browser QA, not cloud auth E2E.
The fixture had no model API and showed the handled model-list error; no production calls or mutations.
Temporary fixture files were removed. Screenshot: `output/odd-coverage-browser.png` (gitignored).

## Real model smoke

Read 10 frames, 2 seconds apart, from local `data/videos/commaai-sample.mp4`. Computed real CLIP B/32
and DINOv2-S embeddings using cached checkpoints; ran native ODDCoverage with default gates and
queries cars × {in daytime, at night} plus an independent heavy snowfall scenario. No mocked embeddings.

| Measurement | Result |
|---|---|
| CLIP/DINO preparation, including cold model loads | 37.30 s |
| First text encoding + proposal | 0.481 s |
| Repeated proposal with resident cached text/features | 0.00067 s |
| Per-branch eligible counts | daytime 5, night 0, snowfall 0 |
| Requested/selected | 4 / 2; hard spacing retained |

Local CPU limited to 2 torch threads. Report/evidence: `output/odd-coverage-demo/report.json`.
These tiny-sample times exclude DB, S3, queue and network. They are **not** VPS latency promises or a
semantic quality evaluation. Threshold calibration, mixed ODD labeled evaluation, production capacity
and real optional detector benchmarking remain separate acceptance work. No mAP/uplift claimed.
