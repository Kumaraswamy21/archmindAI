"""HTTP SSE behavior over the installed LangGraph and verified chat adapter."""

import json
import asyncio
from dataclasses import replace
from threading import Event
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import ApplicationError, handle_application_error
from app.main import create_app
from app.modules.chat.router import create_router
from app.modules.chat.service import ChatService
from app.modules.chat.schemas import ChatRequest
from app.modules.chat.streaming import stream_chat
from app.modules.agentic.classifier import IntentClassification
from test_agentic import result, workflow_for


def client_for(workflow):
    app = FastAPI()
    app.add_exception_handler(ApplicationError, handle_application_error)
    app.include_router(create_router(ChatService(workflow)))
    return TestClient(app)


def events_for(client, repository_id, *, message="Where is OrderService implemented?"):
    with client.stream("POST", "/api/v1/chat/stream", json={
        "repository_id": str(repository_id), "message": message,
    }) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        blocks = "".join(response.iter_text()).strip().split("\n\n")
    events = []
    for block in blocks:
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        event = json.loads(fields["data"])
        assert fields["event"] == event["event_type"]
        assert int(fields["id"]) == event["sequence"]
        events.append(event)
    assert [item["sequence"] for item in events] == list(range(1, len(events) + 1))
    assert len({item["request_id"] for item in events}) == 1
    assert all(item["timestamp"].endswith("Z") for item in events)
    return events


def test_stream_orders_actual_graph_events_and_verified_citations():
    repository_id = uuid4()
    workflow, code, *_ = workflow_for(repository_id)
    original = result(repository_id=repository_id)
    code.answer.return_value = replace(original, evidence={
        "E1": replace(original.evidence["E1"], qualified_symbol="example.OrderService.run"),
    })
    events = events_for(client_for(workflow), repository_id)
    assert [item["event_type"] for item in events] == [
        "workflow_started", "intent_classified", "agent_started", "agent_completed",
        "evidence_verified", "answer_completed",
    ]
    final = events[-1]["payload"]
    assert final["request_id"] == events[0]["request_id"]
    assert final["repository_id"] == str(repository_id)
    assert final["verification_status"] == "CITATION_VERIFIED"
    assert final["citations"] == [{
        "evidence_id": "E1", "file_path": "src/OrderService.java",
        "start_line": 3, "end_line": 8,
        "qualified_symbol": "example.OrderService.run",
    }]
    assert code.answer.call_count == 1


def test_stream_failure_terminates_and_hides_provider_details():
    repository_id = uuid4()
    workflow, code, *_ = workflow_for(repository_id)
    code.answer.side_effect = RuntimeError("private-key secret source content")
    events = events_for(client_for(workflow), repository_id)
    assert [item["event_type"] for item in events] == [
        "workflow_started", "intent_classified", "agent_started", "workflow_failed",
    ]
    assert events[-1]["payload"] == {"code": "WORKFLOW_FAILED", "message": "Workflow failed."}
    assert "private-key" not in json.dumps(events)


def test_stream_provider_timeout_and_repository_scope():
    repository_id = uuid4()
    workflow, code, *_ = workflow_for(repository_id)
    code.answer.side_effect = ApplicationError(504, "RAG_PROVIDER_TIMEOUT", "Analysis generation timed out.")
    events = events_for(client_for(workflow), repository_id)
    assert events[-1]["payload"]["code"] == "RAG_PROVIDER_TIMEOUT"
    missing, *_ = workflow_for(repository_id, registered=False)
    events = events_for(client_for(missing), repository_id)
    assert [item["event_type"] for item in events] == ["workflow_started", "workflow_failed"]
    assert events[-1]["payload"]["code"] == "REPOSITORY_NOT_FOUND"


