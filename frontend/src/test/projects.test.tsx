import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ProjectsPage from "@/app/projects/page";

vi.mock("next/link", () => ({ default: ({ href, children, ...props }: React.ComponentProps<"a">) => <a href={href} {...props}>{children}</a> }));

const repository = (number: number) => ({
  repository_id: `00000000-0000-4000-8000-${String(number).padStart(12, "0")}`,
  name: number === 101 ? "Final Service" : `Service ${number}`,
  source_alias: number === 101 ? "final-service" : `service-${number}`,
  status: "REGISTERED",
  registered_at: "2026-11-01T00:00:00Z",
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => vi.unstubAllGlobals());

describe("projects dashboard", () => {
  it("loads every API page, searches globally, filters index state, and opens a project", async () => {
    const first = Array.from({ length: 100 }, (_, index) => repository(index + 1));
    const last = repository(101);
    const fetcher = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/index/status")) return Promise.resolve(json({ index_status: url.includes(last.repository_id) ? "FAILED" : "READY" }));
      if (url.includes("offset=0")) return Promise.resolve(json({ total: 101, limit: 100, offset: 0, repositories: first }));
      if (url.includes("offset=100")) return Promise.resolve(json({ total: 101, limit: 100, offset: 100, repositories: [last] }));
      throw new Error(`Unexpected API request: ${url}`);
    });
    vi.stubGlobal("fetch", fetcher);
    render(<ProjectsPage />);
    await waitFor(() => expect(screen.getByText("101 OF 101 PROJECTS")).toBeInTheDocument());
    await userEvent.setup().type(screen.getByRole("textbox", { name: "Search projects" }), "Final Service");
    expect(screen.getByRole("link", { name: "Open Final Service workspace" })).toHaveAttribute("href", `/projects/${last.repository_id}`);
    expect(screen.getByText("1 OF 101 PROJECTS")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Index failed")).toBeInTheDocument());
    await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "Filter by indexing status" }), "ready");
    expect(screen.getByRole("heading", { name: "No matching projects" })).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/repositories?limit=100&offset=100", expect.any(Object));
  });

  it("shows a retryable API error without fabricated cards", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<ProjectsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The API is unavailable");
    expect(screen.queryByRole("link", { name: /Open .* workspace/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Try again/ })).toBeInTheDocument();
  });
});
