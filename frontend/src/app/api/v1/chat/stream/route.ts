import { randomUUID } from "node:crypto";

export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  const apiOrigin = new URL(process.env.ARCHMIND_API_URL ?? "http://127.0.0.1:8000").origin;
  let upstream: Response;
  try {
    upstream = await fetch(`${apiOrigin}/api/v1/chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: await request.text(),
      signal: request.signal,
      cache: "no-store",
    });
  } catch {
    return Response.json({
      error: {
        code: "API_UNREACHABLE",
        message: "The API is unavailable. Check the backend connection.",
        request_id: randomUUID(),
        details: null,
      },
    }, { status: 503 });
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
