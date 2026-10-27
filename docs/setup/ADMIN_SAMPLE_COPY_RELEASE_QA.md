# Admin prepared sample copy — release 2026-10-17

User authorized push and deploy. Source `c32b7240f160abc4ef9c13e8cda700a09cdbd897`
was pushed to origin/main. [Implementation and local QA](ADMIN_SAMPLE_COPY_QA.md), CONTRACTS §21.38.

## Rollout

- Preflight: no active jobs/uploads or occupied slots; old worker healthy; VPS disk free35.84 GiB.
- Built worker and combined FE/API from committed source, with Docker frontend tests/tsc/build.
  Candidate smoke checked live schema compatibility, worker copy module, actual pilot API OpenAPI route,
  compiled pilot bundle and file-selector-button CSS. Local API entrypoint is not the pilot runtime entrypoint.
- Published to the existing authorized Docker Hub repository. Preserved rollback image/config backups.
  Rechecked idle, replaced worker first, waited healthy, then deployed FE/API on Render.
- Render deploy `dep-db9mu3m1egvs73fsdr3g` live, combined image:
  `docker.io/tonmeu/rav26@sha256:a895dcc9f83a1827474a89c17fd94a31642fd735dd5c59dfc8d2f1e240fcb6b9`.
- Worker image `sha256:b8582ec0e7749cc4acd4c5a45caf33a6e242345f865be76b8275b16e52d9cc44`.
  No migrations, quota/policy, credentials or ingress changes.

## Cloud evidence

- Ran the candidate's actual PilotStorage.copy_verified on real AWS S3 with dedicated disposable QA objects.
  4 KiB single copy0.49s;129 MiB managed multipart copy2.15s. Downloaded copied bytes for full SHA verification;
  both matched. Deleted exact QA keys and verified absence. Timings measure COPY only, not upload/download
  or a full admin job. No user's private source was copied or published.
- Live authenticated admin source/model catalog, operations and logs API returned successfully.
  New copy endpoint returns404 for operator requesting a nonexistent source and403 for an ordinary user.
  No admin permissions were added to the QA account.
- Live application serves the new copy endpoint bundle and native file-button CSS. Readiness HTTP200;
  worker healthy. Browser opened the real application successfully under its existing ordinary-user session.

## Limits

The complete operator copy job, hidden SYSTEM publication, retry/cancel, unchanged original sessions and
deletion independence passed isolated PostgreSQL/S3 tests documented in local QA. They have not been
repeated as one authenticated full admin cloud/browser flow in this release. The available QA account is
not an operator; real private admin sources were deliberately not used for that acceptance.
Large multi-GiB video capacity, thousands of object copies and concurrent user load remain unbenchmarked.
No model inference or frame decoding is required by this copy path; independent copies consume S3 storage.
The new copy starts hidden and still needs the admin's explicit publication action.

Artifacts (gitignored): `output/sample-copy-release/{preflight,candidate-smoke,s3-copy-smoke,live-smoke,release}.json`.
Rollback backup: worker `output/pre-sample-copy-20261017-c32b7240`; protected old Render config is retained locally.
