"""AST-aware chunk preview through registered, read-only repositories."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path, str]:
    approved = tmp_path / "approved"
    root = approved / "repository"
    root.mkdir(parents=True)
    monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(approved)]))
    monkeypatch.setenv("ARCHMIND_REPOSITORY_ALIASES", json.dumps({"sample": str(root)}))
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/repositories", json={"name": "sample-repo", "source_alias": "sample"}
    )
    assert response.status_code == 201
    return client, root, response.json()["repository_id"]


def preview(client: TestClient, repository_id: str, file_path: str, max_chunk_chars=None):
    body = {"file_path": file_path}
    if max_chunk_chars is not None:
        body["max_chunk_chars"] = max_chunk_chars
    return client.post(
        f"/api/v1/repositories/{repository_id}/chunks/preview",
        json=body,
    )


def test_single_method_exact_content_context_and_stable_id(repository) -> None:
    client, root, repository_id = repository
    source = (
        "package com.example;\n"
        "import java.util.List;\n"
        "@Service\n"
        "class OrderService {\n"
        "  @Transactional\n"
        "  public void createOrder(String id) {\n"
        "    System.out.println(id);\n"
        "  }\n"
        "}\n"
    )
    path = root / "OrderService.java"
    path.write_text(source, encoding="utf-8")
    before = path.stat().st_mtime_ns

    response = preview(client, repository_id, "OrderService.java")

    assert response.status_code == 200
    data = response.json()
    assert data["total_chunks"] == 1
    chunk = data["chunks"][0]
    assert chunk == {
        "chunk_id": chunk["chunk_id"],
        "parent_chunk_id": None,
        "sequence": None,
        "split_strategy": None,
        "start_byte": chunk["start_byte"],
        "end_byte": chunk["end_byte"],
        "repository_id": repository_id,
        "file_path": "OrderService.java",
        "language": "java",
        "chunk_type": "method",
        "symbol_name": "createOrder",
        "qualified_name": "com.example.OrderService.createOrder",
        "parent_symbol": "OrderService",
        "package_name": "com.example",
        "imports": ["java.util.List"],
        "annotations": ["@Service", "@Transactional"],
        "start_line": 5,
        "end_line": 8,
        "content": "@Transactional\n  public void createOrder(String id) {\n    System.out.println(id);\n  }",
        "contextual_header": "Package: com.example | Class: OrderService | Method: createOrder | Annotation: @Service | Annotation: @Transactional",
    }
    assert preview(client, repository_id, "OrderService.java").json() == data
    assert path.stat().st_mtime_ns == before
    assert str(root) not in response.text


def test_multiple_methods_overloads_and_constructor(repository) -> None:
    client, root, repository_id = repository
    (root / "Api.java").write_text(
        "class Api {\n"
        "  Api() {}\n"
        "  void find(int id) {} void find(String id) {}\n"
        "  void save() {}\n"
        "}\n", encoding="utf-8"
    )

    response = preview(client, repository_id, "Api.java")

    assert response.status_code == 200
    chunks = response.json()["chunks"]
    assert response.json()["total_chunks"] == 4
    assert [(c["chunk_type"], c["symbol_name"]) for c in chunks] == [
        ("constructor", "Api"), ("method", "find"),
        ("method", "find"), ("method", "save"),
    ]
    assert len({c["chunk_id"] for c in chunks}) == 4
    assert chunks[1]["content"] == "void find(int id) {}"
    assert chunks[2]["content"] == "void find(String id) {}"
    assert (chunks[1]["start_line"], chunks[1]["end_line"]) == (3, 3)
    assert all("class Api {" not in c["content"] for c in chunks)


def test_nested_classes_and_class_without_methods_do_not_duplicate_content(repository) -> None:
    client, root, repository_id = repository
    (root / "Outer.java").write_text(
        "class Outer {\n"
        "  static class Inner {\n"
        "    void run() {}\n"
        "  }\n"
        "  static class Empty {}\n"
        "}\n", encoding="utf-8"
    )

    response = preview(client, repository_id, "Outer.java")

    assert response.status_code == 200
    chunks = response.json()["chunks"]
    assert [(c["chunk_type"], c["qualified_name"]) for c in chunks] == [
        ("method", "Outer.Inner.run"), ("class", "Outer.Empty")
    ]
    assert chunks[0]["parent_symbol"] == "Inner"
    assert chunks[1]["parent_symbol"] == "Outer"
    assert chunks[1]["content"] == "static class Empty {}"
    assert len({c["chunk_id"] for c in chunks}) == len(chunks)


def test_empty_file_and_leaf_class(repository) -> None:
    client, root, repository_id = repository
    (root / "Empty.java").write_bytes(b"")
    (root / "Marker.java").write_text("@Service\nclass Marker {}\n", encoding="utf-8")

    empty = preview(client, repository_id, "Empty.java")
    marker = preview(client, repository_id, "Marker.java")

    assert empty.status_code == 200
    assert empty.json() == {"file_path": "Empty.java", "total_chunks": 0, "chunks": [], "excluded_spans": []}
    assert marker.status_code == 200
    chunk = marker.json()["chunks"][0]
    assert chunk["chunk_type"] == "class"
    assert chunk["content"] == "@Service\nclass Marker {}"
    assert (chunk["start_line"], chunk["end_line"]) == (1, 2)
    assert chunk["annotations"] == ["@Service"]


def test_reader_errors_and_validation(repository) -> None:
    client, root, repository_id = repository
    (root / "script.py").write_text("pass", encoding="utf-8")
    (root / "bad.java").write_bytes(b"\xff")

    for path, status, code in [
        ("script.py", 400, "UNSUPPORTED_PARSE_LANGUAGE"),
        ("../escape.java", 403, "INVALID_SOURCE_PATH"),
        ("missing.java", 404, "SOURCE_FILE_NOT_FOUND"),
        ("bad.java", 422, "FILE_ENCODING_INVALID"),
    ]:
        response = preview(client, repository_id, path)
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
    assert preview(client, str(uuid4()), "script.py").status_code == 404
    invalid = client.post(f"/api/v1/repositories/{repository_id}/chunks/preview", json={})
    assert invalid.status_code == 422
    assert "details" in invalid.json()["error"]


def test_configured_file_size_limit_applies_to_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = tmp_path / "approved"
    root = approved / "repository"
    root.mkdir(parents=True)
    monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(approved)]))
    monkeypatch.setenv("ARCHMIND_REPOSITORY_ALIASES", json.dumps({"sample": str(root)}))
    monkeypatch.setenv("ARCHMIND_DISCOVERY_MAX_FILE_SIZE_BYTES", "16")
    (root / "Large.java").write_text("class Large { void run() {} }", encoding="utf-8")
    client = TestClient(create_app())
    registration = client.post(
        "/api/v1/repositories", json={"name": "sample-repo", "source_alias": "sample"}
    )
    response = preview(client, registration.json()["repository_id"], "Large.java")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "SOURCE_FILE_TOO_LARGE"


def test_preview_openapi_and_swagger(repository) -> None:
    client, _, _ = repository
    assert client.get("/docs").status_code == 200
    spec_response = client.get("/openapi.json")
    assert spec_response.status_code == 200
    schema = spec_response.json()
    operation = schema["paths"]["/api/v1/repositories/{repository_id}/chunks/preview"]["post"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ChunkPreviewResponse"
    }
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ChunkPreviewRequest"
    }
    assert {"400", "403", "404", "413", "422", "503"} <= set(operation["responses"])
    fields = schema["components"]["schemas"]["JavaChunk"]["required"]
    assert set(fields) == {
        "chunk_id", "repository_id", "file_path", "language", "chunk_type",
        "symbol_name", "qualified_name", "parent_symbol", "package_name",
        "imports", "annotations", "start_line", "end_line", "content",
        "contextual_header", "start_byte", "end_byte",
    }
    request_properties = schema["components"]["schemas"]["ChunkPreviewRequest"]["properties"]
    assert request_properties["max_chunk_chars"].get("maximum") == 1_000_000 or any(
        variant.get("maximum") == 1_000_000
        for variant in request_properties["max_chunk_chars"].get("anyOf", [])
    )
    assert "excluded_spans" in schema["components"]["schemas"]["ChunkPreviewResponse"]["properties"]


def _assert_full_coverage(source: str, response: dict, declaration: str) -> None:
    encoded = source.encode("utf-8")
    chunks = response["chunks"]
    excluded = response["excluded_spans"]
    spans = sorted(
        [(item["start_byte"], item["end_byte"], item.get("content")) for item in chunks]
        + [(item["start_byte"], item["end_byte"], None) for item in excluded]
    )
    assert spans[0][0] == 0
    assert spans[-1][1] == len(encoded)
    assert all(left[1] == right[0] for left, right in zip(spans, spans[1:]))
    for start, end, content in spans:
        if content is not None:
            assert encoded[start:end].decode("utf-8") == content
    for item in [*chunks, *excluded]:
        start, end = item["start_byte"], item["end_byte"]
        expected_start_line = encoded[:start].count(b"\n") + 1
        expected_end_line = encoded[:end].count(b"\n") + (
            0 if encoded[end - 1 : end] == b"\n" else 1
        )
        assert item["start_line"] == expected_start_line
        assert item["end_line"] == max(expected_start_line, expected_end_line)
    assert all(item["reason"] == "outside_chunked_declarations" for item in excluded)
    assert "".join(item["content"] for item in chunks) == declaration


def test_oversized_method_splits_at_statements_with_complete_coverage(repository) -> None:
    client, root, repository_id = repository
    source = (
        "package demo;\n"
        "class Processor {\n"
        "  void process() {\n"
        "    first();\n"
        "    second();\n"
        "    third();\n"
        "    fourth();\n"
        "  }\n"
        "}\n"
    )
    (root / "Processor.java").write_text(source, encoding="utf-8")
    default = preview(client, repository_id, "Processor.java").json()
    limited = preview(client, repository_id, "Processor.java", 35).json()
    again = preview(client, repository_id, "Processor.java", 35).json()

    assert default["total_chunks"] == 1
    assert default["chunks"][0]["chunk_type"] == "method"
    assert limited == again
    chunks = limited["chunks"]
    assert len(chunks) > 1
    assert all(chunk["chunk_type"] == "method_part" for chunk in chunks)
    assert all(chunk["parent_chunk_id"] == default["chunks"][0]["chunk_id"] for chunk in chunks)
    assert [chunk["sequence"] for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert all(len(chunk["content"]) <= 35 for chunk in chunks)
    assert all(chunk["split_strategy"] == "ast" for chunk in chunks)
    assert all(chunk["parent_symbol"] == "Processor" for chunk in chunks)
    assert all("Part: " in chunk["contextual_header"] for chunk in chunks)
    assert all(chunk["start_line"] <= chunk["end_line"] for chunk in chunks)
    _assert_full_coverage(source, limited, default["chunks"][0]["content"])


def test_oversized_constructor_and_leaf_class(repository) -> None:
    client, root, repository_id = repository
    (root / "Ctor.java").write_text(
        "class Ctor {\n  Ctor() {\n    first();\n    second();\n    third();\n  }\n}\n",
        encoding="utf-8",
    )
    (root / "Data.java").write_text(
        "class Data {\n  int a;\n  int b;\n  int c;\n  int d;\n}\n",
        encoding="utf-8",
    )

    constructor = preview(client, repository_id, "Ctor.java", 26).json()["chunks"]
    leaf_class = preview(client, repository_id, "Data.java", 24).json()["chunks"]
    assert len(constructor) > 1
    assert all(chunk["chunk_type"] == "constructor_part" for chunk in constructor)
    assert len(leaf_class) > 1
    assert all(chunk["chunk_type"] == "class_part" for chunk in leaf_class)


def test_fallback_unicode_and_source_offsets(repository) -> None:
    client, root, repository_id = repository
    source = "class Unicode {\n  void run() { String s = \"αβγδεζηθικλμν\"; }\n}\n"
    (root / "Unicode.java").write_text(source, encoding="utf-8")
    default = preview(client, repository_id, "Unicode.java").json()
    limited = preview(client, repository_id, "Unicode.java", 12).json()

    chunks = limited["chunks"]
    assert len(chunks) > 1
    assert any(chunk["split_strategy"] == "fallback" for chunk in chunks)
    assert all(len(chunk["content"]) <= 12 for chunk in chunks)
    assert len({chunk["chunk_id"] for chunk in chunks}) == len(chunks)
    _assert_full_coverage(source, limited, default["chunks"][0]["content"])


@pytest.mark.parametrize("limit", [0, -1, 1_000_001, "bad"])
def test_invalid_chunk_size_is_422(repository, limit) -> None:
    client, _, repository_id = repository
    response = preview(client, repository_id, "Any.java", limit)
    assert response.status_code == 422
    assert "details" in response.json()["error"]


def test_environment_default_chunk_size(repository, monkeypatch: pytest.MonkeyPatch) -> None:
    client, root, repository_id = repository
    (root / "Default.java").write_text(
        "class Default { void run() { one(); two(); three(); } }", encoding="utf-8"
    )
    monkeypatch.setenv("ARCHMIND_MAX_CHUNK_CHARS", "20")
    # Configuration is read when the application is constructed.
    configured = TestClient(create_app())
    registration = configured.post(
        "/api/v1/repositories", json={"name": "sample-repo", "source_alias": "sample"}
    )
    configured_id = registration.json()["repository_id"]
    response = preview(configured, configured_id, "Default.java")
    assert response.status_code == 200
    assert response.json()["total_chunks"] > 1
    assert preview(client, repository_id, "Default.java").json()["total_chunks"] == 1
