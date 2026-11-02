"""Java symbol extraction through registered repository files."""

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


def symbols(client: TestClient, repository_id: str, path: str):
    return client.get(
        f"/api/v1/repositories/{repository_id}/symbols",
        params={"file_path": path},
    )


def test_package_imports_constructor_signature_parameters_and_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    source = root / "src" / "OrderService.java"
    source.parent.mkdir()
    source.write_text(
        "package com.example;\n"
        "import org.springframework.stereotype.Service;\n"
        "import org.springframework.transaction.annotation.Transactional;\n"
        "import static java.util.Collections.emptyList;\n"
        "@Service\n"
        "public class OrderService {\n"
        "  @Transactional\n"
        "  public OrderService() {}\n"
        "  @GetMapping(\"/orders\")\n"
        "  public Order createOrder(OrderRequest request, String... tags) {\n"
        "    return null;\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    before = source.stat()

    response = symbols(client, repository_id, "src/OrderService.java")

    assert response.status_code == 200
    assert response.json() == {
        "file_path": "src/OrderService.java",
        "package": "com.example",
        "imports": [
            "org.springframework.stereotype.Service",
            "org.springframework.transaction.annotation.Transactional",
            "static java.util.Collections.emptyList",
        ],
        "symbols": [
            {
                "name": "OrderService",
                "kind": "class",
                "qualified_name": "com.example.OrderService",
                "enclosing_type": None,
                "annotations": ["@Service"],
                "start_line": 5,
                "end_line": 13,
            },
            {
                "name": "OrderService",
                "kind": "constructor",
                "qualified_name": "com.example.OrderService.OrderService",
                "enclosing_type": "com.example.OrderService",
                "signature": "public OrderService()",
                "parameters": [],
                "annotations": ["@Transactional"],
                "start_line": 7,
                "end_line": 8,
            },
            {
                "name": "createOrder",
                "kind": "method",
                "qualified_name": "com.example.OrderService.createOrder",
                "enclosing_type": "com.example.OrderService",
                "signature": "public Order createOrder(OrderRequest request, String... tags)",
                "parameters": [
                    {"name": "request", "type": "OrderRequest"},
                    {"name": "tags", "type": "String..."},
                ],
                "annotations": ['@GetMapping("/orders")'],
                "start_line": 9,
                "end_line": 12,
            },
        ],
    }
    assert source.stat().st_mtime_ns == before.st_mtime_ns
    assert str(root) not in response.text


def test_interfaces_nested_types_overloads_and_spring_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "Api.java").write_text(
        "package demo;\n"
        "@RestController\n"
        "class Api {\n"
        "  @PostMapping(\"/orders\")\n"
        "  void save(String id) {}\n"
        "  void find(int id) {}\n"
        "  void find(String id) {}\n"
        "  @Repository\n"
        "  static class Inner { Inner() {} }\n"
        "  @Service\n"
        "  interface Nested { void call(); }\n"
        "}\n",
        encoding="utf-8",
    )

    response = symbols(client, repository_id, "Api.java")

    assert response.status_code == 200
    items = response.json()["symbols"]
    assert [(item["kind"], item["name"]) for item in items] == [
        ("class", "Api"),
        ("method", "save"),
        ("method", "find"),
        ("method", "find"),
        ("class", "Inner"),
        ("constructor", "Inner"),
        ("interface", "Nested"),
        ("method", "call"),
    ]
    assert items[0]["annotations"] == ["@RestController"]
    assert items[1]["annotations"] == ['@PostMapping("/orders")']
    assert items[2]["qualified_name"] == items[3]["qualified_name"] == "demo.Api.find"
    assert items[2]["signature"] == "void find(int id)"
    assert items[3]["signature"] == "void find(String id)"
    assert items[4]["qualified_name"] == "demo.Api.Inner"
    assert items[4]["enclosing_type"] == "demo.Api"
    assert items[4]["annotations"] == ["@Repository"]
    assert items[5]["qualified_name"] == "demo.Api.Inner.Inner"
    assert items[5]["enclosing_type"] == "demo.Api.Inner"
    assert items[6]["qualified_name"] == "demo.Api.Nested"
    assert items[6]["annotations"] == ["@Service"]
    assert items[7]["signature"] == "void call()"
    assert items[7]["enclosing_type"] == "demo.Api.Nested"


