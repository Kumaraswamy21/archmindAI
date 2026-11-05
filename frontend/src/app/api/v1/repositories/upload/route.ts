const apiOrigin = new URL(process.env.ARCHMIND_API_URL ?? "http://127.0.0.1:8000").origin;

export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  const incoming = new URL(request.url);
  const target = `${apiOrigin}/api/v1/repositories/upload${incoming.search}`;
  try {
    const upstream = await fetch(target, {
      method: "POST",
      headers: { "Content-Type": request.headers.get("content-type") ?? "application/octet-stream" },
      body: request.body,
      duplex: "half",
      signal: request.signal,
    } as RequestInit & { duplex: "half" });
    const headers = new Headers();
    headers.set("Content-Type", upstream.headers.get("content-type") ?? "application/json");
    const requestId = upstream.headers.get("x-request-id");
    if (requestId) headers.set("X-Request-ID", requestId);
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch {
    return Response.json({ error: { code: "API_UNREACHABLE", message: "The API is unavailable. Check the backend connection." } }, { status: 503 });
  }
}
