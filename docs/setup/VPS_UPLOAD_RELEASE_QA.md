# VPS upload release QA — 2026-10-15

Release source: `1a5ec82f03e0c3b8a8cb8b6e031c047230c08008`. Deploy scope: VPS upload → S3 canonical → frames READY.

## Live identities

- Render: `c4-app-026`, deploy `dep-db88abmq1p3s739vhd30`.
- Combined image: `docker.io/tonmeu/rav26@sha256:b680f46f064b85ff1e8b47f6850ef6202d457137a5f334492ce0953d5128287d`.
- Worker image: `sha256:70623e31badd96ab62ab1249c4f38e597a356b744237fd1144b2234c267a250b`.
- Upload HTTPS: `https://rav26-upload.161.97.164.25.sslip.io`. Public ACME certificate verified; Caddy verifies pinned upstream certificate.
- DB: migrations 015–020 applied; new immutable policy `vps-upload-20261015`.
- Configured ceilings: file 5 GiB, duration 60 minutes, upload capability 4 hours, upload/frames run deadline 2 hours.
- One active VPS upload globally; storage accounting disabled for new jobs, historical accounting receipts preserved.
- Existing POST/MULTIPART paths retained; previous image/env/policy available for rollback.

## Review fixes before rollout

1. Render complete/seal/abort/sweep use HTTPS control; filesystem operations execute on Contabo.
2. Browser capability cannot call purpose-bound control, append after seal, or recreate aborted files.
3. No default/dev/JWT signing secret. Explicit shared secret required; TLS is verified.
4. Shared durable append/status/seal/cleanup locked across threads/processes; HTTP body spool bounded to 16 MiB/request.
5. Cleanup runs after committed publication/terminal outcome, not inside publication transaction.
6. Worker resolves upload UUID under configured upload directory; Render does not supply trusted filesystem paths.
7. Render does not mount binary upload routes when remote VPS is configured.
8. Corrupt metadata fails without truncating acknowledged bytes; browser validates acknowledgements and bounds conflict retries.

## Fresh checks

- 46 upload/delete/storage/VPS regressions passed on real isolated PostgreSQL, before final API routing/import guards.
- 15 split-host/concurrent/capability/interactive/multipart checks passed at the same checkpoint.
- Final source: all 15 VPS release/upload tests passed on real isolated PostgreSQL, zero skips.
- Ruff passed; TypeScript passed; 37 frontend tests passed including three new VPS transfer tests.
- Linux candidate: real ASGI streaming, locked disk append, seal and cleanup passed without production DB/data.
- Early Linux TestClient probe could not run because the production image intentionally lacks developer HTTP test dependencies;
  final Linux smoke used the native ASGI interface. No test dependencies were added to production.

## Cloud smoke

Scope: authenticated Render HTTP + browser-style public TLS/CORS requests + real Supabase/S3/Contabo.
Not a browser file-picker acceptance run.

- QA upload: `738ab687-8257-4e36-8411-a871d53d78da`; source `7b100665-5460-4802-a342-8292828a33a1`; job `01995dd3-a250-4390-b7ae-e7626e886598`.
- Recorded dashcam input: **37,541,050 bytes (35.80 MiB)**.
- Input and canonical metadata SHA-256: `494b921dfe5a9801313adcb483585cd4052addf64f6202a73c14597bb35bd8c6`.
- Browser CORS preflight passed. Durable resume offset: 16,777,216 bytes.
- Lost-response simulation: resend committed chunk → 409 with expected offset, then resume remaining bytes.
- Abort/replay test passed; capability could not recreate aborted upload.
- Transfer/retry phase: **25.609 s**, including deliberately sending the first chunk twice.
- Upload through READY: **165.14 s**. Source availability READY, **240 units / 480 frame objects**.
- Canonical S3 object length and recorded content SHA matched input; downloaded thumbnail SHA matched stored frame receipt.
- `.part` and `.meta` absent on VPS after publication. One QA source retained, existing user data unchanged.
- Render ready=200, upload HTTPS health=200, worker healthy. Worker container sampled at ~739–763 MiB / 6 GiB;
  these are samples, not a measured peak or a 5 GiB capacity benchmark.

## Rollback and limits

Rollback configs/worker image on VPS: `/opt/rav26-worker/output/pre-vps-20261015-1a5ec82f`.
Protected local release evidence: `output/vps-upload-release/` (not Git, no tokens printed).
Pre-migration pilot-table pg_dump: 1,458,358 bytes, SHA-256 `3d8e10c613ad9d43dc46e48dc529e1dd35b1e9f04da0fc4aa29b0ffd1acba2d2`.
This is a table backup, not a fresh full Supabase restore acceptance test.
Stop/drain before rollback; keep additive migrations and receipts; restore previous profiles/policy and compatible images/env.
Do not delete volumes, force FREE slots or drop schema to roll back.

Not tested here: actual 5 GiB/60-minute input, mixed-user sustained load, browser file picker, full GPU preparation,
PII/export acceptance. Source QA and acceptance for those features remain separate.
HTTPS currently uses an IP-based sslip.io hostname; a team-owned domain can replace it later with the same ingress contract.
Caddy TLS references: https://caddyserver.com/docs/automatic-https and
https://caddyserver.com/docs/caddyfile/directives/reverse_proxy .
