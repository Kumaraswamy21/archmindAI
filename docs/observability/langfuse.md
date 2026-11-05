# Langfuse observability

ArchMind can export traces of grounded answering and agent workflows to [Langfuse Cloud](https://cloud.langfuse.com). Tracing is optional, fail-open, and off by default. There is no self-hosted Langfuse process and no new database.

## Enable export

Set these variables in the backend environment (never in Git):

```sh
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
# LANGFUSE_HOST=https://cloud.langfuse.com
# LANGFUSE_TIMEOUT_SECONDS=2
```

`LANGFUSE_ENABLED=true` without both keys leaves tracing disabled and does not fail HTTP requests. The Python package is `langfuse` 3.x (`get_client` / `start_as_current_observation`). The application wraps that SDK so a later major upgrade stays isolated in `backend/app/core/observability/`.

## Trace tree

Root traces are created only for:

- `POST /api/v1/chat` and `POST /api/v1/chat/stream` (`chat`)
- `POST /api/v1/ask` (`ask`)
- `POST /api/v1/agents/analyze` (`agents.analyze`)

Streaming starts the `chat` span inside the worker thread so child spans stay on the same trace.

Child observations are created only when that stage actually runs:

1. HTTP / workflow root (`request_id`, `repository_id`, `workflow_type`)
2. Intent classification (`rules` or LLM generation with prompt versions)
3. Hybrid retrieval (`retrieval_strategy=hybrid_rrf`)
4. Reranking (`metadata_heuristic_v1`)
5. Specialist agent (`agent.code_analysis`, `agent.dependency_analysis`, or `agent.architecture_analysis`)
6. Gemini generation (model, latency, token usage only when the Gemini SDK supplies counts)
7. Query embedding, when semantic search runs under a live trace
8. Evidence verification (`verification_status`, unsupported claim count)
9. Redacted final-response summary (`citation_count`, confidence or workflow status)

Health checks and indexing jobs are not traced in this phase.

## Metadata

Recorded fields are allowlisted: `request_id`, `repository_id`, `workflow_type`, `agent_name`, `retrieval_strategy`, `embedding_model`, `generation_model`, prompt name and system/user versions, `latency_ms`, `error_code` / `error_category`, `intent`, `verification_status`, counts, and a SHA-256 fingerprint of the question (`question_length`, `question_sha256`). `conversation_id` is used as Langfuse `session_id` when the client sent one.

Token usage is copied from Gemini `usage_metadata` when those integers are present. Missing usage is omitted. Counts are never estimated.

## Privacy

Input and output capture is off. Traces must not contain:

- Gemini API keys, database URLs, or other secrets
- Full user questions
- System or user prompt bodies
- Source-code chunk text or embedding vectors
- Authentication tokens

Evidence is represented by IDs, relative paths, and counts only.

## Reliability

SDK construct, span, update, and flush errors are swallowed. Question answering continues with the existing HTTP error envelope. Flush uses the configured timeout on application shutdown.

## Limitations

### Phase 7.7 local validation status (5 November 2026)

The local `backend/.env` contains no `LANGFUSE_ENABLED`, `LANGFUSE_PUBLIC_KEY`, or `LANGFUSE_SECRET_KEY` setting. The running app therefore used the no-op tracer. No Cloud traces, dashboard panels, trace filters, token usage, or scores were verified. Add keys for an existing Langfuse Cloud project to the ignored backend environment and restart the API before attempting trace validation. Keep Git-tracked prompt templates as the source of truth.

For each live request, match `request_id` to a Langfuse root trace and inspect the child observations actually executed. Check safe repository, agent, retrieval, model, prompt-version, latency, usage, and error metadata. Inspect input/output fields for accidental question, source, prompt, or credential capture. Recall@5, MRR, citation validity, groundedness, and routing accuracy require evaluation data and explicit score export; Langfuse does not calculate these automatically for this app. Use supported trace filters and analytics if custom dashboard creation is unavailable.

- Langfuse Cloud is the only supported host. Do not point `LANGFUSE_HOST` at a local compose stack as part of this product.
- Indexing workers, ZIP upload, and standalone scan endpoints are out of scope.
- A disabled or misconfigured tracer records nothing; the UI does not show traces.
- Exact cosine search and managed PostgreSQL are unchanged.