def test_empty_java_file_has_no_symbols(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "Empty.java").write_bytes(b"")

    response = symbols(client, repository_id, "Empty.java")

    assert response.status_code == 200
    assert response.json() == {
        "file_path": "Empty.java",
        "package": None,
        "imports": [],
        "symbols": [],
    }


def test_multiline_generic_signature_and_method_annotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "QueryService.java").write_text(
        "class QueryService {\n"
        "  @Transactional\n"
        "  public <T> java.util.List<T> find(\n"
        "      String id,\n"
        "      java.util.List<T> items\n"
        "  ) throws Exception { return null; }\n"
        "}\n",
        encoding="utf-8",
    )

    response = symbols(client, repository_id, "QueryService.java")

    assert response.status_code == 200
    method = response.json()["symbols"][1]
    assert method["annotations"] == ["@Transactional"]
    assert method["signature"] == (
        "public <T> java.util.List<T> find(String id, java.util.List<T> items) throws Exception"
    )
    assert method["parameters"] == [
        {"name": "id", "type": "String"},
        {"name": "items", "type": "java.util.List<T>"},
    ]
    assert (method["start_line"], method["end_line"]) == (2, 6)


def test_malformed_java_returns_recoverable_symbols(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / "Broken.java").write_text(
        "package demo;\nclass Good { void ok() {} }\nclass Broken { void bad( {",
        encoding="utf-8",
    )

    response = symbols(client, repository_id, "Broken.java")

    assert response.status_code == 200
    assert response.json()["package"] == "demo"
    assert [(item["kind"], item["name"]) for item in response.json()["symbols"]] == [
        ("class", "Good"), ("method", "ok")
    ]


@pytest.mark.parametrize(
    ("filename", "code"),
    [("script.py", "UNSUPPORTED_PARSE_LANGUAGE"), ("notes.txt", "UNSUPPORTED_SOURCE_FILE")],
)
def test_unsupported_source_returns_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str, code: str
) -> None:
    client, root, repository_id = registered_repository(tmp_path, monkeypatch)
    (root / filename).write_text("content", encoding="utf-8")

    response = symbols(client, repository_id, filename)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


def test_unknown_repository_and_invalid_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, repository_id = registered_repository(tmp_path, monkeypatch)
    unknown = symbols(client, str(uuid4()), "Source.java")
    invalid = symbols(client, repository_id, "../outside.java")

    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "REPOSITORY_NOT_FOUND"
    assert invalid.status_code == 403
    assert invalid.json()["error"]["code"] == "INVALID_SOURCE_PATH"


def test_symbols_openapi_schema_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = registered_repository(tmp_path, monkeypatch)
    assert client.get("/docs").status_code == 200
    schema_response = client.get("/openapi.json")
    assert schema_response.status_code == 200
    schema = schema_response.json()
    operation = schema["paths"]["/api/v1/repositories/{repository_id}/symbols"]["get"]

    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/JavaSymbolsResponse"
    }
    assert {parameter["name"] for parameter in operation["parameters"]} == {
        "repository_id", "file_path"
    }
    for status in ("400", "403", "404", "413", "503"):
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
    assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
    components = schema["components"]["schemas"]
    assert set(components["JavaSymbolsResponse"]["required"]) == {
        "file_path", "package", "imports", "symbols"
    }
    assert set(components["JavaSymbolParameter"]["required"]) == {"name", "type"}
    assert "signature" in components["JavaCallableSymbol"]["required"]
    assert "enclosing_type" in components["JavaTypeSymbol"]["required"]
