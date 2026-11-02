"""Synchronous repository scan summaries and Phase 2 workflow."""

from contract_helpers import legacy_error

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def registered_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, max_files: int = 10000
) -> tuple[TestClient, Path, str]:
    approved = tmp_path / "approved"
    root = approved / "repository"
    root.mkdir(parents=True)
    monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(approved)]))
    monkeypatch.setenv("ARCHMIND_REPOSITORY_ALIASES", json.dumps({"sample": str(root)}))
    monkeypatch.setenv("ARCHMIND_DISCOVERY_MAX_FILES", str(max_files))
    client = TestClient(create_app())
    registration = client.post(
        "/api/v1/repositories", json={"name": "sample-repo", "source_alias": "sample"}
    )
    assert registration.status_code == 201
    return client, root, registration.json()["repository_id"]


def scan(client: TestClient, repository_id: str):
    return client.post(f"/api/v1/repositories/{repository_id}/scan")


def test_scan_counts_languages_and_total_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    sources = {
        "src/OrderService.java": b"class OrderService {}",
        "src/OrderController.java": b"class OrderController {}",
        "tools/check.py": b"print('ok')",
        "web/app.ts": b"export {}",
    }
    for relative_path, content in sources.items():
        source = root / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)

    response = scan(client, repository_id)

    assert response.status_code == 200
    assert response.json() == {
        "repository_id": repository_id,
        "status": "COMPLETED",
        "summary": {
            "total_files": 4,
            "total_size_bytes": sum(len(content) for content in sources.values()),
            "languages": {"java": 2, "python": 1, "typescript": 1},
        },
    }
    assert str(root) not in response.text


def test_empty_repository_has_zero_counts_for_all_languages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, repository_id = registered_repository(tmp_path, monkeypatch)

    response = scan(client, repository_id)

    assert response.status_code == 200
    assert response.json()["summary"] == {
        "total_files": 0,
        "total_size_bytes": 0,
        "languages": {"java": 0, "python": 0, "typescript": 0},
    }


def test_scan_reuses_discovery_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "visible.py").write_bytes(b"one")
    (root / "notes.txt").write_bytes(b"ignored")
    for dirname in (
        ".git", "node_modules", "target", "build", "dist", "__pycache__", ".venv"
    ):
        excluded = root / dirname
        excluded.mkdir()
        (excluded / "hidden.py").write_bytes(b"hidden")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "linked.java").write_bytes(b"linked")
    (root / "linked.java").symlink_to(outside / "linked.java")

    response = scan(client, repository_id)

    assert response.status_code == 200
    assert response.json()["summary"] == {
        "total_files": 1,
        "total_size_bytes": 3,
        "languages": {"java": 0, "python": 1, "typescript": 0},
    }


def test_repeated_scans_recompute_without_modifying_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    source = root / "first.py"
    source.write_bytes(b"one")
    before = source.stat()

    first = scan(client, repository_id)
    repeated = scan(client, repository_id)
    (root / "second.ts").write_bytes(b"two")
    updated = scan(client, repository_id)

    assert first.status_code == repeated.status_code == updated.status_code == 200
    assert first.json() == repeated.json()
    assert updated.json()["summary"] == {
        "total_files": 2,
        "total_size_bytes": 6,
        "languages": {"java": 0, "python": 1, "typescript": 1},
    }
    assert source.read_bytes() == b"one"
    assert source.stat().st_mtime_ns == before.st_mtime_ns


def test_unknown_repository_returns_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = registered_repository(tmp_path, monkeypatch)

    response = scan(client, str(uuid4()))

    assert response.status_code == 404
    assert legacy_error(response) == {
        "error": {"code": "REPOSITORY_NOT_FOUND", "message": "Repository not found."}
    }


def test_unavailable_repository_returns_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    root.rename(tmp_path / "moved")

    response = scan(client, repository_id)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REPOSITORY_UNAVAILABLE"


def test_discovery_limit_error_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch, max_files=1)
    (root / "one.py").write_bytes(b"1")
    (root / "two.ts").write_bytes(b"2")

    response = scan(client, repository_id)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "DISCOVERY_LIMIT_EXCEEDED"


def test_phase_two_workflow_and_scan_openapi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "sample.py").write_bytes(b"print('ok')\n")
    discovered = client.get(f"/api/v1/repositories/{repository_id}/files")
    content = client.get(
        f"/api/v1/repositories/{repository_id}/files/content",
        params={"path": "sample.py"},
    )
    scanned = scan(client, repository_id)

    assert discovered.status_code == content.status_code == scanned.status_code == 200
    assert discovered.json()["total_files"] == 1
    assert content.json()["content"] == "print('ok')\n"
    assert scanned.json()["summary"]["total_size_bytes"] == 12
    assert client.get("/docs").status_code == 200

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    schema = openapi.json()
    assert {
        "/api/v1/repositories",
        "/api/v1/repositories/{repository_id}/files",
        "/api/v1/repositories/{repository_id}/files/content",
        "/api/v1/repositories/{repository_id}/scan",
    } <= set(schema["paths"])
    operation = schema["paths"]["/api/v1/repositories/{repository_id}/scan"]["post"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RepositoryScanResponse"
    }
    for status, code in {
        "404": "REPOSITORY_NOT_FOUND",
        "413": "DISCOVERY_LIMIT_EXCEEDED",
        "503": "REPOSITORY_UNAVAILABLE",
    }.items():
        media = operation["responses"][status]["content"]["application/json"]
        assert media["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
        assert media["example"]["error"]["code"] == code
    assert "422" in operation["responses"]
    components = schema["components"]["schemas"]
    assert set(components["RepositoryScanResponse"]["required"]) == {
        "repository_id", "status", "summary"
    }
    assert set(components["RepositoryScanSummary"]["required"]) == {
        "total_files", "total_size_bytes", "languages"
    }
    assert set(components["RepositoryLanguageCounts"]["required"]) == {
        "java", "python", "typescript"
    }
