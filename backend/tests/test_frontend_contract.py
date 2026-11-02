"""Frontend project contract without a local database substitute."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.errors import ApplicationError
from app.main import create_app
from app.modules.repository_ingestion.persistence import ProjectRecord


class SharedProjectStore:
    def __init__(self):
        self.rows = {}

    def create(self, name, source_alias):
        if any(r.source_alias == source_alias or r.name.lower() == name.lower()
               for r in self.rows.values()):
            raise ApplicationError(409, "REPOSITORY_ALREADY_REGISTERED", "Repository already registered.")
        row = ProjectRecord(uuid4(), name, source_alias, "REGISTERED", datetime.now(timezone.utc))
        self.rows[row.repository_id] = row
        return row

    def get(self, repository_id):
        return self.rows.get(repository_id)

    def list(self, limit, offset):
        rows = sorted(self.rows.values(), key=lambda r: (r.registered_at, r.repository_id))
        return len(rows), rows[offset:offset + limit]


@pytest.fixture
def setup_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "approved"
    first = root / "first"
    second = root / "second"
    first.mkdir(parents=True)
    second.mkdir()
    (first / "First.java").write_text("class First {}")
    (second / "Second.java").write_text("class Second {}")
    monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(root)]))
    monkeypatch.setenv("ARCHMIND_REPOSITORY_ALIASES", json.dumps({"first": str(first), "second": str(second)}))
    monkeypatch.setenv("ARCHMIND_FRONTEND_ORIGINS", '["http://localhost:3000"]')
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = SharedProjectStore()

    def client():
        app = create_app()
        app.state.repository_service.attach_store(store)
        return TestClient(app)

    return client, store


def test_registration_listing_pagination_and_restart(setup_projects):
    make_client, _ = setup_projects
    first_client = make_client()
    first = first_client.post("/api/v1/repositories", json={
        "name": "Customer Portal", "source_alias": "first",
    })
    second = first_client.post("/api/v1/repositories", json={
        "name": "Payments API", "source_alias": "second",
    })
    assert first.status_code == second.status_code == 201
    first_id = first.json()["repository_id"]
    assert first.json()["name"] == "Customer Portal"
    assert first.json()["source_alias"] == "first"
    restarted = make_client()
    detail = restarted.get(f"/api/v1/repositories/{first_id}")
    assert detail.status_code == 200
    assert detail.json() == first.json()
    page = restarted.get("/api/v1/repositories?limit=1&offset=1")
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["repositories"] == [second.json()]
    assert restarted.get("/api/v1/repositories?limit=0").status_code == 422
    assert restarted.get("/api/v1/repositories?offset=-1").status_code == 422
    files = restarted.get(f"/api/v1/repositories/{first_id}/files")
    assert [item["path"] for item in files.json()["files"]] == ["First.java"]
    other_files = restarted.get(f"/api/v1/repositories/{second.json()['repository_id']}/files")
    assert [item["path"] for item in other_files.json()["files"]] == ["Second.java"]


def test_aliases_duplicate_errors_and_repository_scope(setup_projects):
    make_client, _ = setup_projects
    client = make_client()
    aliases = client.get("/api/v1/repositories/approved-aliases")
    assert aliases.status_code == 200
    assert aliases.json() == {"aliases": ["first", "second"]}
    assert "approved" not in aliases.text
    first = client.post("/api/v1/repositories", json={"name": "First project", "source_alias": "first"})
    assert first.status_code == 201
    duplicate = client.post("/api/v1/repositories", json={"name": "Another name", "source_alias": "first"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "REPOSITORY_ALREADY_REGISTERED"
    missing = client.post("/api/v1/repositories", json={"name": "Missing", "source_alias": "unknown"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "REPOSITORY_ALIAS_NOT_FOUND"
    invalid = client.post("/api/v1/repositories", json={"name": "No path", "source_alias": "second", "path": "/secret"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["details"]
    assert invalid.json()["error"]["request_id"] == invalid.headers["X-Request-ID"]
    assert client.get(f"/api/v1/repositories/{uuid4()}").status_code == 404
    unknown_route = client.get("/api/v1/no-such-route")
    assert unknown_route.status_code == 404
    assert unknown_route.json()["error"]["request_id"] == unknown_route.headers["X-Request-ID"]
    assert client.get(f"/api/v1/repositories/{first.json()['repository_id']}/files").json()["files"][0]["path"] == "First.java"


def test_cors_and_openapi(setup_projects):
    make_client, _ = setup_projects
    client = make_client()
    allowed = client.options("/api/v1/repositories", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
    })
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    denied = client.options("/api/v1/repositories", headers={
        "Origin": "https://unapproved.example", "Access-Control-Request-Method": "POST",
    })
    assert denied.status_code == 400
    schema = client.get("/openapi.json").json()
    assert schema["openapi"].startswith("3.")
    for route in ("/api/v1/repositories", "/api/v1/repositories/{repository_id}",
                  "/api/v1/repositories/approved-aliases"):
        assert route in schema["paths"]
    assert schema["paths"]["/api/v1/repositories"]["post"]["responses"]["422"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/ErrorResponse"


@pytest.mark.parametrize("origins", ['["*"]', '["http://localhost:3000/"]', 'not-json'])
def test_invalid_cors_configuration_is_rejected(monkeypatch, origins):
    monkeypatch.setenv("ARCHMIND_FRONTEND_ORIGINS", origins)
    with pytest.raises(ValueError):
        create_app()
