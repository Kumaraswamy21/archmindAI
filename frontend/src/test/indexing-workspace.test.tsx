import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IndexingWorkspace } from "@/components/indexing-workspace";

vi.mock("next/link", () => ({ default: ({ href, children, ...props }: React.ComponentProps<"a">) => <a href={href} {...props}>{children}</a> }));

const firstId = "11111111-1111-4111-8111-111111111111";
const secondId = "22222222-2222-4222-8222-222222222222";
const firstJobId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const secondJobId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const error = (code: string, status: number) => json({ error: { code, message: "A public API error.", request_id: "trace" } }, status);
const coverage = (index_status: "EMPTY" | "READY") => ({ repository_id: firstId, total_chunks: index_status === "READY" ? 7 : 0, embedded_chunks: index_status === "READY" ? 7 : 0, pending_chunks: 0, failed_chunks: 0, index_status });
const job = (status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED", stage: string, completed: string[] = [], repository_id = firstId, job_id = firstJobId) => ({
  job_id, repository_id, status, current_stage: stage, completed_stages: completed,
  started_at: status === "QUEUED" ? null : "2026-11-01T00:00:00Z",
  completed_at: status === "COMPLETED" || status === "FAILED" ? "2026-11-01T00:01:00Z" : null,
  error_code: status === "FAILED" ? "EMBEDDING_PROVIDER_FAILED" : null,
  error_message: status === "FAILED" ? "Embedding generation failed." : null,
});

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function pollCallback(spy: ReturnType<typeof vi.spyOn>) {
  const entry = spy.mock.calls.findLast((call: unknown[]) => call[1] === 3000);
  if (!entry) throw new Error("No active polling interval");
  return entry[0] as () => Promise<void>;
}

describe("indexing workspace", () => {
  it("starts a job, renders real stages, and polls its persisted status", async () => {
    const intervals = vi.spyOn(window, "setInterval");
    const fetcher = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith("/index/jobs/latest")) return Promise.resolve(error("INDEX_JOB_NOT_FOUND", 404));
      if (url.endsWith("/index/status")) return Promise.resolve(json(coverage("EMPTY")));
      if (url.endsWith("/files")) return Promise.resolve(json({ total_files: 3, files: [] }));
      if (url.endsWith("/index") && init?.method === "POST") return Promise.resolve(json(job("QUEUED", "DISCOVERING"), 202));
      if (url.endsWith(`/index/jobs/${firstJobId}`)) return Promise.resolve(json(job("RUNNING", "PARSING", ["DISCOVERING"])));
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetcher);
    render(<IndexingWorkspace repositoryId={firstId} />);
    expect(await screen.findByText("No managed indexing job")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open AI workspace/ })).toBeDisabled();
    await userEvent.setup().click(screen.getByRole("button", { name: "Start indexing" }));
    expect(await screen.findByText("Queued for worker")).toBeInTheDocument();
    expect(screen.getByText("Discovering").closest("li")).toHaveClass("stage-queued");
    await act(async () => { await pollCallback(intervals)(); });
    expect(await screen.findByText("Indexing in progress")).toBeInTheDocument();
    expect(screen.getByText("Discovering").closest("li")).toHaveClass("stage-done");
    expect(screen.getByText("Parsing").closest("li")).toHaveClass("stage-active");
  });

  it("shows a failed stage and retries with a new job", async () => {
    const fetcher = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith("/index/jobs/latest")) return Promise.resolve(json(job("FAILED", "EMBEDDING", ["DISCOVERING", "PARSING", "CHUNKING", "PERSISTING"])));
      if (url.endsWith("/index/status")) return Promise.resolve(json(coverage("EMPTY")));
      if (url.endsWith("/files")) return Promise.resolve(json({ total_files: 2, files: [] }));
      if (url.endsWith("/index") && init?.method === "POST") return Promise.resolve(json(job("QUEUED", "DISCOVERING", [], firstId, secondJobId), 202));
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetcher);
    render(<IndexingWorkspace repositoryId={firstId} />);
    expect(await screen.findByText("Indexing failed")).toBeInTheDocument();
    expect(screen.getByText("Embedding").closest("li")).toHaveClass("stage-failed");
    expect(screen.getByRole("alert")).toHaveTextContent("Embedding generation failed.");
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry failed indexing" }));
    expect(await screen.findByText("Queued for worker")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledWith(`/api/v1/repositories/${firstId}/index`, expect.objectContaining({ method: "POST" }));
  });

  it("recovers a running job after refresh, reaches ready, and stops polling", async () => {
    const intervals = vi.spyOn(window, "setInterval");
    const clear = vi.spyOn(window, "clearInterval");
    let statusReads = 0;
    const fetcher = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith("/index/jobs/latest")) return Promise.resolve(json(job("RUNNING", "VECTOR_INDEXING", ["DISCOVERING", "PARSING", "CHUNKING", "PERSISTING", "EMBEDDING"])));
      if (url.endsWith("/index/status")) return Promise.resolve(json(coverage(++statusReads > 1 ? "READY" : "EMPTY")));
      if (url.endsWith("/files")) return Promise.resolve(json({ total_files: 4, files: [] }));
      if (url.endsWith(`/index/jobs/${firstJobId}`)) return Promise.resolve(json(job("COMPLETED", "COMPLETED", ["DISCOVERING", "PARSING", "CHUNKING", "PERSISTING", "EMBEDDING", "VECTOR_INDEXING", "DEPENDENCY_EXTRACTION"])));
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetcher);
    render(<IndexingWorkspace repositoryId={firstId} />);
    expect(await screen.findByText("Indexing in progress")).toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalledWith(`/api/v1/repositories/${firstId}/index`, expect.objectContaining({ method: "POST" }));
    await act(async () => { await pollCallback(intervals)(); });
    expect(await screen.findByText("Indexing completed")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("link", { name: /Open AI workspace/ })).toHaveAttribute("href", `/projects/${firstId}/ai`));
    expect(screen.getByText("7 / 7")).toBeInTheDocument();
    expect(screen.getByText("Completed").closest("li")).toHaveClass("stage-done");
    expect(clear).toHaveBeenCalled();
  });

  it("keeps project state isolated when the repository changes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.endsWith("/index/jobs/latest")) return Promise.resolve(error("INDEX_JOB_NOT_FOUND", 404));
      if (url.endsWith("/index/status")) return Promise.resolve(json(coverage(url.includes(firstId) ? "READY" : "EMPTY")));
      if (url.endsWith("/files")) return Promise.resolve(json({ total_files: url.includes(firstId) ? 5 : 1, files: [] }));
      throw new Error(`Unexpected request: ${url}`);
    }));
    const view = render(<IndexingWorkspace repositoryId={firstId} />);
    expect(await screen.findByRole("link", { name: /Open AI workspace/ })).toBeInTheDocument();
    view.rerender(<IndexingWorkspace repositoryId={secondId} />);
    expect(await screen.findByText("No managed indexing job")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open AI workspace/ })).toBeDisabled();
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("does not enable analysis when index coverage is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.endsWith("/index/jobs/latest")) return Promise.resolve(error("INDEX_JOB_NOT_FOUND", 404));
      if (url.endsWith("/index/status")) return Promise.resolve(error("DATABASE_NOT_CONFIGURED", 503));
      if (url.endsWith("/files")) return Promise.resolve(json({ total_files: 1, files: [] }));
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<IndexingWorkspace repositoryId={firstId} />);
    expect(await screen.findByText("No managed indexing job")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open AI workspace/ })).toBeDisabled();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
  });
});
