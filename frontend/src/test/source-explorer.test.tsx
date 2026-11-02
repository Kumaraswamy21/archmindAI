import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import ExplorePage from "@/app/projects/[repositoryId]/explore/page";
import { SourceBrowser } from "@/components/source-browser";
import { SourceSidePanel } from "@/components/source-side-panel";
import { tokenizeJava } from "@/lib/java-tokens";

const one = "11111111-1111-4111-8111-111111111111";
const two = "22222222-2222-4222-8222-222222222222";
const files = (id: string) => ({ repository_id: id, total_files: 2, files: [
  { path: "src/Service.java", language: "java", size_bytes: 42 },
  { path: "src/util/Helper.java", language: "java", size_bytes: 10 },
] });
const content = (id: string, path: string) => ({ repository_id: id, file_path: path, language: "java", line_count: 3, content: "public class Service {\n  void run() {}\n}\n", start_line: null, end_line: null });
const json = (value: object, status = 200) => new Response(JSON.stringify(value), { status });
afterEach(() => vi.unstubAllGlobals());

it("loads the file tree, filters by path, and opens a full read-only Java file", async () => {
  const fetcher = vi.fn((url: string) => {
    if (url.endsWith("/files")) return Promise.resolve(json(files(one)));
    if (url.includes("/files/content?")) return Promise.resolve(json(content(one, "src/Service.java")));
    throw new Error(url);
  });
  vi.stubGlobal("fetch", fetcher);
  render(await ExplorePage({ params: Promise.resolve({ repositoryId: one }), searchParams: Promise.resolve({}) }));
  expect(await screen.findByRole("button", { name: "Open src/Service.java" })).toBeInTheDocument();
  await userEvent.setup().type(screen.getByRole("textbox", { name: "Search repository files" }), "helper");
  expect(screen.queryByRole("button", { name: "Open src/Service.java" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open src/util/Helper.java" })).toBeInTheDocument();
  await userEvent.setup().clear(screen.getByRole("textbox", { name: "Search repository files" }));
  await userEvent.setup().click(screen.getByRole("button", { name: "Open src/Service.java" }));
  await waitFor(() => expect(document.querySelector('[data-line="2"] code')?.textContent).toBe("  void run() {}"));
  expect(document.querySelector(".java-keyword")?.textContent).toBe("public");
  expect(fetcher.mock.calls.some(([url]) => url.includes(`/repositories/${one}/files/content?path=src%2FService.java`))).toBe(true);
});

it("opens a dependency source link at its recorded line range", async () => {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.endsWith("/files")) return Promise.resolve(json(files(one)));
    if (url.includes("/files/content?")) return Promise.resolve(json(content(one, "src/Service.java")));
    throw new Error(url);
  }));
  const page = await ExplorePage({ params: Promise.resolve({ repositoryId: one }), searchParams: Promise.resolve({ path: "src/Service.java", start_line: "2", end_line: "2" }) });
  render(page);
  await waitFor(() => expect(document.querySelector('[data-line="2"]')?.getAttribute("data-highlighted")).toBe("true"));
  expect(document.querySelectorAll('[data-highlighted="true"]')).toHaveLength(1);
});

it("ignores unsafe or ambiguous source paths in route parameters", async () => {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.endsWith("/files")) return Promise.resolve(json(files(one)));
    throw new Error(`Unexpected source request: ${url}`);
  }));
  const page = await ExplorePage({ params: Promise.resolve({ repositoryId: one }), searchParams: Promise.resolve({ path: ["src/Service.java", "/server/private.java"], start_line: "1", end_line: "2" }) });
  render(page);
  expect(await screen.findByText("Select a file")).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("navigates citations, highlights inclusive ranges, and keeps the same repository scope", async () => {
  const fetcher = vi.fn((url: string) => {
    if (url.endsWith("/files")) return Promise.resolve(json(files(one)));
    if (url.includes("/files/content?")) return Promise.resolve(json(content(one, url.includes("Helper") ? "src/util/Helper.java" : "src/Service.java")));
    throw new Error(url);
  });
  vi.stubGlobal("fetch", fetcher);
  function Host() {
    const [index, setIndex] = useState(0);
    return <SourceSidePanel repositoryId={one} citations={[
      { evidence_id: "E1", file_path: "src/Service.java", start_line: 2, end_line: 2, qualified_symbol: null },
      { evidence_id: "E2", file_path: "src/util/Helper.java", start_line: 1, end_line: 3, qualified_symbol: null },
    ]} index={index} onIndexChange={setIndex} onClose={() => {}} />;
  }
  render(<Host />);
  await waitFor(() => expect(document.querySelectorAll('[data-highlighted="true"]')).toHaveLength(1));
  expect(document.querySelector('[data-line="2"]')?.getAttribute("data-highlighted")).toBe("true");
  await userEvent.setup().click(screen.getByRole("button", { name: "Next citation" }));
  await waitFor(() => expect(document.querySelectorAll('[data-highlighted="true"]')).toHaveLength(3));
  expect(screen.getByRole("dialog", { name: /E2.*Helper.java/ })).toBeInTheDocument();
  expect(fetcher.mock.calls.every(([url]) => url.includes(`/repositories/${one}/`))).toBe(true);
});

