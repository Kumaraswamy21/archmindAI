import { afterEach, describe, expect, it, vi } from "vitest";
import { submitChat } from "@/lib/chat-stream";

const repositoryId = "11111111-1111-4111-8111-111111111111";
const requestId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const body = { repository_id: repositoryId, message: "Where is Service?", conversation_id: null };
const answer = {
  request_id: requestId, conversation_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  repository_id: repositoryId, answer: "Service is in `Service.java` [E1].",
  citations: [{ evidence_id: "E1", file_path: "src/Service.java", start_line: 2, end_line: 4, qualified_symbol: "Service.run" }],
  agents_executed: ["code_analysis"], classified_intent: "CODE_LOOKUP",
  verification_status: "CITATION_VERIFIED", workflow_status: "ANSWERED",
  execution_duration_ms: 12, prompt_versions: { answer: null, classifier: null }, model_identifier: null,
};

function frame(sequence: number, event_type: string, payload: Record<string, unknown>) {
  return `id: ${sequence}\nevent: ${event_type}\ndata: ${JSON.stringify({ request_id: requestId, event_type, sequence, timestamp: "2026-11-01T00:00:00Z", payload })}\n\n`;
}

function streamResponse(text: string, splitAt = text.length) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({ start(controller) {
    controller.enqueue(encoder.encode(text.slice(0, splitAt)));
    controller.enqueue(encoder.encode(text.slice(splitAt)));
    controller.close();
  } });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("POST SSE chat client", () => {
  it("parses split frames in order and returns the verified terminal answer", async () => {
    const frames = frame(1, "workflow_started", { repository_id: repositoryId }) +
      frame(2, "intent_classified", { intent: "CODE_LOOKUP", selected_workflow: "code_analysis" }) +
      frame(3, "agent_started", { agent: "code_analysis" }) +
      frame(4, "agent_completed", { agent: "code_analysis", evidence_count: 1 }) +
      frame(5, "evidence_verified", { verification_status: "CITATION_VERIFIED", correction_pass: false }) +
      frame(6, "answer_completed", answer);
    const fetcher = vi.fn().mockResolvedValue(streamResponse(frames, 37));
    vi.stubGlobal("fetch", fetcher);
    const events: string[] = [];
    const result = await submitChat(body, new AbortController().signal, event => events.push(event.event_type));
    expect(result.transport).toBe("stream");
    expect(result.answer.citations[0].file_path).toBe("src/Service.java");
    expect(events).toEqual(["workflow_started", "intent_classified", "agent_started", "agent_completed", "evidence_verified", "answer_completed"]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/chat/stream");
  });

  it("falls back only when the streaming route is unavailable", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response("", { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(answer), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const events: string[] = [];
    const result = await submitChat(body, new AbortController().signal, event => events.push(event.event_type));
    expect(result.transport).toBe("standard");
    expect(result.answer.answer).toContain("Service.java");
    expect(events).toEqual([]);
    expect(fetcher.mock.calls[1][0]).toBe("/api/v1/chat");
  });

  it("terminates on backend workflow failure without a duplicate request", async () => {
    const fetcher = vi.fn().mockResolvedValue(streamResponse(frame(1, "workflow_started", { repository_id: repositoryId }) + frame(2, "workflow_failed", { code: "RAG_PROVIDER_TIMEOUT", message: "Analysis timed out." })));
    vi.stubGlobal("fetch", fetcher);
    await expect(submitChat(body, new AbortController().signal, () => {})).rejects.toMatchObject({ code: "RAG_PROVIDER_TIMEOUT", message: "Analysis timed out." });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("rejects incomplete streams and cross-repository answers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(frame(1, "workflow_started", { repository_id: repositoryId }))));
    await expect(submitChat(body, new AbortController().signal, () => {})).rejects.toMatchObject({ code: "STREAM_INCOMPLETE" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(frame(1, "workflow_started", { repository_id: repositoryId }) + frame(2, "answer_completed", { ...answer, repository_id: "22222222-2222-4222-8222-222222222222" }))));
    await expect(submitChat(body, new AbortController().signal, () => {})).rejects.toMatchObject({ code: "RETRIEVAL_SCOPE_MISMATCH" });
  });

  it("propagates cancellation and never starts a fallback request", async () => {
    const controller = new AbortController();
    const fetcher = vi.fn().mockImplementation((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));
    vi.stubGlobal("fetch", fetcher);
    const pending = submitChat(body, controller.signal, () => {});
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
