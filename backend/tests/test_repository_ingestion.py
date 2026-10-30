"""Repository registration API and configuration checks."""

import json
from datetime import timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.repository_ingestion.registry import RepositoryRegistry


@pytest.fixture
def repository_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    approved = tmp_path / "approved"
    (approved / "ecommerce").mkdir(parents=True)
    (approved / "payments").mkdir()
    monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(approved)]))
    monkeypatch.setenv(
        "ARCHMIND_REPOSITORY_ALIASES",
        json.dumps(
            {
                "sample-ecommerce": str(approved / "ecommerce"),
                "sample-payments": str(approved / "payments"),
            }
        ),
    )
    return TestClient(create_app())


def test_register_approved_repository(repository_client: TestClient) -> None:
    response = repository_client.post(
        "/api/v1/repositories",
        json={"name": "ecommerce-service", "source_alias": "sample-ecommerce"},
    )

    assert response.status_code == 201
    assert UUID(response.json()["repository_id"])
    assert response.json()["name"] == "ecommerce-service"
    assert response.json()["status"] == "REGISTERED"
    assert set(response.json()) == {"repository_id", "name", "status"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": "", "source_alias": "sample-ecommerce"},
        {"name": "ecommerce-service", "source_alias": ""},
        {
            "name": "ecommerce-service",
            "source_alias": "sample-ecommerce",
            "path": "/private/repository",
        },
    ],
)
def test_invalid_registration_request_returns_422(
    repository_client: TestClient, payload: dict[str, str]
) -> None:
    response = repository_client.post("/api/v1/repositories", json=payload)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_unknown_alias_returns_404(repository_client: TestClient) -> None:
    response = repository_client.post(
        "/api/v1/repositories",
        json={"name": "unknown-service", "source_alias": "not-configured"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "REPOSITORY_ALIAS_NOT_FOUND",
            "message": "Repository source alias not found.",
        }
    }


def test_duplicate_alias_or_name_returns_409(repository_client: TestClient) -> None:
    endpoint = "/api/v1/repositories"
    first = repository_client.post(
        endpoint, json={"name": "ecommerce-service", "source_alias": "sample-ecommerce"}
    )
    duplicate_alias = repository_client.post(
        endpoint, json={"name": "another-name", "source_alias": "sample-ecommerce"}
    )
    duplicate_name = repository_client.post(
        endpoint, json={"name": "ecommerce-service", "source_alias": "sample-payments"}
    )

    assert first.status_code == 201
    for response in (duplicate_alias, duplicate_name):
        assert response.status_code == 409
        assert response.json() == {
            "error": {
                "code": "REPOSITORY_ALREADY_REGISTERED",
                "message": "Repository already registered.",
            }
        }


@pytest.mark.parametrize("source_kind", ["missing", "file", "outside", "relative", "symlink"])
def test_invalid_configured_source_returns_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_kind: str,
) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    if source_kind == "missing":
        source = approved / "missing"
    elif source_kind == "file":
        source = approved / "not-a-directory"
        source.write_text("not a repository")
    elif source_kind == "outside":
        source = outside
    elif source_kind == "relative":
        source = Path("relative/repository")
    else:
        source = approved / "escape"
        source.symlink_to(outside, target_is_directory=True)

    monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(approved)]))
    monkeypatch.setenv("ARCHMIND_REPOSITORY_ALIASES", json.dumps({"invalid": str(source)}))
    response = TestClient(create_app()).post(
        "/api/v1/repositories", json={"name": "invalid-service", "source_alias": "invalid"}
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "REPOSITORY_SOURCE_UNAVAILABLE",
            "message": "Configured repository source is unavailable.",
        }
    }
    assert str(tmp_path) not in response.text


def test_registry_keeps_internal_path_and_timestamp(tmp_path: Path) -> None:
    registry = RepositoryRegistry()
    record = registry.register("sample", "sample-alias", tmp_path)

    assert registry.get(record.repository_id) == record
    assert registry.get(UUID(int=0)) is None
    assert record.root_path == tmp_path
    assert record.registered_at.tzinfo == timezone.utc
    assert record.status == "REGISTERED"


def test_repository_openapi_and_phase_one_routes(repository_client: TestClient) -> None:
    assert repository_client.get("/docs").status_code == 200
    schema_response = repository_client.get("/openapi.json")
    assert schema_response.status_code == 200
    schema = schema_response.json()
    assert {
        "/api/v1/health",
        "/api/v1/questions/validate",
        "/api/v1/questions/examples/{example_id}",
        "/api/v1/repositories",
    } <= set(schema["paths"])
    assert repository_client.get("/api/v1/health").status_code == 200
    assert repository_client.post(
        "/api/v1/questions/validate", json={"question": "  Where?  "}
    ).json() == {"valid": True, "normalized_question": "Where?"}

    operation = schema["paths"]["/api/v1/repositories"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RepositoryRegistrationRequest"
    }
    for status, model in {
        "201": "RepositoryRegistrationResponse",
        "404": "ErrorResponse",
        "409": "ErrorResponse",
        "503": "ErrorResponse",
    }.items():
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model}"
        }
    for status, code in {
        "404": "REPOSITORY_ALIAS_NOT_FOUND",
        "409": "REPOSITORY_ALREADY_REGISTERED",
        "503": "REPOSITORY_SOURCE_UNAVAILABLE",
    }.items():
        assert operation["responses"][status]["content"]["application/json"]["example"]["error"]["code"] == code
    assert "422" in operation["responses"]
    request_schema = schema["components"]["schemas"]["RepositoryRegistrationRequest"]
    assert set(request_schema["required"]) == {"name", "source_alias"}
    assert set(request_schema["properties"]) == {"name", "source_alias"}
    assert request_schema["additionalProperties"] is False
    response_schema = schema["components"]["schemas"]["RepositoryRegistrationResponse"]
    assert set(response_schema["properties"]) == {"repository_id", "name", "status"}
