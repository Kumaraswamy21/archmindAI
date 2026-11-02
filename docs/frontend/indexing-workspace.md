# Project indexing workspace

## Lifecycle

The project overview reads repository metadata, the latest durable job, current source-file count, and active vector coverage. `GET /api/v1/repositories/{id}/index/jobs/latest` restores job state after a page reload or when another client started the job. No browser storage or guessed status is used for recovery.

**Start Indexing** sends `POST /api/v1/repositories/{id}/index`. The API returns a queued job immediately; a separate backend worker must be running to claim it. The workspace polls `GET /api/v1/repositories/{id}/index/jobs/{job_id}` every three seconds while the job is `QUEUED` or `RUNNING`, and stops on `COMPLETED` or `FAILED`. An active-job HTTP 409 causes the workspace to load and monitor the latest job. A failed job exposes the backend's public error message and offers **Retry Failed Indexing**, which queues a new job. A completed job can be reindexed.

The timeline follows the backend's real stages: Discovering, Parsing, Chunking, Persisting, Embedding, Vector indexing, Dependency extraction, and Completed. A stage is marked done only when `completed_stages` includes it. Queued, running, failed, and waiting states use the job's persisted fields. No percentage or estimated completion time is calculated.

## Counts and analysis gate

Source-file count comes from `GET /repositories/{id}/files` and describes the current approved checkout. The managed worker indexes Java files; other discoverable language files may be included in this count. Chunk count, embedded chunk count, and vector status come from `GET /repositories/{id}/index/status` and describe the active snapshot. If the status endpoint is unavailable, counts display as unavailable rather than zero.

The **Open AI Workspace** action requires `index_status: READY`. Direct visits to the AI route check the same backend status before enabling its question form. The form uses the existing repository-scoped `POST /api/v1/chat` and shows source citations. `PARTIAL`, `PENDING`, `EMPTY`, `FAILED`, and unavailable coverage cannot start AI analysis from the frontend. If a reindex fails while an older complete snapshot remains active and `READY`, analysis can still use that active snapshot, as supported by the backend's atomic snapshot promotion.

The backend does not synchronize Git changes automatically. Reindex to capture a newer approved checkout. Source is read, never executed or modified by this UI.
