# Admin prepared sample copy — QA 2026-10-17

Scope CONTRACTS §21.38, user approved independent SYSTEM copies. This records local candidate QA;
subsequent authorized push/deploy and cloud evidence are in [release QA](ADMIN_SAMPLE_COPY_RELEASE_QA.md).

## Changes

- Native file selectors have explicit border/background/padding, hover/focus and disabled styling in shared CSS.
  Video/model/dataset upload inputs inherit it; hidden inputs retain their existing custom controls.
- Admin sample video panel offers Upload mới / Chọn từ kho của tôi. Existing source pages are scoped by
  the authenticated source API and paginated; picker includes only PRIVATE VIDEO, with readiness reasons.
  Copy is disabled without READY + FRAMES + DINO + CLIP. Original uploads remain available.
- New operator-only POST copy-to-samples verifies ownership and frozen feature input hashes, pins input,
  deduplicates existing SYSTEM sources by core/full SHA, then uses PREPARE_FRAMES sample_copy mode.
  No new job kind, queue, service, migration or policy defaults. Worker/API/FE must all be updated for rollout.
- Worker uses conditional server-side S3 COPY; managed multipart for large raw objects, bounded concurrency,
  no decoder/model compute or browser/VPS download. Source HEAD size/ETag and available SHA checksum/metadata
  are checked. Copies use attempt-specific SYSTEM keys and existing durable INTENT/WRITTEN receipts.
- Atomic fenced publication creates a hidden SYSTEM source, exact frames and byte-identical NPZ artifacts;
  manifest source ID changes while unit IDs, SHA and FieldKeys remain. GLOBAL_ONLY reservations are persisted
  once and unused holds released. Original source/sessions remain unchanged; sample publication stays explicit.
- Failed/cancelled attempts retain holds until the existing supervisor proves stop and DELETE+HEAD cleanup.
  Duplicate queued attempts do not overwrite/delete a winning sample. UI supports progress/cancel/retry;
  operations metadata preserves copy tracking after reload and catalog refresh on observed completion.

## Verification

- PostgreSQL16 localhost55433 + S3 moto, no production tests/data mutations. Regression across sample copy,
  frames, existing frame preparation, object receipts, operations and public API: **78 passed**.
- Focused final sample copy suite: **10 passed**, covers API auth/idempotency, unchanged private source/session, copied bytes
  and exact identities, hidden/public access, deletion independence, queued duplicates, cancellation between
  batches, failed writes/cleanup, GLOBAL accounting on/off and core/full-SHA mismatch.
- Moto omits optional HEAD checksums; the checksum mismatch case supplies the response checksum explicitly.
  S3 object copies themselves execute against moto. This does not claim a real AWS large multipart benchmark.
- Frontend **63 tests passed**, TypeScript passed, local build and explicit `VITE_RAV_MODE=pilot` build passed.
  Verified the pilot bundle contains copy-to-samples. Vite `--mode pilot` alone does not set VITE_RAV_MODE;
  the actual Docker build already exports it. Existing bundle size warning remains.
- Ruff src/tests passed. Browser local fixture uses real SampleCopyPicker + upload CSS: native buttons visibly
  styled, incomplete CLIP source disabled, ready source callback works. Screenshot `output/sample-copy-ui.png`.
  This is component QA, not authenticated full admin browser acceptance or production S3 copy acceptance.

## Remaining release gates

No actual admin data was published. No migration or quota changes needed, but both worker and FE/API need
rollout together. Cloud conditional/multipart S3 permissions/throughput and large-video capacity remain
release acceptance work. SYSTEM copies use additional S3 storage; they avoid repeated preprocessing,
not duplicate storage bytes. Existing private sessions continue to use their original source.

## Picker UI refinement (2026-10-17, after release)

Private-video cards now reuse MediaImg/mediaRef lazy thumbnails, show duration and unit count,
clamp long names with a full-name tooltip, and show a placeholder before frames are ready.
Copy buttons use primary styling; reload/pagination have borders, and source-mode buttons have
visible selected styling and icons. No backend or copy semantics changed.

TypeScript,63 frontend tests and pilot build passed. Browser component fixture verified a real local
thumbnail through seeded media cache, long filenames, missing-frame placeholder/disabled copy,
copy callback and warm/dark styling. No production request was sent by the fixture.
Screenshot: `output/sample-picker-thumbnail-ui.png`. Temporary fixture removed before commit.
This refinement has not been pushed or deployed.
