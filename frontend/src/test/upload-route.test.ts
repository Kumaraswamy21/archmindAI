import { afterEach, expect, it, vi } from "vitest";
import { POST } from "@/app/api/v1/repositories/upload/route";

afterEach(() => vi.unstubAllGlobals());

it("streams the incoming ZIP body to the backend", async () => {
  const upstream = vi.fn().mockResolvedValue(new Response(JSON.stringify({ repository_id: "test" }), {
    status: 201, headers: { "Content-Type": "application/json", "X-Request-ID": "upload-trace" },
  }));
  vi.stubGlobal("fetch", upstream);
  const request = new Request("http://127.0.0.1:3000/api/v1/repositories/upload?name=Large%20Repo", {
    method: "POST", headers: { "Content-Type": "application/zip" }, body: new Blob(["ZIP"]),
  });
  const response = await POST(request);
  expect(response.status).toBe(201);
  expect(response.headers.get("X-Request-ID")).toBe("upload-trace");
  const [url, init] = upstream.mock.calls[0];
  expect(url).toContain("/api/v1/repositories/upload?name=Large%20Repo");
  expect(init.body).toBe(request.body);
  expect(init.duplex).toBe("half");
});
