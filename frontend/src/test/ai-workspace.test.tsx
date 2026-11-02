import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import AiWorkspacePage from "@/app/projects/[repositoryId]/ai/page";

const repositoryId = "11111111-1111-4111-8111-111111111111";
const requestId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const answer = {
  request_id: requestId, conversation_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  repository_id: repositoryId, answer: "**Grounded answer** in `Service.java` [E1].",
  verification_status: "CITATION_VERIFIED", workflow_status: "ANSWERED",
  agents_executed: ["code_analysis"], classified_intent: "CODE_LOOKUP",
  citations: [{ evidence_id: "E1", file_path: "src/Service.java", start_line: 1, end_line: 3, qualified_symbol: "Service.run" }],
  execution_duration_ms: 10, prompt_versions: { answer: null, classifier: null }, model_identifier: null,
};
const frame = (sequence: number, event_type: string, payload: object) => `id: ${sequence}\nevent: ${event_type}\ndata: ${JSON.stringify({ request_id: requestId, event_type, sequence, timestamp: "2026-11-01T00:00:00Z", payload })}\n\n`;
const stream = (text: string) => new Response(new ReadableStream({ start(controller) {
  controller.enqueue(new TextEncoder().encode(text)); controller.close();
} }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
vi.mock("next/navigation", () => ({ useParams: () => ({ repositoryId }) }));
afterEach(() => vi.unstubAllGlobals());

it("blocks direct AI workspace analysis until the backend reports READY", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ index_status: "PARTIAL" }), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  render(<AiWorkspacePage />);
  expect(await screen.findByText("Index required")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Ask about this repository" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("streams user and assistant messages, actual agent activity, markdown, and clickable citations", async () => {
  const events = frame(1, "workflow_started", { repository_id: repositoryId }) +
    frame(2, "intent_classified", { intent: "CODE_LOOKUP", selected_workflow: "code_analysis" }) +
    frame(3, "agent_started", { agent: "code_analysis" }) +
    frame(4, "agent_completed", { agent: "code_analysis", evidence_count: 1 }) +
    frame(5, "evidence_verified", { verification_status: "CITATION_VERIFIED", correction_pass: false }) +
    frame(6, "answer_completed", answer);
  const fetcher = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/index/status")) return Promise.resolve(new Response(JSON.stringify({ index_status: "READY" }), { status: 200 }));
    if (url.endsWith("/chat/stream") && init?.method === "POST") return Promise.resolve(stream(events));
    if (url.endsWith("/files")) return Promise.resolve(new Response(JSON.stringify({ repository_id: repositoryId, total_files: 1, files: [{ path: "src/Service.java", language: "java", size_bytes: 32 }] }), { status: 200 }));
    if (url.includes("/files/content?")) return Promise.resolve(new Response(JSON.stringify({ repository_id: repositoryId, file_path: "src/Service.java", language: "java", line_count: 3, content: "class Service {\n  void run() {}\n}", start_line: null, end_line: null }), { status: 200 }));
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<AiWorkspacePage />);
  const input = await screen.findByRole("textbox", { name: "Ask about this repository" });
  await waitFor(() => expect(input).toBeEnabled());
  await userEvent.setup().type(input, "Where is Service?");
  await userEvent.setup().click(screen.getByRole("button", { name: "Send question" }));
  expect(await screen.findByText("Grounded answer", { exact: false })).toBeInTheDocument();
  expect(screen.getByText("Code Analysis started")).toBeInTheDocument();
  expect(screen.getByText("Evidence verified")).toBeInTheDocument();
  expect(screen.getByText("CITATION VERIFIED")).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", { name: /E1 src\/Service.java/ }));
  expect(await screen.findByRole("dialog", { name: /src\/Service.java/ })).toBeInTheDocument();
  await waitFor(() => expect(document.querySelector('[data-line="2"] code')?.textContent).toBe("  void run() {}"));
  expect(document.querySelectorAll('[data-highlighted="true"]')).toHaveLength(3);
  expect(screen.getAllByText("Where is Service?").length).toBeGreaterThan(0);
  const post = fetcher.mock.calls.find(call => (call[1] as RequestInit | undefined)?.method === "POST");
  expect(JSON.parse(post?.[1]?.body as string).repository_id).toBe(repositoryId);
});

