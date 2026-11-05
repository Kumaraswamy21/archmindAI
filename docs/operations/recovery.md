# Recovery operations

1. Apply Alembic revision `20261103_0007` to the configured managed PostgreSQL database before starting the Phase 7.2 API or worker. Use the existing migration procedure in `backend/README.md`. The application does not create tables automatically.
2. Restart the API. At startup it marks non-index request workflows older than 15 minutes as `FAILED` (`WORKFLOW_INTERRUPTED`), or `CANCELLED` if cancellation had been requested. If PostgreSQL is unavailable, requests that need workflow storage return `503`; recovery can run at the next startup.
3. Restart the indexing worker (`python -m app.modules.project_indexing.worker` from `backend`). Queued jobs are claimable immediately. A running job becomes claimable when its 180-second lease expires. The replacement verifies the source snapshot before resuming; changed sources fail with `SOURCE_CHANGED` and preserve the old active snapshot.
4. Inspect `GET /api/v1/repositories/{repository_id}/index/jobs/{job_id}` for authoritative indexing status. Inspect `GET /api/v1/workflows/{workflow_id}?repository_id={repository_id}` for request workflow status and the reconciled index mirror.
5. For cancellation, call the relevant `/cancel` endpoint and poll status. A provider call already in progress may finish before cancellation is observed.

Do not run multiple worker processes against the same repository expecting parallel speedup. The lease and active-job constraint serialize a repository's index jobs. If a worker is unresponsive, let the lease expire and start a replacement; do not modify job rows manually. The worker verifies ownership before every staged write and final promotion.

For a controlled restart test, enqueue an index job in an isolated remote PostgreSQL schema, claim it, stop that worker, expire its test lease, then let a replacement worker process it. Confirm the job reaches `COMPLETED`, the active snapshot is present, and a second worker cannot process the same completed job. This procedure is automated with deterministic embeddings in `test_project_indexing_remote.py` when `ARCHMIND_RUN_PROJECT_INDEX_REMOTE_TEST=1` and remote credentials are supplied.
