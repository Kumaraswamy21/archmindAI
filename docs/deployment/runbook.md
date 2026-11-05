# Deployment runbook and smoke checks

## Preflight

1. Resolve the authentication and authorization blocker in [production.md](production.md). Confirm anonymous repository, source, chat, indexing, and workflow requests are denied server-side. Test cross-user IDs.
2. Verify the selected host's current runtime, persistent disk, request-size, idle-timeout, worker, and free-tier limits. Check database backups and restore access. Keep the API and worker on the same approved source mount.
3. Review environment values against the two `.env.example` files, without printing secrets. Verify the exact frontend origin and backend URL. Confirm no secret uses a `NEXT_PUBLIC_` prefix.
4. Run CI checks and tests, take a database backup, apply reviewed migrations manually, then start the worker, API, and frontend using [production.md](production.md).
5. Wait for `/api/v1/health` and `/api/v1/ready` to return HTTP 200. Health only proves the API process responds; readiness checks database migration and pgvector state and the presence of a provider key.

## Executable smoke procedure

Run the script from `backend` after deploying an authenticated build. The default invocation performs **no writes**. It checks liveness, readiness, anonymous access denial, and an authenticated repository listing. It currently fails at authentication, correctly reflecting the Phase 7.3 blocker.

```sh
export ARCHMIND_SMOKE_API_URL='https://api.example.com'
export ARCHMIND_SMOKE_BEARER_TOKEN='set-from-secure-operator-session'
python scripts/smoke_production.py
```

For functional checks, configure a **dedicated disposable approved Java repository alias** containing known source and an answerable question. Ensure a worker is running. The explicit flag permits repository registration and indexing writes; the script never deletes data. It polls the job, checks hybrid retrieval, runs agent analysis, validates verified citations against scoped source-file access, and calls the frontend API proxy.

```sh
export ARCHMIND_SMOKE_TEST_ALIAS='approved-smoke-repository'
export ARCHMIND_SMOKE_QUESTION='Where is the smoke test service implemented?'
export ARCHMIND_SMOKE_FRONTEND_URL='https://app.example.com'
# Set ARCHMIND_SMOKE_SESSION_COOKIE if the frontend uses a server-managed session.
python scripts/smoke_production.py --functional --allow-test-writes
```

The bearer token and optional session cookie are read from the process environment and are never printed. Use a least-privilege test account and rotate/remove the temporary token after the run. The current version has no authentication system; do not run the functional procedure against a public deployment until that gap is closed.

## Routine checks

- API liveness: `/api/v1/health`.
- API readiness: `/api/v1/ready`; HTTP 503 means missing configuration, unavailable database, missing extension, or pending migration.
- Worker: supervise the indexing process and watch job status. A queued job needs a running worker. An expired lease is reclaimable; an active provider call cannot be interrupted instantly.
- Logs: request logs are JSON lines with request ID, method, path, status, and duration. They omit query strings, bodies, prompts, source contents, and credentials. Investigate failures by request ID.
- Database: monitor connection limits and backup success with the managed provider. The application does not schedule backups.

If readiness fails after a migration, stop traffic and use [rollback.md](rollback.md). Do not run Alembic downgrades automatically.