it("falls back to standard chat without inventing live activity", async () => {
  const fetcher = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith("/index/status")) return Promise.resolve(new Response(JSON.stringify({ index_status: "READY" }), { status: 200 }));
    if (url.endsWith("/chat/stream")) return Promise.resolve(new Response("", { status: 404 }));
    if (url.endsWith("/chat")) return Promise.resolve(new Response(JSON.stringify(answer), { status: 200 }));
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<AiWorkspacePage />);
  const input = await screen.findByRole("textbox", { name: "Ask about this repository" });
  await waitFor(() => expect(input).toBeEnabled());
  await userEvent.setup().type(input, "Where is Service?");
  await userEvent.setup().click(screen.getByRole("button", { name: "Send question" }));
  expect(await screen.findByText("Live events were unavailable. The standard chat endpoint returned the final answer.")).toBeInTheDocument();
  expect(screen.queryByText("Agent started")).not.toBeInTheDocument();
});

it("prevents duplicate submissions and lets the user cancel an active stream", async () => {
  const fetcher = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/index/status")) return Promise.resolve(new Response(JSON.stringify({ index_status: "READY" }), { status: 200 }));
    if (url.endsWith("/chat/stream")) return new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    });
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<AiWorkspacePage />);
  const input = await screen.findByRole("textbox", { name: "Ask about this repository" });
  await waitFor(() => expect(input).toBeEnabled());
  await userEvent.setup().type(input, "Where is Service?");
  const form = input.closest("form");
  if (!form) throw new Error("Missing chat form");
  fireEvent.submit(form); fireEvent.submit(form);
  await waitFor(() => expect(fetcher.mock.calls.filter(call => String(call[0]).endsWith("/chat/stream"))).toHaveLength(1));
  await userEvent.setup().click(screen.getByRole("button", { name: "Cancel" }));
  expect(await screen.findByText("Analysis cancelled.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry question" })).toBeEnabled();
});

it("shows a backend failure and retries the same question", async () => {
  let attempts = 0;
  const fetcher = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith("/index/status")) return Promise.resolve(new Response(JSON.stringify({ index_status: "READY" }), { status: 200 }));
    if (url.endsWith("/chat/stream")) {
      attempts += 1;
      return Promise.resolve(stream(frame(1, "workflow_started", { repository_id: repositoryId }) +
        (attempts === 1
          ? frame(2, "workflow_failed", { code: "RAG_PROVIDER_TIMEOUT", message: "Analysis timed out." })
          : frame(2, "answer_completed", answer))));
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  const view = render(<AiWorkspacePage />);
  const input = await screen.findByRole("textbox", { name: "Ask about this repository" });
  await waitFor(() => expect(input).toBeEnabled());
  await userEvent.setup().type(input, "Where is Service?");
  await userEvent.setup().click(screen.getByRole("button", { name: "Send question" }));
  expect(await screen.findByText("Analysis timed out.")).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", { name: "Retry question" }));
  expect(await screen.findByText("Grounded answer", { exact: false })).toBeInTheDocument();
  expect(view.container.querySelectorAll(".chat-user")).toHaveLength(1);
  expect(attempts).toBe(2);
});

it("rechecks index readiness before each question", async () => {
  let statusReads = 0;
  const fetcher = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith("/index/status")) return Promise.resolve(new Response(JSON.stringify({ index_status: ++statusReads === 1 ? "READY" : "PARTIAL" }), { status: 200 }));
    throw new Error(`Chat should remain gated: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<AiWorkspacePage />);
  const input = await screen.findByRole("textbox", { name: "Ask about this repository" });
  await waitFor(() => expect(input).toBeEnabled());
  await userEvent.setup().type(input, "Where is Service?");
  await userEvent.setup().click(screen.getByRole("button", { name: "Send question" }));
  expect(await screen.findByText(/index is no longer ready/)).toBeInTheDocument();
  expect(input).toBeDisabled();
  expect(fetcher).toHaveBeenCalledTimes(2);
});
