"""ZIP upload behavior and archive safety checks."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.repository_ingestion.discovery import discover_source_files
from app.modules.repository_ingestion.uploads import MAX_ARCHIVE_BYTES, MAX_EXTRACTED_BYTES


def archive(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        for name, content in files.items():
            package.writestr(name, content)
    return output.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ARCHMIND_UPLOAD_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("ARCHMIND_REPOSITORY_ALIASES", raising=False)
    monkeypatch.delenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", raising=False)
    return TestClient(create_app())


def test_upload_registers_and_reads_source_without_approved_alias(client):
    response = client.post("/api/v1/repositories/upload?name=My%20Code",
                           content=archive({"repo/src/Main.java": "class Main {}", "repo/README.md": "hi"}),
                           headers={"Content-Type": "application/zip"})
    assert response.status_code == 201, response.text
    repository_id = response.json()["repository_id"]
    assert response.json()["source_alias"].startswith("upload-")
    files = client.get(f"/api/v1/repositories/{repository_id}/files")
    assert [entry["path"] for entry in files.json()["files"]] == ["repo/src/Main.java"]
    source = client.get(f"/api/v1/repositories/{repository_id}/files/content?path=repo/src/Main.java")
    assert source.json()["content"] == "class Main {}"


def test_upload_ignores_macos_archive_metadata(client):
    response = client.post("/api/v1/repositories/upload?name=Mac Archive",
                           content=archive({"repo/Main.java": "class Main {}",
                                            "__MACOSX/repo/._Main.java": "metadata",
                                            "repo/._Main.java": "metadata"}),
                           headers={"Content-Type": "application/zip"})
    assert response.status_code == 201, response.text
    repository_id = response.json()["repository_id"]
    files = client.get(f"/api/v1/repositories/{repository_id}/files")
    assert [entry["path"] for entry in files.json()["files"]] == ["repo/Main.java"]


def test_existing_upload_discovery_ignores_macos_metadata(tmp_path):
    (tmp_path / "__MACOSX").mkdir()
    (tmp_path / "__MACOSX" / "._Main.java").write_bytes(b"\xff")
    (tmp_path / "._Hidden.java").write_bytes(b"\xff")
    (tmp_path / "Main.java").write_text("class Main {}")
    files = discover_source_files(tmp_path, max_files=10, max_file_size_bytes=1000)
    assert [file.path for file in files] == ["Main.java"]


@pytest.mark.parametrize("files,code", [
    ({"../escape.py": "pass"}, "INVALID_ARCHIVE"),
    ({"repo/README.md": "hi"}, "NO_SOURCE_FILES"),
])
def test_upload_rejects_invalid_archives(client, files, code):
    response = client.post("/api/v1/repositories/upload?name=Bad",
                           content=archive(files), headers={"Content-Type": "application/zip"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert client.get("/api/v1/repositories").json()["total"] == 0


def test_upload_rejects_non_zip(client):
    response = client.post("/api/v1/repositories/upload?name=Bad", content=b"hello",
                           headers={"Content-Type": "application/zip"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_ARCHIVE"


def test_upload_size_boundary(client, monkeypatch):
    assert MAX_ARCHIVE_BYTES == 500 * 1024 * 1024
    assert MAX_EXTRACTED_BYTES >= MAX_ARCHIVE_BYTES
    monkeypatch.setattr("app.modules.repository_ingestion.router.MAX_ARCHIVE_BYTES", 8)
    response = client.post("/api/v1/repositories/upload?name=Large",
                           content=b"123456789", headers={"Content-Type": "application/zip"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "UPLOAD_LIMIT_EXCEEDED"
