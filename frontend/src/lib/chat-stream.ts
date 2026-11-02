import { api, ApiError, errorFromResponse, type ChatRequest, type ChatResponse } from "./api/client";
import type { components } from "./api/schema";

export type ChatStreamEvent = components["schemas"]["StreamEvent"];
export type ChatTransport = "stream" | "standard";

class StreamUnavailable extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseEvent(frame: string, previousSequence: number, requestId: string | null): ChatStreamEvent | null {
  const lines = frame.split(/\r?\n/);
  const data = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
  if (!data) return null;
  let parsed: unknown;
  try { parsed = JSON.parse(data); } catch { throw new ApiError("STREAM_INVALID", "The workflow stream contained invalid data."); }
  if (!isRecord(parsed) || typeof parsed.event_type !== "string" || typeof parsed.sequence !== "number" ||
      typeof parsed.request_id !== "string" || !isRecord(parsed.payload) ||
      parsed.sequence !== previousSequence + 1 || (requestId !== null && parsed.request_id !== requestId)) {
    throw new ApiError("STREAM_INVALID", "The workflow stream contained an invalid event.");
  }
  const allowed = ["workflow_started", "intent_classified", "agent_started", "agent_completed", "evidence_verified", "answer_completed", "workflow_failed"];
  if (!allowed.includes(parsed.event_type)) throw new ApiError("STREAM_INVALID", "The workflow stream contained an unknown event.");
  return parsed as ChatStreamEvent;
}

async function readStream(
  body: ChatRequest, signal: AbortSignal, onEvent: (event: ChatStreamEvent) => void,
): Promise<ChatResponse> {
  let response: Response;
  try {
    response = await fetch("/api/v1/chat/stream", {
      method: "POST",
      headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
      body: JSON.stringify(body), signal, cache: "no-store",
    });
  } catch (cause) {
    if (signal.aborted || (cause instanceof Error && cause.name === "AbortError")) throw cause;
    throw new ApiError("API_UNREACHABLE", "The API is unavailable. Check the backend connection.");
  }
  if ([404, 405, 501].includes(response.status)) throw new StreamUnavailable();
  if (!response.ok) throw await errorFromResponse(response);
  if (!response.headers.get("content-type")?.includes("text/event-stream") || !response.body) {
    throw new ApiError("STREAM_INVALID", "The API did not return a workflow event stream.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sequence = 0;
  let requestId: string | null = null;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let separator: RegExpExecArray | null;
      while ((separator = /\r?\n\r?\n/.exec(buffer)) !== null) {
        const frame = buffer.slice(0, separator.index);
        buffer = buffer.slice(separator.index + separator[0].length);
        const event = parseEvent(frame, sequence, requestId);
        if (!event) continue;
        sequence = event.sequence;
        requestId = event.request_id;
        if (event.event_type === "workflow_started" && event.payload.repository_id !== body.repository_id) {
          throw new ApiError("RETRIEVAL_SCOPE_MISMATCH", "Workflow left the requested repository.");
        }
        onEvent(event);
        if (event.event_type === "workflow_failed") {
          throw new ApiError(
            typeof event.payload.code === "string" ? event.payload.code : "WORKFLOW_FAILED",
            typeof event.payload.message === "string" ? event.payload.message : "Workflow failed.",
            event.request_id,
          );
        }
        if (event.event_type === "answer_completed") {
          const answer = event.payload as ChatResponse;
          if (answer.repository_id !== body.repository_id || answer.request_id !== event.request_id ||
              typeof answer.answer !== "string" || !Array.isArray(answer.citations)) {
            throw new ApiError("RETRIEVAL_SCOPE_MISMATCH", "Answer left the requested repository.");
          }
          return answer;
        }
      }
      if (done) throw new ApiError("STREAM_INCOMPLETE", "The workflow stream ended before an answer arrived.");
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export async function submitChat(
  body: ChatRequest, signal: AbortSignal, onEvent: (event: ChatStreamEvent) => void,
): Promise<{ answer: ChatResponse; transport: ChatTransport }> {
  try {
    return { answer: await readStream(body, signal, onEvent), transport: "stream" };
  } catch (cause) {
    if (!(cause instanceof StreamUnavailable)) throw cause;
    const answer = await api.chat(body, signal);
    if (answer.repository_id !== body.repository_id) {
      throw new ApiError("RETRIEVAL_SCOPE_MISMATCH", "Answer left the requested repository.");
    }
    return { answer, transport: "standard" };
  }
}
