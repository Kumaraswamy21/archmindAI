"""Smoke tests for the API documentation endpoints."""

from contract_helpers import legacy_error

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_openapi_schema_has_expected_metadata() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"] == {
        "title": "ArchMind AI API",
        "version": "0.1.0",
        "description": "Read-only repository intelligence and architecture analysis platform",
    }


def test_swagger_docs_are_accessible() -> None:
    response = client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_health_check_returns_expected_response() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "archmind"}


def test_health_check_openapi_schema() -> None:
    schema = client.get("/openapi.json").json()

    operation = schema["paths"]["/api/v1/health"]["get"]
    assert operation["summary"] == "Check service health"
    assert operation["description"] == "Return the current health status of the ArchMind API."
    assert operation["responses"]["200"]["description"] == "The service is healthy."
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthResponse"
    }

    response_schema = schema["components"]["schemas"]["HealthResponse"]
    assert set(response_schema["required"]) == {"status", "service"}
    assert set(response_schema["properties"]) == {"status", "service"}
    assert response_schema["properties"]["status"]["type"] == "string"
    assert response_schema["properties"]["service"]["type"] == "string"


def test_valid_question_returns_normalized_response() -> None:
    response = client.post(
        "/api/v1/questions/validate",
        json={"question": "  Where is OrderService implemented?  "},
    )

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "normalized_question": "Where is OrderService implemented?",
    }


def test_question_at_maximum_length_is_valid() -> None:
    response = client.post(
        "/api/v1/questions/validate", json={"question": "x" * 2000}
    )

    assert response.status_code == 200
    assert response.json()["normalized_question"] == "x" * 2000


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "   "},
        {},
        {"question": "x" * 2001},
    ],
    ids=["empty", "whitespace-only", "missing", "too-long"],
)
def test_invalid_question_returns_422(payload: dict[str, str]) -> None:
    response = client.post("/api/v1/questions/validate", json=payload)

    assert response.status_code == 422
    assert isinstance(response.json()["error"]["details"], list)
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_question_validation_openapi_schema_and_examples() -> None:
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/questions/validate"]["post"]

    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/QuestionValidationRequest"
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/QuestionValidationResponse"
    }
    assert "422" in operation["responses"]

    request_schema = schema["components"]["schemas"]["QuestionValidationRequest"]
    question_schema = request_schema["properties"]["question"]
    assert request_schema["required"] == ["question"]
    assert question_schema["type"] == "string"
    assert question_schema["minLength"] == 1
    assert question_schema["maxLength"] == 2000
    assert request_schema["examples"] == [
        {"question": "  Where is OrderService implemented?  "}
    ]

    response_schema = schema["components"]["schemas"]["QuestionValidationResponse"]
    assert set(response_schema["required"]) == {"valid", "normalized_question"}
    assert response_schema["properties"]["valid"]["type"] == "boolean"
    assert response_schema["properties"]["normalized_question"]["type"] == "string"
    assert response_schema["examples"] == [
        {"valid": True, "normalized_question": "Where is OrderService implemented?"}
    ]


@pytest.mark.parametrize(
    ("example_id", "question"),
    [
        ("order-service", "Where is OrderService implemented?"),
        ("service-dependencies", "Which services depend on the payment module?"),
    ],
)
def test_known_question_example_returns_200(example_id: str, question: str) -> None:
    response = client.get(f"/api/v1/questions/examples/{example_id}")

    assert response.status_code == 200
    assert response.json() == {"example_id": example_id, "question": question}


def test_unknown_question_example_returns_standard_error() -> None:
    response = client.get("/api/v1/questions/examples/unknown")

    assert response.status_code == 404
    assert legacy_error(response) == {
        "error": {
            "code": "EXAMPLE_NOT_FOUND",
            "message": "Question example not found.",
        }
    }


def test_all_routes_and_example_responses_are_documented() -> None:
    schema = client.get("/openapi.json").json()
    assert {
        "/api/v1/health",
        "/api/v1/questions/validate",
        "/api/v1/questions/examples/{example_id}",
    } <= set(schema["paths"])

    operation = schema["paths"]["/api/v1/questions/examples/{example_id}"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/QuestionExampleResponse"
    }
    assert operation["responses"]["404"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }

    components = schema["components"]["schemas"]
    assert set(components["QuestionExampleResponse"]["required"]) == {
        "example_id", "question"
    }
    assert components["ErrorResponse"]["properties"]["error"] == {
        "$ref": "#/components/schemas/ErrorDetail"
    }
    assert set(components["ErrorDetail"]["required"]) == {"code", "message", "request_id"}
    assert set(components["ErrorDetail"]["properties"]) == {"code", "message", "request_id", "details"}