def test_stream_rejects_cross_repository_evidence():
    repository_id = uuid4()
    workflow, code, *_ = workflow_for(repository_id)
    original = result(repository_id=repository_id)
    code.answer.return_value = replace(original, evidence={
        "E1": replace(original.evidence["E1"], repository_id=uuid4()),
    })
    events = events_for(client_for(workflow), repository_id)
    assert events[-1]["event_type"] == "workflow_failed"
    assert events[-1]["payload"]["code"] == "RETRIEVAL_SCOPE_MISMATCH"
    assert not any(item["event_type"] == "answer_completed" for item in events)


def test_clarification_emits_only_nodes_that_ran():
    repository_id = uuid4()
    workflow, code, dependency, architecture, _ = workflow_for(
        repository_id,
        classification=IntentClassification(intent="UNKNOWN", routing_reason="Question is vague."),
    )
    events = events_for(client_for(workflow), repository_id, message="Could you look at this?")
    assert [item["event_type"] for item in events] == [
        "workflow_started", "intent_classified", "answer_completed",
    ]
    assert events[-1]["payload"]["workflow_status"] == "CLARIFICATION_REQUIRED"
    code.answer.assert_not_called()
    dependency.answer.assert_not_called()
    architecture.answer.assert_not_called()


def test_stream_no_source_or_prompt_in_progress_and_nonstream_regression():
    repository_id = uuid4()
    workflow, code, *_ = workflow_for(repository_id)
    original = result(repository_id=repository_id)
    code.answer.return_value = replace(original, evidence={
        "E1": replace(original.evidence["E1"], content="sensitive source content"),
    })
    events = events_for(client_for(workflow), repository_id)
    progress = json.dumps(events[:-1])
    assert "sensitive source content" not in progress
    assert "prompt_versions" not in progress
    assert "model_identifier" not in progress
    second, *_ = workflow_for(repository_id)
    response = client_for(second).post("/api/v1/chat", json={
        "repository_id": str(repository_id), "message": "Where is OrderService implemented?",
    })
    assert response.status_code == 200
    assert response.json()["citations"][0]["evidence_id"] == "E1"


def test_stream_cancellation_signal_stops_before_agent():
    repository_id = uuid4()
    workflow, code, *_ = workflow_for(repository_id)
    cancelled = Event()
    cancelled.set()
    from app.modules.agentic.schemas import AnalyzeRequest
    from app.modules.agentic.workflow import WorkflowCancelled
    try:
        workflow.stream_with_evidence(
            AnalyzeRequest(repository_id=repository_id, question="Where is OrderService implemented?"),
            request_id=uuid4(), emit=lambda *_: None, cancel_event=cancelled,
        )
    except WorkflowCancelled:
        pass
    else:
        raise AssertionError("Cancellation must stop the graph")
    code.answer.assert_not_called()


def test_disconnect_cancels_active_stream_and_cleans_up():
    entered = Event()
    observed_cancel = Event()

    class RequestStub:
        disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    class ServiceStub:
        def stream_respond(self, _body, *, request_id, emit, cancel_event):
            entered.set()
            if cancel_event.wait(timeout=2):
                observed_cancel.set()
            from app.modules.agentic.workflow import WorkflowCancelled
            raise WorkflowCancelled()

    async def exercise():
        request = RequestStub()
        body = ChatRequest(repository_id=uuid4(), message="Explain this code.")
        stream = stream_chat(request, body, ServiceStub(), uuid4())
        assert "workflow_started" in await stream.__anext__()
        consumer = asyncio.create_task(stream.__anext__())
        assert await asyncio.to_thread(entered.wait, 2)
        request.disconnected = True
        try:
            await asyncio.wait_for(consumer, timeout=2)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("Disconnected stream must end")
        assert await asyncio.to_thread(observed_cancel.wait, 2)

    asyncio.run(exercise())


def test_stream_openapi_and_swagger():
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/chat/stream"]["post"]
    assert "text/event-stream" in operation["responses"]["200"]["content"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ChatRequest"
    }
    response = client.post("/api/v1/chat/stream", json={
        "repository_id": str(uuid4()), "message": "Explain this code.",
    })
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CHAT_NOT_CONFIGURED"
