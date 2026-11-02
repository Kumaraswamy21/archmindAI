# AI chat workspace

## Request and transport

`/projects/{repository_id}/ai` checks `GET /api/v1/repositories/{repository_id}/index/status` before enabling the composer and again before each submission. Only `READY` coverage permits analysis. Each submitted question sends the selected repository UUID, trimmed message, and `conversation_id: null`. The local message list is a page-session display only; the backend stores no conversation turns and receives no previous message as prompt memory.

The client prefers `POST /api/v1/chat/stream` with `Accept: text/event-stream` and a fetch `ReadableStream` reader. It decodes frames across arbitrary network chunks, checks ordered sequence numbers and request IDs, validates the terminal repository ID, and requires a terminal `answer_completed` or `workflow_failed` event. A workflow failure or incomplete stream is shown as an error and is never silently retried through the standard endpoint. If the stream route returns HTTP 404, 405, or 501, the client sends one request to `POST /api/v1/chat` and labels live activity unavailable. The user can retry a failed turn explicitly. A Cancel action aborts the active fetch; the backend receives a disconnect and stops at its next cancellation boundary.

The composer prevents concurrent duplicate submissions. Enter submits, Shift+Enter adds a line, and suggested questions fill the composer. Markdown is rendered without raw HTML; external HTTP(S) links open separately with safe `rel` values. No chain-of-thought, system prompts, provider credentials, or raw progress payloads are displayed.

## Activity and citations

The activity panel renders only received `workflow_started`, `intent_classified`, `agent_started`, `agent_completed`, `evidence_verified`, `answer_completed`, and `workflow_failed` events. It shows intent, selected workflow, specialist execution, and verification metadata from those events. The final response also supplies a run summary and verification status. Standard nonstream responses show their final summary without inventing an event timeline.

Each citation is a button that opens the repository file listing and full-file content APIs using the cited repository-relative path. The backend enforces repository scoping and path safety. The source API reads the current checkout, which may differ from the indexed snapshot if source changed after indexing. Reindex to refresh cited locations.

## Citation source viewer

The source citation buttons open a resizable panel that keeps the chat conversation visible and mounted. The panel uses the repository file list and full-file content APIs, highlights the cited line range, and supports navigation between citations in the same answer. See [source explorer](source-explorer.md).

