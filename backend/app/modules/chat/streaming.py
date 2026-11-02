"""SSE framing and bounded lifecycle for a single LangGraph chat invocation."""

import asyncio
import json
from datetime import datetime, timezone
from threading import Event
from typing import Any, Literal
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel, Field

from app.errors import ApplicationError
from app.modules.agentic.workflow import WorkflowCancelled
from app.modules.chat.schemas import ChatRequest
from app.modules.chat.service import ChatService


EventType = Literal[
    "workflow_started", "intent_classified", "agent_started", "agent_completed",
    "evidence_verified", "answer_completed", "workflow_failed",
]


class StreamEvent(BaseModel):
    request_id: UUID
    event_type: EventType
    sequence: int = Field(ge=1)
    timestamp: datetime
    payload: dict[str, Any]


def encode_event(event: StreamEvent) -> str:
    data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


async def stream_chat(
    request: Request, body: ChatRequest, service: ChatService, request_id: UUID,
):
    """Run synchronous graph work off the event loop and release it on disconnect."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[EventType, dict[str, Any]]] = asyncio.Queue()
    cancelled = Event()
    sequence = 0

    def enqueue(event_type: EventType, payload: dict[str, Any]) -> None:
        if not cancelled.is_set():
            loop.call_soon_threadsafe(queue.put_nowait, (event_type, payload))

    def run_workflow() -> None:
        try:
            result = service.stream_respond(
                body, request_id=request_id, emit=enqueue, cancel_event=cancelled,
            )
            enqueue("answer_completed", result.model_dump(mode="json"))
        except WorkflowCancelled:
            enqueue("workflow_failed", {"code": "WORKFLOW_CANCELLED", "message": "Workflow cancelled."})
        except ApplicationError as exc:
            enqueue("workflow_failed", {"code": exc.code, "message": exc.message})
        except Exception:
            enqueue("workflow_failed", {"code": "WORKFLOW_FAILED", "message": "Workflow failed."})

    def frame(event_type: EventType, payload: dict[str, Any]) -> str:
        nonlocal sequence
        sequence += 1
        return encode_event(StreamEvent(
            request_id=request_id, event_type=event_type, sequence=sequence,
            timestamp=datetime.now(timezone.utc), payload=payload,
        ))

    task: asyncio.Task[None] | None = None
    try:
        yield frame("workflow_started", {"repository_id": str(body.repository_id)})
        task = asyncio.create_task(asyncio.to_thread(run_workflow))
        while True:
            if await request.is_disconnected():
                cancelled.set()
                break
            try:
                event_type, payload = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if await request.is_disconnected():
                cancelled.set()
                break
            yield frame(event_type, payload)
            if event_type in {"answer_completed", "workflow_failed"}:
                break
    finally:
        cancelled.set()
        if task is not None and not task.done():
            task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
