"""Read-only source-file content API and path security tests."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def registered_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_file_size_bytes: int = 10485760,
) -> tuple[TestClient, Path, str]:
    approved = tmp_path / "approved"
    root = approved / "repository"
    root.mkdir(parents=True)
    monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(approved)]))
    monkeypatch.setenv("ARCHMIND_REPOSITORY_ALIASES", json.dumps({"sample": str(root)}))
    monkeypatch.setenv("ARCHMIND_DISCOVERY_MAX_FILE_SIZE_BYTES", str(max_file_size_bytes))
    client = TestClient(create_app())
    registration = client.post(
        "/api/v1/repositories", json={"name": "sample-repo", "source_alias": "sample"}
    )
    assert registration.status_code == 201
    return client, root, registration.json()["repository_id"]


def read_file(client: TestClient, repository_id: str, path: str):
    return client.get(
        f"/api/v1/repositories/{repository_id}/files/content", params={"path": path}
    )


@pytest.mark.parametrize(
    ("filename", "language"),
    [("OrderService.java", "java"), ("service.py", "python"), ("service.ts", "typescript")],
)
def test_read_supported_source_file_and_line_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    language: str,
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    source = root / "src" / filename
    source.parent.mkdir()
    source.write_text("first line\nsecond line\n", encoding="utf-8")

    response = read_file(client, repository_id, f"src/{filename}")

    assert response.status_code == 200
    assert response.json() == {
        "repository_id": repository_id,
        "file_path": f"src/{filename}",
        "language": language,
        "line_count": 2,
        "content": "first line\nsecond line\n",
    }
    assert str(root) not in response.text


def test_empty_file_has_zero_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "empty.py").write_bytes(b"")

    response = read_file(client, repository_id, "empty.py")

    assert response.status_code == 200
    assert response.json()["line_count"] == 0
    assert response.json()["content"] == ""


def test_missing_file_returns_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, repository_id = registered_repository(tmp_path, monkeypatch)

    response = read_file(client, repository_id, "missing.py")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "SOURCE_FILE_NOT_FOUND", "message": "Source file not found."}
    }


def test_unknown_repository_returns_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = registered_repository(tmp_path, monkeypatch)

    response = read_file(client, str(uuid4()), "source.py")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "REPOSITORY_NOT_FOUND", "message": "Repository not found."}
    }


@pytest.mark.parametrize(
    "path",
    ["../outside.py", "src/../outside.py", "/tmp/outside.py", "./inside.py", "C:\\outside.py", "src\\inside.py"],
)
def test_invalid_or_escaping_path_returns_403(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "inside.py").write_text("safe")

    response = read_file(client, repository_id, path)

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "INVALID_SOURCE_PATH",
            "message": "Source file path is not permitted.",
        }
    }
    assert str(root) not in response.text


def test_excluded_directory_is_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    excluded = root / "node_modules"
    excluded.mkdir()
    (excluded / "hidden.ts").write_text("hidden")

    response = read_file(client, repository_id, "node_modules/hidden.ts")

    assert response.status_code == 403


def test_file_and_directory_symlinks_are_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("secret")
    (root / "link.py").symlink_to(outside / "secret.py")
    (root / "linked_dir").symlink_to(outside, target_is_directory=True)

    for path in ("link.py", "linked_dir/secret.py"):
        response = read_file(client, repository_id, path)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "INVALID_SOURCE_PATH"
        assert "secret" not in response.text


def test_unsupported_extension_returns_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "notes.txt").write_text("notes")

    response = read_file(client, repository_id, "notes.txt")

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "UNSUPPORTED_SOURCE_FILE",
            "message": "Unsupported source file type.",
        }
    }


def test_oversized_file_returns_413(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(
        tmp_path, monkeypatch, max_file_size_bytes=4
    )
    (root / "large.py").write_text("12345")

    response = read_file(client, repository_id, "large.py")

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "SOURCE_FILE_TOO_LARGE",
            "message": "Source file exceeds the size limit.",
        }
    }


def test_invalid_utf8_returns_controlled_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "invalid.py").write_bytes(b"\xff")

    response = read_file(client, repository_id, "invalid.py")

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "FILE_ENCODING_INVALID",
            "message": "Source file is not valid UTF-8.",
        }
    }


def test_read_does_not_modify_source_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    source = root / "readonly.py"
    source.write_text("value = 1\n", encoding="utf-8")
    source.chmod(0o444)
    before = source.stat()

    response = read_file(client, repository_id, "readonly.py")

    after = source.stat()
    assert response.status_code == 200
    assert source.read_bytes() == b"value = 1\n"
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_mode == before.st_mode


def test_missing_query_parameter_uses_fastapi_validation_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, repository_id = registered_repository(tmp_path, monkeypatch)

    response = client.get(f"/api/v1/repositories/{repository_id}/files/content")

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_content_openapi_schemas_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = registered_repository(tmp_path, monkeypatch)
    assert client.get("/docs").status_code == 200
    schema_response = client.get("/openapi.json")
    assert schema_response.status_code == 200
    schema = schema_response.json()
    operation = schema["paths"]["/api/v1/repositories/{repository_id}/files/content"]["get"]

    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RepositoryFileContentResponse"
    }
    for status in ("400", "403", "404", "413", "503"):
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
    assert {"$ref": "#/components/schemas/ErrorResponse"} in operation["responses"]["422"]["content"]["application/json"]["schema"]["anyOf"]
    assert {"$ref": "#/components/schemas/RequestValidationErrorResponse"} in operation["responses"]["422"]["content"]["application/json"]["schema"]["anyOf"]
    assert {parameter["name"] for parameter in operation["parameters"]} == {
        "repository_id", "path"
    }
    response_schema = schema["components"]["schemas"]["RepositoryFileContentResponse"]
    assert set(response_schema["required"]) == {
        "repository_id", "file_path", "language", "line_count", "content"
    }