it("reports missing files and stale citation ranges without showing a false highlight", async () => {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.endsWith("/files")) return Promise.resolve(json(files(one)));
    if (url.includes("/files/content?")) return Promise.resolve(json(content(one, "src/Service.java")));
    throw new Error(url);
  }));
  const view = render(<SourceBrowser repositoryId={one} initialPath="src/Removed.java" highlight={{ startLine: 1, endLine: 3 }} />);
  expect(await screen.findByText("Referenced file is unavailable")).toBeInTheDocument();
  view.rerender(<SourceBrowser key="stale" repositoryId={one} initialPath="src/Service.java" highlight={{ startLine: 12, endLine: 15 }} />);
  expect(await screen.findByText(/cited line range is no longer present/)).toBeInTheDocument();
  expect(document.querySelectorAll('[data-highlighted="true"]')).toHaveLength(0);
});

it("shows a retryable error when a listed file disappears before content is read", async () => {
  const fetcher = vi.fn((url: string) => {
    if (url.endsWith("/files")) return Promise.resolve(json(files(one)));
    if (url.includes("/files/content?")) return Promise.resolve(json({ error: { code: "FILE_NOT_FOUND", message: "Source file not found.", request_id: "request-1", details: null } }, 404));
    throw new Error(url);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<SourceBrowser repositoryId={one} initialPath="src/Service.java" />);
  expect(await screen.findByText("Source unavailable")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry loading" })).toBeInTheDocument();
  expect(document.querySelectorAll('[data-highlighted="true"]')).toHaveLength(0);
});

it("reloads only the selected repository when the repository changes", async () => {
  const fetcher = vi.fn((url: string) => {
    const id = url.includes(one) ? one : two;
    if (url.endsWith("/files")) return Promise.resolve(json(files(id)));
    if (url.includes("/files/content?")) return Promise.resolve(json(content(id, "src/Service.java")));
    throw new Error(url);
  });
  vi.stubGlobal("fetch", fetcher);
  const view = render(<SourceBrowser key={one} repositoryId={one} initialPath="src/Service.java" />);
  await waitFor(() => expect(document.querySelector('[data-line="1"]')).toBeInTheDocument());
  view.rerender(<SourceBrowser key={two} repositoryId={two} initialPath="src/Service.java" />);
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url.includes(`/repositories/${two}/files/content`))).toBe(true));
  expect(fetcher.mock.calls.every(([url]) => url.includes(`/repositories/${one}/`) || url.includes(`/repositories/${two}/`))).toBe(true);
});

it("rejects a file response from another repository", async () => {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.endsWith("/files")) return Promise.resolve(json(files(one)));
    if (url.includes("/files/content?")) return Promise.resolve(json(content(two, "src/Service.java")));
    throw new Error(url);
  }));
  render(<SourceBrowser repositoryId={one} initialPath="src/Service.java" />);
  expect(await screen.findByText(/source response did not match the selected repository/)).toBeInTheDocument();
  expect(document.querySelector('[data-line="1"]')).not.toBeInTheDocument();
});

it("tokenizes Java comments and strings without treating their words as keywords", () => {
  const tokens = tokenizeJava("// public class\nString value = \"class\";\n/* return */");
  expect(tokens[0][0]).toEqual({ kind: "comment", text: "// public class" });
  expect(tokens[1].some(token => token.kind === "string" && token.text === '"class"')).toBe(true);
  expect(tokens[2][0]).toEqual({ kind: "comment", text: "/* return */" });
});
