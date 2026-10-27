# Read-only sample references — QA 2026-10-17

CONTRACTS §21.39 replaces new physical copies from §21.38. User approved implementation and later deployment.

## Implementation

- Own prepared PRIVATE video → operator-only reference-as-sample API → hidden protected SYSTEM source.
  Narrow SECURITY DEFINER RPC creates frame/feature metadata aliases using INSERT SELECT with the same S3
  keys and exact unit/SHA identities. No S3 I/O, AI, new job, storage hold or second persisted-byte charge.
- Migration027 adds an immutable backing reference and REFERENCE source pin; source/frame/feature guards
  block destructive mutation. Metadata aliases have distinct source/frame/artifact IDs and existing auth paths.
  Physical object-key uniqueness becomes an index for aliases; attempt-object receipt uniqueness remains.
  No broad INSERT grant to API; RPC and reference table are inaccessible to anon/authenticated roles.
- Backing PRIVATE ownership and existing sessions stay unchanged. Publication is explicit; hide preserves
  existing-session access. Users cannot delete SYSTEM sources or access the backing PRIVATE source directly.
  The backing source cannot be deleted while referenced, including hidden samples. Pilot has no unshare/GC.
- Old copy API and sample_copy retry return410; legacy handlers remain for safe stop/cleanup.
  The explicitly authorized live job07ca489e-3d6a-4e67-883f-3f16229289f8 was cancelled through owner/CAS API.
  Verified CANCELLED at2176/13423, CLOSED cleanup_done=true, all2176 receipts DELETED, holds FINAL/remaining0.
- FE says Thêm vào kho mẫu, read-only reference/no file copy, keeps thumbnail/readiness and warns about
  backing deletion lock. No legacy copy retry button; handles an older connected API with a clear message.

## Verification

- Isolated PostgreSQL16 port55433 and S3 moto: **85 regression tests passed** across references, legacy copies,
  frames/prepare, receipts, operations and HTTP API. After final migration guard and additional cases,
  **8 focused reference tests passed**. No production DB used for pytest.
- Tests cover no S3 writes/new jobs/ledger changes, accounting on/off, auth/owner/readiness/idempotency,
  SHA collision, exact shared keys, separate metadata identity, hidden/published access, shared thumbnail
  request/readback, real worker diversity proposal over shared embeddings, source deletion pin, SQL guards,
  RPC/table grants and cleanup of a failed legacy attempt without deleting shared backing keys.
- Ruff src/tests, TypeScript, **63 frontend tests**, pilot build passed. Existing bundle warning remains.
- Browser opened actual local FE against unchanged cloud reads: latest labels, thumbnail cards and cancelled
  legacy job visible; no reference/publication request sent on human data. Screenshot output/sample-reference-ui.png.

## Release gate

Requires migration027 plus FE/API and worker rollout. Code tests do not imply this feature is live.
User subsequently requested deployment; release identities, cloud acceptance and limits will be recorded below.
No automatic publication of the human admin's data or Git push is implied by deployment.

Initial cloud migration attempt was rejected by the receipt action constraint:027 had omitted the
INTERACTIVE_SAVE action introduced in013. The entire transaction rolled back; no runtime was changed.
Corrected027 before any successful application, preserving the existing action and adding a regression
case for checkpoint receipts. Final focused suite:9 passed; separate interactive/checkpoint regression
suite:10 passed, Ruff passed before reattempt. The initial candidate921d2b19 must not be rolled out.

## Live release

- Source `dd13f6633a6e65cb9af8a949f9e6d7eb9fa6a833`; deployment requested by user, Git not pushed.
  Built new worker/combined images after the migration compatibility fix. Docker verified pilot FE build/tests
  and candidate API route/worker module. Preflight idle, all slots free, no uploads; VPS37.03 GiB free.
- Applied only027 transactionally, checksum `9840f63a94acaeeaebd02b4996618429a87bf93552af8e2d2bf5b0109772d77b`;
  prior migration checksums unchanged and RPC/table grants checked. Pre-migration affected constraint
  definitions and migration records saved in protected release output. No row deletion or quota change.
- Worker replaced first and healthy:
  `sha256:d9b1bc9bb8fc21dff698d015d52842fe25c30fc7f5594e051e25114a07f9a741`.
- Render deploy `dep-db9no2nbtjjs73cdc2m0` live, FE/API image:
  `docker.io/tonmeu/rav26@sha256:25d81793ea30db93800ac74036175c689ec2624e79f7aed957b13a844e212e2b`.
  Live identities/readiness and unchanged Render environment verified. Previous image/config backups retained.
  Rollback images can read aliases; REFERENCE pins and DB guards still protect backing files. Do not roll
  back027 by restoring global key uniqueness after aliases exist; use a forward migration for schema repair.
- Used exactly the source selected by the user in cancelled job07ca489e..., source
  `795f7727-803d-4d0f-a2dd-d836255d6dfd`, to create hidden sample
  `5b1a2fcf-a192-548d-801d-301be51e27b0`. No publication action was performed.
- Single POST5.72s for6710 units:13420 frame/thumbnail metadata aliases plus2 feature aliases, all exact
  backing object keys/SHA identities reused; raw key identical. No new job and storage ledger unchanged;
  original source immutable fields unchanged. Idempotent replay matched the original response.
- Ordinary QA account denied both hidden sample and PRIVATE backing reads; reference creation denied403;
  retired copy route returned410. Live FE bundle contains the reference endpoint; health ready200.
- Browser using the local FE/cloud API confirmed the new SYSTEM entry is hidden/READY, all FRAMES/DINO/CLIP
  ready and the publication button remains available to the admin. Screenshot:
  `output/sample-reference-release/catalog-hidden.png`; no publication or user decisions changed.

Artifacts (gitignored): `output/sample-reference-release/{preflight,candidate-smoke,migration,live-smoke,release}.json`.
The5.72s observation is one end-to-end API call, not a multi-user capacity benchmark. No automatic unshare,
backing garbage collection, model inference or public-user proposal was run on the human admin's new sample.
Public/shared proposal and hide/existing-session behavior were verified on isolated QA data.
