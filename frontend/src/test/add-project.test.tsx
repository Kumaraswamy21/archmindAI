import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import NewProjectPage from "@/app/projects/new/page";
import { validateProjectAlias, validateProjectName } from "@/lib/project-validation";

const navigation = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("next/link", () => ({ default: ({ href, children, ...props }: React.ComponentProps<"a">) => <a href={href} {...props}>{children}</a> }));

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { vi.unstubAllGlobals(); navigation.push.mockReset(); });

describe("add project", () => {
  it("validates names and accepts only server approved aliases", () => {
    expect(validateProjectName("   ")).toBe("Enter a project name.");
    expect(validateProjectName("a".repeat(101))).toBe("Use 100 characters or fewer.");
    expect(validateProjectName("line\nbreak")).toBe("Use printable characters only.");
    expect(validateProjectAlias("/tmp/secret", ["approved"])).toBe("Choose a source from the approved list.");
  });

  it("loads aliases, validates the form, registers, and navigates", async () => {
    const projectId = "48b55c4c-5170-49cf-a5d5-2117ebae26de";
    const fetcher = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith("/approved-aliases")) return Promise.resolve(json({ aliases: ["approved"] }));
      if (url.endsWith("/repositories") && init?.method === "POST") return Promise.resolve(json({ repository_id: projectId, name: "Team Portal", source_alias: "approved", status: "REGISTERED", registered_at: "2026-11-01T00:00:00Z" }, 201));
      throw new Error(`Unexpected API request: ${url}`);
    });
    vi.stubGlobal("fetch", fetcher);
    render(<NewProjectPage />);
    await screen.findByRole("option", { name: "approved" });
    await userEvent.setup().click(screen.getByRole("button", { name: "Create project" }));
    expect(screen.getByText("Enter a project name.")).toBeInTheDocument();
    expect(screen.getByText("Choose an approved source.")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
    await userEvent.setup().type(screen.getByRole("textbox", { name: "Display name" }), "  Team Portal  ");
    await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "Approved source" }), "approved");
    await userEvent.setup().click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(navigation.push).toHaveBeenCalledWith(`/projects/${projectId}`));
    const post = fetcher.mock.calls.find(call => (call[1] as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(post?.[1]?.body as string)).toEqual({ name: "Team Portal", source_alias: "approved" });
  });

  it("shows a useful duplicate-registration error and request ID", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith("/approved-aliases")) return Promise.resolve(json({ aliases: ["approved"] }));
      if (init?.method === "POST") return Promise.resolve(json({ error: { code: "REPOSITORY_ALREADY_REGISTERED", message: "Repository already registered.", request_id: "trace-duplicate" } }, 409));
      throw new Error("Unexpected API request");
    }));
    render(<NewProjectPage />);
    await screen.findByRole("option", { name: "approved" });
    await userEvent.setup().type(screen.getByRole("textbox", { name: "Display name" }), "Existing");
    await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "Approved source" }), "approved");
    await userEvent.setup().click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already registered");
    expect(screen.getByText("Request ID: trace-duplicate")).toBeInTheDocument();
    expect(navigation.push).not.toHaveBeenCalled();
  });

  it("shows alias-loading failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<NewProjectPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The API is unavailable");
  });
});
