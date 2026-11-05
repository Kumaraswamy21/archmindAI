# Frontend API contract (Phases 6.6.1–6.6.3)

All routes below are under `/api/v1` and use UUID repository IDs. The server reads source through administrator-configured aliases or uploaded ZIP archives; request bodies never accept absolute paths or Git URLs. With `DATABASE_URL` configured, registration writes a `repositories` row immediately and list/detail/source operations resolve the same ID after backend restart. Uploaded source files must also remain on the backend host (or shared storage). Apply the existing Alembic migrations before registration. Without a database URL, the Phase 1–3 in-memory behavior remains available but is not durable.

The tracked [OpenAPI export](openapi.json) matches the generated `/openapi.json` endpoint and can seed a typed frontend client.

For one-click source indexing, `POST /repositories/{repository_id}/index` returns HTTP 202 with a durable job. Poll `GET /repositories/{repository_id}/index/jobs/{job_id}` for stage progress, then `GET /repositories/{repository_id}/index/status` for active vector coverage. The API does not execute the job inside the request; the [project onboarding guide](../modules/project-onboarding.md) explains the separate worker and snapshot behavior.

The project workspace can recover a job after reload with `GET /repositories/{repository_id}/index/jobs/latest`. It returns the latest persisted job or HTTP 404 `INDEX_JOB_NOT_FOUND`. The endpoint is repository scoped and static `latest` is registered before the dynamic job ID route.

## Project management

| Method and path | Request | Success |
| --- | --- | --- |
| `GET /repositories/approved-aliases` | None | `{"aliases":["sample-ecommerce"]}`; only available, server-approved alias names |
| `POST /repositories` | `{"name":"Customer Portal","source_alias":"sample-ecommerce"}` | HTTP 201 project metadata |
| `POST /repositories/upload?name=Customer%20Portal` | Raw ZIP body with `Content-Type: application/zip`, up to 500 MB | HTTP 201 project metadata; supported source files are stored under `ARCHMIND_UPLOAD_ROOT` |
| `GET /repositories` | `limit` 1–100 (default 50), `offset` >= 0 (default 0) | `{"total":1,"limit":50,"offset":0,"repositories":[...]}` |
| `GET /repositories/{repository_id}` | UUID path parameter | Project metadata or HTTP 404 |

Project metadata has `repository_id`, `name`, `source_alias`, `status` (`REGISTERED`), and `registered_at` (UTC date-time). `name` is a display name: it may contain spaces and is separate from the source alias. Names are trimmed, 1–100 printable characters, and unique case-insensitively. One project may use a configured alias at a time. Existing duplicate historical rows remain readable; a new registration using their name or alias returns HTTP 409. List order is by registration time then UUID, so offset pagination is deterministic. The listing exposes persisted metadata even when a configured source has since become unavailable; source operations then return HTTP 503.

The static `/repositories/approved-aliases` route is registered before `/{repository_id}`. No response includes the server filesystem path, database credentials, or Gemini key.

## Shared errors

Expected application errors and request validation errors use this body:

```json
{
  "error": {
    "code": "REPOSITORY_NOT_FOUND",
    "message": "Repository not found.",
    "request_id": "a71eb734-5e29-4edb-b5c4-ec351994669b",
    "details": null
  }
}
```

`X-Request-ID` repeats `error.request_id`. For HTTP 422, `code` is `VALIDATION_ERROR` and `details` is a list of `location`, `message`, and `type` records. The error envelope applies to existing `/api/v1` routes as well. OpenAPI documents this 422 body. Registration errors include `REPOSITORY_ALIAS_NOT_FOUND` (404), `REPOSITORY_ALREADY_REGISTERED` (409), `REPOSITORY_SOURCE_UNAVAILABLE` (503), and `DATABASE_UNAVAILABLE` (503). Other existing route-specific codes remain intact.
Unknown routes and methods use `HTTP_ERROR`; unexpected server failures use a generic `INTERNAL_ERROR` without exception details.

## Browser configuration and security

Set `ARCHMIND_FRONTEND_ORIGINS` to a JSON list of exact HTTP(S) origins, for example `['http://localhost:3000']` expressed as valid JSON: `ARCHMIND_FRONTEND_ORIGINS='["http://localhost:3000"]'`. The default is `[]`. Wildcards, paths, query strings, and fragments are rejected at startup. Only configured origins receive CORS approval for GET and POST. The browser calls the API; it does not receive `DATABASE_URL` or `GEMINI_API_KEY`.

The backend never executes or writes analyzed repository source. Repository-scoped operations use a UUID and server-side alias resolution. The current API has no user authentication or tenant model, so expose it only within a trusted environment until access control is added.

## Existing analysis flow

