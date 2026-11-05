# Workflow reliability

Phase 7.2 uses the configured managed PostgreSQL database. Alembic revision `20261103_0007` adds `workflow_runs` and indexing cancellation state. No local database or task broker is required.

## Request workflows

Chat and agent analysis create a workflow row before executing the LangGraph graph. The row records `workflow_id`, `request_id`, `repository_id`, type, status, timestamps, error code, attempt count, cancellation flag, and a small allowlisted JSON summary. Question text is represented only by length and SHA-256 fingerprint. Source content, prompts, citations, and secrets are not stored in workflow rows.

The graph is executed within one HTTP request. A LangGraph PostgreSQL checkpointer is intentionally not installed: the graph's full state includes question text, source evidence, answer text, callback objects, and provider results. Persisting that state would violate the data minimization requirement. These requests cannot resume at a graph node after a process crash. On application startup, request runs last updated more than 15 minutes ago are marked `FAILED` with `WORKFLOW_INTERRUPTED` or `CANCELLED` if cancellation was requested. A client can submit a new request. This is safe recovery, not durable graph execution. Runs younger than the threshold remain `RUNNING` until a later startup. The workflow ID equals the request ID for chat and agent analysis; the streaming API emits that ID in its first event.

`GET /api/v1/workflows/{workflow_id}?repository_id=...` and `POST /api/v1/workflows/{workflow_id}/cancel?repository_id=...` require the matching repository ID. There is no user identity or role system yet; the repository ID is a scope check, not user authentication. Cancellation is checked before each attempt and between streamed graph nodes. It cannot interrupt a provider call already in progress. Late completion observes the persisted cancellation flag and remains `CANCELLED`.

Provider timeouts and rate limits and recoverable database connection failures receive bounded exponential retries (three attempts, starting at 250 ms, capped at five seconds). Validation, missing repositories, authorization, unsupported operations, and ordinary provider errors are not retried. The Gemini adapter and indexing embedding worker also apply their own bounded retries; callers should avoid adding further layers.

## Indexing jobs

The existing `index_jobs` table remains authoritative. One active job per repository is enforced with a partial unique index. Workers claim with `FOR UPDATE SKIP LOCKED`, a lease token, and an expiry. A second worker cannot claim an unexpired job. After expiry, a new worker reclaims the job, verifies the source snapshot, reuses staged chunks and embeddings at resumable stages, and performs a single atomic active-snapshot promotion. A stale worker cannot write after losing its lease. Provider work already in flight may have happened twice after a lease expiry, but the active snapshot is promoted at most once by the current lease holder.

An index workflow row mirrors the job status. The worker updates it at claim and completion. The workflow GET endpoint reconciles from the authoritative job, including after a temporary mirror update failure. The index job API remains the authoritative status endpoint. A failure between enqueueing a job and creating its workflow row can leave a queued index job without a workflow row; the job can still be processed and inspected through the index job API.

Index cancellation is durable. A queued job becomes `CANCELLED` immediately. A running worker checks the flag between stages and batches; it cannot stop an active provider call. Staged data is removed when cancellation is finalized, while the active snapshot remains intact.
