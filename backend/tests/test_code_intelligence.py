"""Java AST parsing through registered, securely read repository files."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def registered_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Path, str]:
    approved = tmp_path / "approved"
    root = approved / "repository"
    root.mkdir(parents=True)
    monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(approved)]))
    monkeypatch.setenv("ARCHMIND_REPOSITORY_ALIASES", json.dumps({"sample": str(root)}))
    client = TestClient(create_app())
    registration = client.post(
        "/api/v1/repositories", json={"name": "sample-repo", "source_alias": "sample"}
    )
    assert registration.status_code == 201
    return client, root, registration.json()["repository_id"]


def parse_file(client: TestClient, repository_id: str, path: str):
    return client.post(
        f"/api/v1/repositories/{repository_id}/parse", json={"file_path": path}
    )


def test_java_classes_methods_and_one_based_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    source = root / "src" / "OrderService.java"
    source.parent.mkdir()
    source.write_text(
        "package demo;\n"
        "// An example service.\n"
        "class OrderService {\n"
        "    void createOrder() {\n"
        "    }\n"
        "    int count() { return 1; }\n"
        "}\n"
        "class Helper {\n"
        "    void assist() {}\n"
        "}\n",
        encoding="utf-8",
    )
    before = source.stat()

    response = parse_file(client, repository_id, "src/OrderService.java")
    repeated = parse_file(client, repository_id, "src/OrderService.java")

    assert response.status_code == 200
    assert response.json() == repeated.json()
    assert response.json() == {
        "file_path": "src/OrderService.java",
        "language": "java",
        "has_syntax_errors": False,
        "classes": [
            {
                "name": "OrderService",
                "start_line": 3,
                "end_line": 7,
                "methods": [
                    {"name": "createOrder", "start_line": 4, "end_line": 5},
                    {"name": "count", "start_line": 6, "end_line": 6},
                ],
            },
            {
                "name": "Helper",
                "start_line": 8,
                "end_line": 10,
                "methods": [{"name": "assist", "start_line": 9, "end_line": 9}],
            },
        ],
    }
    assert source.stat().st_mtime_ns == before.st_mtime_ns
    assert str(root) not in response.text


def test_nested_classes_and_constructors_are_out_of_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "Outer.java").write_text(
        "class Outer {\n"
        "  Outer() {}\n"
        "  void direct() {}\n"
        "  class Inner { void nested() {} }\n"
        "}\n",
        encoding="utf-8",
    )

    response = parse_file(client, repository_id, "Outer.java")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["classes"]] == ["Outer"]
    assert [item["name"] for item in response.json()["classes"][0]["methods"]] == [
        "direct"
    ]


def test_empty_java_file_returns_no_classes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "Empty.java").write_bytes(b"")

    response = parse_file(client, repository_id, "Empty.java")

    assert response.status_code == 200
    assert response.json() == {
        "file_path": "Empty.java",
        "language": "java",
        "has_syntax_errors": False,
        "classes": [],
    }


def test_malformed_java_reports_errors_and_recoverable_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "Broken.java").write_text(
        "class Good { void ok() {} }\nclass Broken { void bad( {",
        encoding="utf-8",
    )

    response = parse_file(client, repository_id, "Broken.java")

    assert response.status_code == 200
    assert response.json()["has_syntax_errors"] is True
    assert response.json()["classes"] == [
        {
            "name": "Good",
            "start_line": 1,
            "end_line": 1,
            "methods": [{"name": "ok", "start_line": 1, "end_line": 1}],
        }
    ]


@pytest.mark.parametrize(
    ("filename", "code"),
    [("script.py", "UNSUPPORTED_PARSE_LANGUAGE"), ("notes.txt", "UNSUPPORTED_SOURCE_FILE")],
)
def test_unsupported_source_file_returns_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str, code: str
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / filename).write_text("content", encoding="utf-8")

    response = parse_file(client, repository_id, filename)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


def test_unknown_repository_returns_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = registered_repository(tmp_path, monkeypatch)

    response = parse_file(client, str(uuid4()), "Example.java")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPOSITORY_NOT_FOUND"


@pytest.mark.parametrize("path", ["../outside.java", "/tmp/outside.java"])
def test_invalid_file_path_returns_403(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    client, _, repository_id = registered_repository(tmp_path, monkeypatch)

    response = parse_file(client, repository_id, path)

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "INVALID_SOURCE_PATH",
            "message": "Source file path is not permitted.",
        }
    }


def test_parse_openapi_schema_and_documented_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = registered_repository(tmp_path, monkeypatch)
    assert client.get("/docs").status_code == 200
    schema_response = client.get("/openapi.json")
    assert schema_response.status_code == 200
    schema = schema_response.json()
    operation = schema["paths"]["/api/v1/repositories/{repository_id}/parse"]["post"]

    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/JavaParseRequest"
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/JavaParseResponse"
    }
    for status in ("400", "403", "404", "413", "503"):
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
    assert {"$ref": "#/components/schemas/ErrorResponse"} in operation["responses"]["422"]["content"]["application/json"]["schema"]["anyOf"]
    response_schema = schema["components"]["schemas"]["JavaParseResponse"]
    assert set(response_schema["required"]) == {
        "file_path", "language", "has_syntax_errors", "classes"
    }