After registration, use the managed `POST /repositories/{id}/index` job for whole-repository Java indexing and poll its job endpoint. Legacy projects without a managed snapshot can still index one Java file at a time with `POST /repositories/{id}/chunks/index`, followed by embeddings and a dependency scan. Search, ask, and chat routes use the stable repository ID. The API has no automatic Git synchronization.

## Unified AI chat

For live progress, `POST /api/v1/chat/stream` accepts the same request and emits SSE frames. Its terminal `answer_completed` payload is the complete `ChatResponse` below, including the same citation validation. See [streaming event frames and payloads](streaming-events.md). Use a streaming `fetch()` reader for this POST endpoint; native `EventSource` only supports GET. After streaming starts, a failure is a terminal `workflow_failed` frame with a public `code` and `message`.

`POST /api/v1/chat` uses the existing LangGraph supervisor, code/dependency/architecture specialists, and evidence verification. It does not start a separate LLM workflow.

Request:

```json
{
  "repository_id": "<uuid>",
  "message": "How is authentication implemented?",
  "conversation_id": null
}
```

`message` is trimmed and must contain 1–2,000 characters after trimming. `conversation_id` is an optional UUID. If omitted or null, the server generates one; if supplied, it echoes it. It is a correlation identifier only. The server stores no conversation turns and does not include previous messages in the prompt.

Successful response fields:

| Field | Type and meaning |
| --- | --- |
| `request_id` | UUID for this HTTP request and graph invocation; matches `X-Request-ID` |
| `conversation_id` | Correlation UUID, generated or echoed |
| `repository_id` | Requested repository UUID |
| `answer` | Final verified or corrected answer, or clarification/insufficient-evidence text |
| `citations` | Array of evidence-backed source locations |
| `agents_executed` | Ordered specialist names from the graph |
| `classified_intent` | `CODE_LOOKUP`, `CODE_EXPLANATION`, `DEPENDENCY_ANALYSIS`, `ARCHITECTURE_ANALYSIS`, or `UNKNOWN` |
| `verification_status` | `CITATION_VERIFIED`, `PARTIALLY_VERIFIED`, `INSUFFICIENT_EVIDENCE`, or `NOT_APPLICABLE` |
| `workflow_status` | `ANSWERED`, `INSUFFICIENT_EVIDENCE`, or `CLARIFICATION_REQUIRED` |
| `execution_duration_ms` | Nonnegative graph execution duration |
| `prompt_versions` | Object with `answer` and `classifier`; each is null or `{name, system, user}` |
| `model_identifier` | Generation model, classifier model when clarification used only classification, or null when no model was called |

Example of the complete response shape:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000001",
  "conversation_id": "00000000-0000-4000-8000-000000000002",
  "repository_id": "00000000-0000-4000-8000-000000000003",
  "answer": "The cited method checks the token before accepting the request [E1].",
  "citations": [{
    "evidence_id": "E1",
    "file_path": "src/main/java/example/AuthService.java",
    "start_line": 12,
    "end_line": 20,
    "qualified_symbol": "example.AuthService.validateToken"
  }],
  "agents_executed": ["code_analysis"],
  "classified_intent": "CODE_EXPLANATION",
  "verification_status": "CITATION_VERIFIED",
  "workflow_status": "ANSWERED",
  "execution_duration_ms": 143.2,
  "prompt_versions": {
    "answer": {"name": "code_analysis", "system": "1.1.0", "user": "1.1.0"},
    "classifier": null
  },
  "model_identifier": "gemini-3-flash-preview"
}
```

The values above illustrate the schema; they are not a recorded answer or performance measurement.

Every citation has `evidence_id`, repository-relative `file_path`, inclusive one-based `start_line` and `end_line`, and `qualified_symbol` when the evidence has one (otherwise null). The chat adapter rechecks every returned citation against the graph's request-local retrieved evidence: repository ID, path, and line containment must agree. The existing graph also checks claim markers and can remove unsupported claims once. `CITATION_VERIFIED` checks evidence linkage and bounds; it is not proof that every explanation is semantically correct.

The source API supports citation navigation with `GET /api/v1/repositories/{repository_id}/files/content?path=<file_path>&start_line=<start_line>&end_line=<end_line>`. Both line parameters are required together. The response returns that inclusive line slice in `content`, its `line_count`, and `start_line`, `end_line`, and `total_line_count`. An out-of-bounds range returns HTTP 416 `SOURCE_LINE_RANGE_INVALID`. Without line parameters, the API retains the full-file response. The source API reads the current approved checkout; if source changes after indexing, current lines may differ from the cited snapshot. Reindex to refresh citations.

Chat errors use the shared `error` envelope. An unavailable repository returns 404; invalid or empty messages return 422; missing embeddings return 409; provider and verification failures return controlled 429, 502, 503, or 504 responses. The prompt loader uses existing immutable releases; this phase adds no prompt template.
