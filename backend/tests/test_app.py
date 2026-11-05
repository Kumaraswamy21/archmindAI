"""Smoke tests for the API documentation endpoints."""

from contract_helpers import legacy_error

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.main import create_app
from sqlalchemy.exc import OperationalError
from alembic.config import Config
from alembic.script import ScriptDirectory
from pathlib import Path


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


def test_readiness_requires_database_and_reports_outage() -> None:
    service = create_app()
    with TestClient(service) as client:
        missing = client.get("/api/v1/ready")
    assert missing.status_code == 503
    assert missing.json()["error"]["code"] == "DATABASE_NOT_CONFIGURED"

    class BrokenEngine:
        def connect(self):
            raise OperationalError("connection failed", None, None)

        def dispose(self):
            pass

    service.state.db_engine = BrokenEngine()
    service.state.provider_configured = True
    with TestClient(service) as client:
        outage = client.get("/api/v1/ready")
    assert outage.status_code == 503
    assert outage.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_readiness_success_and_pool_shutdown() -> None:
    expected = ScriptDirectory.from_config(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    ).get_current_head()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def begin(self):
            return self

        def execute(self, *_):
            return None

        def scalar(self, statement):
            return expected if "alembic_version" in str(statement) else True

    class Engine:
        disposed = False

        def connect(self):
            return Connection()

        def dispose(self):
            self.disposed = True

    service = create_app()
    engine = Engine()
    service.state.db_engine = engine
    service.state.provider_configured = True
    with TestClient(service) as client:
        response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "service": "archmind"}
    assert engine.disposed


def test_json_body_limit_and_request_id(monkeypatch) -> None:
    monkeypatch.setenv("ARCHMIND_JSON_BODY_LIMIT_BYTES", "1024")
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/questions/validate", json={"question": "x" * 2000}
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
    with TestClient(create_app()) as client:
        streamed = client.post(
            "/api/v1/questions/validate", content=(b"x" * 700 for _ in range(2))
        )
    assert streamed.status_code == 413


def test_production_mode_stops_without_authentication(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARCHMIND_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:secret@db.example.com/archmind"
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("ARCHMIND_FRONTEND_ORIGINS", '["https://app.example.com"]')
    monkeypatch.setenv("ARCHMIND_UPLOAD_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="authentication and authorization"):
        create_app()


def test_health_check_openapi_schema() -> None:
    schema = client.get("/openapi.json").json()

    operation = schema["paths"]["/api/v1/health"]["get"]
    assert operation["summary"] == "Check service health"
    assert (
        operation["description"]
        == "Return the current health status of the ArchMind API."
    )
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
    response = client.post("/api/v1/questions/validate", json={"question": "x" * 2000})

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
        "example_id",
        "question",
    }
    assert components["ErrorResponse"]["properties"]["error"] == {
        "$ref": "#/components/schemas/ErrorDetail"
    }
    assert set(components["ErrorDetail"]["required"]) == {
        "code",
        "message",
        "request_id",
    }
    assert set(components["ErrorDetail"]["properties"]) == {
        "code",
        "message",
        "request_id",
        "details",
    }
