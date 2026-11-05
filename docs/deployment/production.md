# Manual production deployment plan

**Deployment is blocked.** Phase 7.3 authentication and repository authorization was deferred. The API still permits anonymous access to registered repositories and source. `ARCHMIND_ENV=production` deliberately refuses startup until that work is implemented and reviewed. Do not expose the current API or frontend to the public Internet. This document is a preparation plan, not an approval to deploy.

## Required services and placement

- A Linux host or container service that can run one FastAPI web process and one continuously running indexing worker. An existing host is suitable; no additional paid service is assumed.
- A Node.js host for the Next.js server. It must be able to reach the backend using `ARCHMIND_API_URL`. Static-only hosting is unsuitable because the app has server route handlers and API rewrites.
- The existing managed PostgreSQL service with TLS and the `vector` extension. The API, worker, and migration job must reach the same database.
- The existing Gemini API credential. Langfuse is optional.
- Persistent source storage visible at the **same paths** to the API and index worker. Uploaded ZIP sources currently live on the backend filesystem. A multi-host setup requires a shared volume or a later object-storage design.

Hosting providers change free-tier limits, sleep behavior, request-size caps, background-worker support, and disk persistence. Verify the current terms and technical limits of any chosen host before allocating it. Do not assume a free tier supports an always-running worker or a 500 MiB upload.

## Environment configuration

Start from [backend/.env.example](../../backend/.env.example) and [frontend/.env.example](../../frontend/.env.example). Store real secrets in the host's secret manager, not in Git, browser variables, logs, or a build artifact.

| Service | Required for a future production deployment | Purpose |
| --- | --- | --- |
| Backend | `ARCHMIND_ENV=production` | Enables strict startup validation and, currently, the authentication blocker. |
| Backend | `DATABASE_URL`, `DATABASE_SSL_MODE=require` or stronger | Remote managed PostgreSQL; localhost and non-PostgreSQL URLs are rejected. |
| Backend and worker | `GEMINI_API_KEY` | Embedding, retrieval, and agent analysis. |
| Backend | `ARCHMIND_FRONTEND_ORIGINS` | JSON list of exact HTTPS browser origins. No wildcard. |
| Backend and worker | `ARCHMIND_UPLOAD_ROOT` | Existing absolute persistent directory. |
| Backend and worker | `ARCHMIND_APPROVED_REPOSITORY_ROOTS`, `ARCHMIND_REPOSITORY_ALIASES` | Only approved mounted source roots and aliases. Optional when using uploads only. |
| Frontend | `ARCHMIND_DEPLOY_ENV=production`, `ARCHMIND_API_URL` | Explicit server-side API origin at build and runtime. Nonlocal origins require HTTPS. |

Optional bounded settings include `ARCHMIND_JSON_BODY_LIMIT_BYTES` (default 1 MiB, maximum 10 MiB), discovery limits, `EMBEDDING_*`, `RAG_*`, `HYBRID_*`, released prompt versions, and `LANGFUSE_*`. An enabled Langfuse export requires both keys. No `NEXT_PUBLIC_` variable contains an API or database secret. Authentication settings are intentionally absent because authentication has not been implemented.

The backend distinguishes `development`, `test`, and `production` through `ARCHMIND_ENV`. Development permits partial configuration for local work. Test runs without cloud credentials unless a remote test is explicitly enabled. Production requires database, Gemini, HTTPS frontend origins, and persistent upload storage, then stops at the authentication blocker. The frontend uses a separate `ARCHMIND_DEPLOY_ENV`; the Next.js production build alone does not imply a public deployment profile.

## Manual build and startup sequence after the blocker is resolved

1. Provision a **separate** managed PostgreSQL database and confirm it supports the `vector` extension, TLS, backups, and the expected total connection count. The API pool permits 5 idle plus 2 overflow connections per process; the worker has its own pool. Leave headroom for migrations and provider maintenance. Do not use a local database.
2. Take and verify a restorable backup using the database provider's backup/snapshot service. Review the migration set and apply it manually from `backend`: `python -m alembic -c alembic.ini upgrade head`. Revision `20261030_0003` creates the `vector` extension if allowed; otherwise an administrator must enable it first. Revision `20261103_0007` adds workflow reliability tables.
3. Install backend dependencies with `python -m pip install -e .` and configure the backend and worker environment. The code host must contain any approved source tree; configure **deployed** absolute paths, not paths on a developer laptop. Run `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --limit-concurrency 100 --timeout-graceful-shutdown 60` behind TLS termination. Run `python -m app.modules.project_indexing.worker` as a separate supervised process.
4. On the Next.js host, run `npm ci`, set `ARCHMIND_DEPLOY_ENV=production` and `ARCHMIND_API_URL`, then run `npm run build` and `npm run start`. Keep the API origin identical at build and runtime because rewrites are build configuration. Configure the host to forward upload and SSE traffic without buffering the stream.
5. Check `GET /api/v1/health` for process liveness and `GET /api/v1/ready` for database connection, current Alembic head, pgvector, and provider configuration. Readiness does not call Gemini and therefore does not prove provider availability. Run the [smoke procedure](runbook.md) only after authentication is implemented.

JSON requests are capped before parsing at 1 MiB by default. ZIP uploads use a separate streamed 500 MiB limit; the front proxy and host must accept the intended upload size. Gemini calls default to 30-second timeouts with bounded retries, so an entire workflow may last longer than a single provider call. Set ingress and SSE idle timeouts accordingly. Graceful shutdown flushes traces and disposes the API connection pool; an interrupted workflow is recovered as failed on a later startup. Index jobs use leases and can be reclaimed by a replacement worker.
