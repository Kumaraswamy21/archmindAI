# Chat workflow streaming events

`POST /api/v1/chat/stream` accepts the same JSON `ChatRequest` as `POST /api/v1/chat` and returns `text/event-stream`. A browser frontend should use `fetch()` with a streaming response reader because the native `EventSource` API cannot send this POST body. Send `Accept: text/event-stream` and `Content-Type: application/json`.

Each SSE frame has an `id` equal to its sequence, an `event` name, and one JSON `data` object:

```text
id: 1
event: workflow_started
data: {"request_id":"00000000-0000-4000-8000-000000000001","event_type":"workflow_started","sequence":1,"timestamp":"2026-11-01T00:00:00Z","payload":{"repository_id":"00000000-0000-4000-8000-000000000003"}}

```

`request_id` is the HTTP `X-Request-ID` when the application middleware supplies it. `sequence` starts at 1 and increases within this response. `timestamp` is UTC. The OpenAPI `StreamEvent` component describes the envelope; this document describes each event's payload. A stream has exactly one terminal event while the client remains connected.

| Event | When emitted | Payload |
| --- | --- | --- |
| `workflow_started` | The server opens the stream | `repository_id` |
| `intent_classified` | LangGraph classification node completes | `intent`, `selected_workflow` |
| `agent_started` | A routed specialist node starts | `agent` |
| `agent_completed` | That specialist node completes | `agent`, `evidence_count` |
| `evidence_verified` | An evidence verification node completes | `verification_status`, `correction_pass` |
| `answer_completed` | The graph and chat citation validation both succeed | The full `ChatResponse` object from the nonstreaming API |
| `workflow_failed` | The graph or final citation validation fails | `code`, `message` |

The graph may omit specialist and evidence events when it routes to clarification. A correction pass can emit a second `evidence_verified` event. Progress payloads contain only metadata; they do not contain source text, prompts, internal reasoning, or credentials. The final answer has the same verified citation fields and repository scoping as `POST /api/v1/chat`.

Request validation and unavailable chat configuration return the normal JSON error response before streaming begins. Once the SSE response starts, errors use a terminal `workflow_failed` event and the HTTP status remains 200. Provider timeouts retain their public error code, such as `RAG_PROVIDER_TIMEOUT`; unexpected errors use `WORKFLOW_FAILED` without exception details. A disconnected client causes the server to signal cancellation and stop at the next graph boundary; an already running provider call can finish before that signal takes effect. No conversation history is stored by this endpoint.
