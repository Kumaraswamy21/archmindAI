"""Secure source-file discovery through registered repositories."""

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
    max_files: int = 10000,
    max_file_size_bytes: int = 10485760,
) -> tuple[TestClient, Path, str]:
    approved = tmp_path / "approved"
    root = approved / "repository"
    root.mkdir(parents=True)
    monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(approved)]))
    monkeypatch.setenv("ARCHMIND_REPOSITORY_ALIASES", json.dumps({"sample": str(root)}))
    monkeypatch.setenv("ARCHMIND_DISCOVERY_MAX_FILES", str(max_files))
    monkeypatch.setenv("ARCHMIND_DISCOVERY_MAX_FILE_SIZE_BYTES", str(max_file_size_bytes))
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/repositories", json={"name": "sample-repo", "source_alias": "sample"}
    )
    assert response.status_code == 201
    return client, root, response.json()["repository_id"]


def test_language_filtering_relative_paths_and_stable_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "z.ts").write_text("export {}")
    (root / "a.py").write_text("pass")
    (root / "ignored.txt").write_text("ignored")
    nested = root / "src" / "main"
    nested.mkdir(parents=True)
    (nested / "OrderService.java").write_text("class OrderService {}")

    endpoint = f"/api/v1/repositories/{repository_id}/files"
    response = client.get(endpoint)
    repeated = client.get(endpoint)

    assert response.status_code == 200
    assert response.json() == repeated.json()
    assert response.json() == {
        "repository_id": repository_id,
        "total_files": 3,
        "files": [
            {"path": "a.py", "language": "python", "size_bytes": 4},
            {
                "path": "src/main/OrderService.java",
                "language": "java",
                "size_bytes": 21,
            },
            {"path": "z.ts", "language": "typescript", "size_bytes": 9},
        ],
    }
    assert str(root) not in response.text


def test_excluded_directories_are_not_scanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    for dirname in (
        ".git", "node_modules", "target", "build", "dist", "__pycache__", ".venv"
    ):
        directory = root / dirname / "nested"
        directory.mkdir(parents=True)
        (directory / "hidden.py").write_text("hidden")
    (root / "visible.py").write_text("visible")

    response = client.get(f"/api/v1/repositories/{repository_id}/files")

    assert response.status_code == 200
    assert [file["path"] for file in response.json()["files"]] == ["visible.py"]


def test_file_and_directory_symlinks_are_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("secret")
    (root / "linked.py").symlink_to(outside / "secret.py")
    (root / "linked-directory").symlink_to(outside, target_is_directory=True)
    (root / "visible.java").write_text("visible")

    response = client.get(f"/api/v1/repositories/{repository_id}/files")

    assert response.status_code == 200
    assert [file["path"] for file in response.json()["files"]] == ["visible.java"]


def test_empty_repository_returns_empty_file_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, repository_id = registered_repository(tmp_path, monkeypatch)

    response = client.get(f"/api/v1/repositories/{repository_id}/files")

    assert response.status_code == 200
    assert response.json() == {
        "repository_id": repository_id,
        "total_files": 0,
        "files": [],
    }


def test_file_size_limit_rejects_the_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(
        tmp_path, monkeypatch, max_file_size_bytes=3
    )
    (root / "large.py").write_text("four")

    response = client.get(f"/api/v1/repositories/{repository_id}/files")

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "DISCOVERY_LIMIT_EXCEEDED",
            "message": "Repository discovery limit exceeded.",
        }
    }
    assert str(root) not in response.text


def test_file_count_limit_rejects_the_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch, max_files=1)
    (root / "first.py").write_text("1")
    (root / "second.java").write_text("2")

    response = client.get(f"/api/v1/repositories/{repository_id}/files")

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "DISCOVERY_LIMIT_EXCEEDED"


def test_files_exactly_at_configured_limits_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(
        tmp_path, monkeypatch, max_files=1, max_file_size_bytes=4
    )
    (root / "exact.py").write_text("four")

    response = client.get(f"/api/v1/repositories/{repository_id}/files")

    assert response.status_code == 200
    assert response.json()["files"] == [
        {"path": "exact.py", "language": "python", "size_bytes": 4}
    ]


def test_unknown_repository_returns_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = registered_repository(tmp_path, monkeypatch)

    response = client.get(f"/api/v1/repositories/{uuid4()}/files")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "REPOSITORY_NOT_FOUND", "message": "Repository not found."}
    }


def test_unavailable_registered_repository_returns_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    root.rename(tmp_path / "moved")

    response = client.get(f"/api/v1/repositories/{repository_id}/files")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "REPOSITORY_UNAVAILABLE",
            "message": "Registered repository is unavailable.",
        }
    }
    assert str(root) not in response.text


def test_registered_root_replaced_by_symlink_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("secret")
    root.rename(tmp_path / "original")
    root.symlink_to(outside, target_is_directory=True)

    response = client.get(f"/api/v1/repositories/{repository_id}/files")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REPOSITORY_UNAVAILABLE"
    assert "secret.py" not in response.text


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("ARCHMIND_DISCOVERY_MAX_FILES", "0"),
        ("ARCHMIND_DISCOVERY_MAX_FILE_SIZE_BYTES", "not-an-integer"),
    ],
)
def test_invalid_discovery_limit_configuration_fails_startup(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match="positive integers"):
        create_app()


def test_discovery_openapi_schema_and_error_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = registered_repository(tmp_path, monkeypatch)
    assert client.get("/docs").status_code == 200
    schema_response = client.get("/openapi.json")
    assert schema_response.status_code == 200
    schema = schema_response.json()
    operation = schema["paths"]["/api/v1/repositories/{repository_id}/files"]["get"]

    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RepositoryFilesResponse"
    }
    for status in ("404", "413", "503"):
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
    for status, code in {
        "404": "REPOSITORY_NOT_FOUND",
        "413": "DISCOVERY_LIMIT_EXCEEDED",
        "503": "REPOSITORY_UNAVAILABLE",
    }.items():
        assert operation["responses"][status]["content"]["application/json"]["example"]["error"]["code"] == code
    assert "422" in operation["responses"]
    components = schema["components"]["schemas"]
    assert set(components["RepositoryFilesResponse"]["properties"]) == {
        "repository_id", "total_files", "files"
    }
    assert set(components["RepositoryFile"]["properties"]) == {
        "path", "language", "size_bytes"
    }
