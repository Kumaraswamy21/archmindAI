"""Frontend chat adapter over the existing verified graph."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.errors import ApplicationError, handle_application_error, handle_validation_error
from app.main import create_app
from app.modules.chat.router import create_router
from app.modules.chat.service import ChatService
from test_agentic import graph_stub, result, workflow_for  # noqa: F401


def _client(workflow):
    app = FastAPI()
    app.add_exception_handler(ApplicationError, handle_application_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.include_router(create_router(ChatService(workflow)))
    return TestClient(app)


def test_successful_chat_routing_symbols_and_correlation(graph_stub):
    repository_id = uuid4()
    workflow, code, dependency, architecture, _ = workflow_for(repository_id)
    original = result(repository_id=repository_id)
    evidence = original.evidence["E1"]
    code.answer.return_value = replace(original, evidence={
        "E1": replace(evidence, qualified_symbol="example.OrderService.run"),
    })
    conversation_id = uuid4()
    response = _client(workflow).post("/api/v1/chat", json={
        "repository_id": str(repository_id), "message": "  Where is OrderService implemented?  ",
        "conversation_id": str(conversation_id),
    })
    assert response.status_code == 200
    body = response.json()
    assert UUID(body["request_id"])
    assert body["conversation_id"] == str(conversation_id)
    assert body["repository_id"] == str(repository_id)
    assert body["classified_intent"] == "CODE_LOOKUP"
    assert body["agents_executed"] == ["code_analysis"]
    assert body["workflow_status"] == "ANSWERED"
    assert body["verification_status"] == "CITATION_VERIFIED"
    assert body["execution_duration_ms"] == pytest.approx(15.0)
    assert body["prompt_versions"]["answer"]["name"] == "code_analysis"
    assert body["prompt_versions"]["classifier"] is None
    assert body["model_identifier"] == "gemini-2.5-flash"
    assert body["citations"] == [{
        "evidence_id": "E1", "file_path": "src/OrderService.java",
        "start_line": 3, "end_line": 8,
        "qualified_symbol": "example.OrderService.run",
    }]
    code.answer.assert_called_once()
    dependency.answer.assert_not_called()
    architecture.answer.assert_not_called()


def test_conversation_id_is_only_correlation(graph_stub):
    repository_id = uuid4()
    first_workflow, *_ = workflow_for(repository_id)
    first = _client(first_workflow).post("/api/v1/chat", json={
        "repository_id": str(repository_id), "message": "Explain OrderService.",
        "conversation_id": None,
    })
    assert first.status_code == 200
    generated = UUID(first.json()["conversation_id"])
    second_workflow, *_ = workflow_for(repository_id)
    second = _client(second_workflow).post("/api/v1/chat", json={
        "repository_id": str(repository_id), "message": "Explain OrderService.",
        "conversation_id": str(generated),
    })
    assert second.status_code == 200
    assert second.json()["conversation_id"] == str(generated)
    assert second.json()["request_id"] != first.json()["request_id"]


def test_invalid_repository_empty_message_and_provider_error(graph_stub):
    repository_id = uuid4()
    workflow, code, *_ = workflow_for(repository_id, registered=False)
    client = _client(workflow)
    missing = client.post("/api/v1/chat", json={
        "repository_id": str(repository_id), "message": "Explain OrderService.",
    })
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "REPOSITORY_NOT_FOUND"
    empty = client.post("/api/v1/chat", json={
        "repository_id": str(repository_id), "message": "   ",
    })
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "VALIDATION_ERROR"
    available, code, *_ = workflow_for(repository_id)
    code.answer.side_effect = ApplicationError(502, "RAG_PROVIDER_FAILED", "Analysis generation failed.")
    failed = _client(available).post("/api/v1/chat", json={
        "repository_id": str(repository_id), "message": "Explain OrderService.",
    })
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "RAG_PROVIDER_FAILED"


def test_unsupported_claims_and_cross_repository_evidence(graph_stub):
    repository_id = uuid4()
    workflow, code, *_ = workflow_for(repository_id)
    code.answer.return_value = replace(result(repository_id=repository_id),
                                       answer="This behavior is unsupported.")
    response = _client(workflow).post("/api/v1/chat", json={
        "repository_id": str(repository_id), "message": "Explain OrderService.",
    })
    assert response.status_code == 200
    assert response.json()["workflow_status"] == "INSUFFICIENT_EVIDENCE"
    assert response.json()["citations"] == []
    other_workflow, other_code, *_ = workflow_for(repository_id)
    original = result(repository_id=repository_id)
    other_code.answer.return_value = replace(original, evidence={
        "E1": replace(original.evidence["E1"], repository_id=uuid4()),
    })
    isolated = _client(other_workflow).post("/api/v1/chat", json={
        "repository_id": str(repository_id), "message": "Explain OrderService.",
    })
    assert isolated.status_code == 503
    assert isolated.json()["error"]["code"] == "RETRIEVAL_SCOPE_MISMATCH"


def test_citation_navigation_uses_secure_content_api(tmp_path: Path, monkeypatch):
    root = tmp_path / "approved"
    root.mkdir()
    source = root / "Service.java"
    source.write_text("first\nsecond\nthird\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(root)]))
    monkeypatch.setenv("ARCHMIND_REPOSITORY_ALIASES", json.dumps({"sample": str(root)}))
    client = TestClient(create_app())
    registered = client.post("/api/v1/repositories", json={
        "name": "Sample", "source_alias": "sample",
    })
    repository_id = registered.json()["repository_id"]
    endpoint = f"/api/v1/repositories/{repository_id}/files/content"
    selected = client.get(endpoint, params={"path": "Service.java", "start_line": 2, "end_line": 3})
    assert selected.status_code == 200
    assert selected.json()["content"] == "second\nthird\n"
    assert selected.json()["line_count"] == 2
    assert selected.json()["total_line_count"] == 3
    assert selected.json()["start_line"] == 2
    assert selected.json()["end_line"] == 3
    assert client.get(endpoint, params={"path": "Service.java", "start_line": 9, "end_line": 9}).status_code == 416
    assert client.get(endpoint, params={"path": "Service.java", "start_line": 2}).status_code == 422
    assert source.read_text() == "first\nsecond\nthird\n"


def test_chat_is_documented_and_unconfigured_error():
    client = TestClient(create_app())
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/chat"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ChatRequest"
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ChatResponse"
    }
    assert client.post("/api/v1/chat", json={
        "repository_id": str(uuid4()), "message": "Explain this code.",
    }).status_code == 503
