# Project onboarding and managed indexing (Phase 6.6.2)

Projects are registered through an administrator-approved alias as described in the [frontend contract](../api/frontend-contract.md). No client can supply a filesystem path or Git URL. After registration, the new indexing API queues one durable job for the repository. The API returns HTTP 202 immediately; a separate process running the same backend package performs the work.

## Start the API and worker

Configure managed `DATABASE_URL`, `GEMINI_API_KEY`, approved roots, and aliases. Review and apply Alembic revision `20261031_0006` explicitly. Start the API as before, then start one or more worker processes from `backend/` with the same environment:

```sh
set -a
source .env
set +a
.venv/bin/python -m app.modules.project_indexing.worker
```

The worker is a long-running CLI process. It polls the PostgreSQL queue; no FastAPI background task, Redis, Celery, local database, or new service is required. If no worker is running, a job remains `QUEUED`. A running worker has a renewable lease. After a process crash, another worker may reclaim an expired lease, clear that job's unfinished staging rows, and restart from `DISCOVERING`. A lease token prevents a displaced worker from promoting its stale work. Operators must run the worker under a process supervisor in deployment.

## API contract

| Method and path | Result |
| --- | --- |
| `POST /api/v1/repositories/{repository_id}/index` | HTTP 202 indexing job. HTTP 409 `INDEX_JOB_ACTIVE` when that repository already has a queued or running job. |
| `GET /api/v1/repositories/{repository_id}/index/jobs/{job_id}` | Repository-scoped job details. A job from another repository returns HTTP 404. |
| `GET /api/v1/repositories/{repository_id}/index/jobs/latest` | Latest persisted job for this repository, including after a browser refresh. Returns HTTP 404 when no job exists. |
| `GET /api/v1/repositories/{repository_id}/index/status` | Existing vector coverage response (`EMPTY`, `PENDING`, `PARTIAL`, `FAILED`, `READY`) for the currently active chunks. |

The job response has `job_id`, `repository_id`, `status` (`QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`), `current_stage`, `completed_stages`, `started_at`, `completed_at`, `error_code`, and `error_message`. Queued and running jobs have a null `completed_at`. Failed jobs retain the stage where they stopped. Error messages are fixed public descriptions; database, provider, and filesystem details are not returned. Retry a failed job by POSTing again. A partial unique PostgreSQL index prevents simultaneous active jobs for the same repository across API processes.

## Stages and snapshot rules

The worker records these transitions in PostgreSQL: `DISCOVERING → PARSING → CHUNKING → PERSISTING → EMBEDDING → VECTOR_INDEXING → DEPENDENCY_EXTRACTION → COMPLETED`. Discovery finds eligible source files. Parsing validates Java syntax and hashes exact UTF-8 source. Chunking uses the existing Tree-sitter symbol extractor and AST chunker and writes job-owned staging rows as it goes. Persisting checks that the committed staging count matches the chunks produced. Embedding calls the existing Gemini provider and verifies dimensions. Vector indexing validates that every staged chunk has a pgvector value of the configured dimension; the project continues to use exact cosine search rather than an approximate index. Dependency extraction uses the existing static Java extractor.

The `source_snapshot_id` is a SHA-256 digest of sorted Java paths and their exact source hashes. The worker checks the paths and hashes again before promotion. If source changes during a job, it fails with `SOURCE_CHANGED` and leaves the old snapshot active. The final promotion replaces chunks, vectors, and dependency edges and sets `repositories.active_snapshot_id` in one database transaction. Readers therefore see the previous complete snapshot or the new complete snapshot, never staged rows. An in-flight multi-query answer that crosses a promotion may fail its existing evidence consistency check rather than mix evidence. Repeating a job on unchanged source produces the same source snapshot ID and active chunk IDs. Previous job history remains; unfinished staging is removed on failure or recovery.

Managed projects reject the legacy per-file chunk indexing and dependency scan mutations with `MANAGED_INDEX_REQUIRED`. Existing non-managed projects may still use those APIs. This prevents manual writes from corrupting the committed source snapshot. The worker reads but never executes or edits source. Java is the only language included in this managed indexing snapshot; Python and TypeScript discovery remain available separately.

## Limits

The existing discovery file-count and file-size limits apply. The worker currently loads a job's staged chunks and dependency units into memory during embedding and promotion, so repository size should be bounded operationally. There is no Git polling or automatic reindex on commits. A source change immediately after the final verification may make the live checkout newer than the captured snapshot; the snapshot ID still identifies the exact bytes that were indexed. An API process and worker must use compatible embedding model and dimension settings.
