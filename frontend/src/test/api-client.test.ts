import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api/client";

afterEach(() => vi.unstubAllGlobals());

describe("typed API client", () => {
  it("requests the paginated same-origin repository route", async () => {
    const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ total: 0, limit: 12, offset: 24, repositories: [] }), { status: 200 }));
    vi.stubGlobal("fetch", request);
    const result = await api.repositories(12, 24);
    expect(request.mock.calls[0][0]).toBe("/api/v1/repositories?limit=12&offset=24");
    expect(result.total).toBe(0);
  });

  it("keeps public backend error codes and request IDs", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "REPOSITORY_NOT_FOUND", message: "Repository not found.", request_id: "trace-1" } }), { status: 404 })));
    await expect(api.repository("missing")).rejects.toEqual(new ApiError("REPOSITORY_NOT_FOUND", "Repository not found.", "trace-1"));
  });
});
