"""Frontend chat API over the existing LangGraph workflow."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.errors import ApplicationError, ErrorResponse
from app.modules.chat.schemas import ChatRequest, ChatResponse
from app.modules.chat.service import ChatService
from app.modules.chat.streaming import stream_chat


def create_router(service: ChatService | None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["chat"])

    @router.post("/chat", response_model=ChatResponse,
                 summary="Analyze a repository message with verified citations",
                 responses={
                     404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                     429: {"model": ErrorResponse}, 502: {"model": ErrorResponse},
                     503: {"model": ErrorResponse}, 504: {"model": ErrorResponse},
                 })
    def chat(body: ChatRequest, request: Request) -> ChatResponse:
        if service is None:
            raise ApplicationError(503, "CHAT_NOT_CONFIGURED", "Repository chat is not configured.")
        correlation = getattr(request.state, "request_id", None)
        return service.respond(body, request_id=UUID(correlation) if correlation else uuid4())

    @router.post(
        "/chat/stream", response_class=StreamingResponse,
        summary="Stream verified repository chat workflow events",
        responses={
            200: {"description": "Server-Sent Events. See StreamEvent schema and streaming-events.md.",
                  "content": {"text/event-stream": {"schema": {
                      "$ref": "#/components/schemas/StreamEvent"
                  }}}},
            422: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
        },
    )
    async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
        if service is None:
            raise ApplicationError(503, "CHAT_NOT_CONFIGURED", "Repository chat is not configured.")
        correlation = getattr(request.state, "request_id", None)
        request_id = UUID(correlation) if correlation else uuid4()
        return StreamingResponse(
            stream_chat(request, body, service, request_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
