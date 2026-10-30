"""Static Java edges, repository isolation, idempotency, and API contract."""

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.models import DependencyRelationshipRow

from app.errors import ApplicationError, handle_application_error
from app.main import create_app
from app.modules.dependency_intelligence.router import create_router
from app.modules.dependency_intelligence.service import DependencyIntelligenceService
from app.modules.dependency_intelligence.extractor import JavaDependencyExtractor, JavaSourceUnit
from app.modules.code_intelligence.java_parser import JavaParserAdapter
from app.modules.code_intelligence.symbol_extractor import JavaSymbolExtractor
from app.modules.repository_ingestion.registry import RepositoryRegistry
from app.modules.repository_ingestion.schemas import RepositoryRegistrationRequest
from app.modules.repository_ingestion.security import RepositoryConfiguration
from app.modules.repository_ingestion.service import RepositoryService


SOURCE = """package demo;
import demo.OrderService;
import external.Unknown;
@Service class Controller extends Base implements Worker {
    @Autowired private OrderService order;
    private final Base base;
    Controller(Base base) { this.base = base; order.create(); helper(); }
    void helper() { order.create(); this.base.work(); unknown.call(); }
}
class OrderService { void create() {} }
class Base { void work() {} }
interface Worker {}
"""


class MemoryDependencies:
    def __init__(self):
        self.data = {}

    def replace_snapshot(self, record, relationships):
        self.data[record.repository_id] = {edge.relationship_id: edge for edge in relationships}
        return len(relationships)

    def list_relationships(self, repository_id, *, symbol, relationship_type, direction, limit):
        if repository_id not in self.data:
            return None
        values = sorted(self.data[repository_id].values(), key=lambda edge: (
            edge.file_path, edge.start_line, edge.end_line, edge.relationship_id,
        ))
        if relationship_type:
            values = [edge for edge in values if edge.relationship_type == relationship_type]
        if symbol:
            values = [edge for edge in values if (
                direction in ("outgoing", "both") and edge.source_symbol == symbol
            ) or (
                direction in ("incoming", "both") and edge.target_symbol == symbol
            )]
        return len(values), values[:limit]


@pytest.fixture
def setup(tmp_path: Path):
    approved = tmp_path / "approved"
    first = approved / "first"
    second = approved / "second"
    first.mkdir(parents=True)
    second.mkdir()
    java = first / "Controller.java"
    java.write_text(SOURCE)
    (first / "ignore.py").write_text("pass\n")
    config = RepositoryConfiguration(
        aliases={"first": first, "second": second},
        approved_roots=(approved,), max_files=100, max_file_size_bytes=100000,
    )
    repositories = RepositoryService(config, RepositoryRegistry())
    first_id = repositories.register(RepositoryRegistrationRequest(
        name="first", source_alias="first",
    )).repository_id
    second_id = repositories.register(RepositoryRegistrationRequest(
        name="second", source_alias="second",
    )).repository_id
    store = MemoryDependencies()
    service = DependencyIntelligenceService(repositories, store)
    return service, store, first_id, second_id, java


def test_extracts_supported_relationships_and_source_locations(setup):
    service, store, repository_id, _, java = setup
    before = (java.read_bytes(), java.stat().st_mtime_ns)
    result = service.scan(repository_id)
    assert result.status == "COMPLETED"
    edges = list(store.data[repository_id].values())
    assert result.total_relationships == len(edges)
    assert {edge.relationship_type for edge in edges} == {
        "IMPORTS", "EXTENDS", "IMPLEMENTS", "INJECTS", "CALLS",
    }
    assert any(edge.relationship_type == "IMPORTS" and edge.target_symbol == "demo.OrderService"
               and edge.resolution_status == "RESOLVED" and edge.start_line == 2 for edge in edges)
    assert any(edge.relationship_type == "IMPORTS" and edge.target_symbol == "external.Unknown"
               and edge.resolution_status == "UNRESOLVED" and edge.start_line == 3 for edge in edges)
    assert any(edge.relationship_type == "EXTENDS" and edge.target_symbol == "demo.Base"
               and edge.resolution_status == "RESOLVED" and edge.start_line == 4 for edge in edges)
    assert any(edge.relationship_type == "IMPLEMENTS" and edge.target_symbol == "demo.Worker"
               and edge.resolution_status == "RESOLVED" for edge in edges)
    assert any(edge.relationship_type == "INJECTS" and edge.target_symbol == "demo.OrderService"
               and edge.start_line == 5 for edge in edges)
    assert any(edge.relationship_type == "INJECTS" and edge.target_symbol == "demo.Base"
               and edge.start_line == 7 for edge in edges)
    assert any(edge.relationship_type == "CALLS" and edge.target_symbol == "demo.OrderService.create"
               and edge.resolution_status == "RESOLVED" for edge in edges)
    assert any(edge.relationship_type == "CALLS" and edge.target_symbol == "demo.Base.work"
               and edge.resolution_status == "RESOLVED" and edge.start_line == 8 for edge in edges)
    assert any(edge.relationship_type == "CALLS" and edge.target_symbol == "unknown.call"
               and edge.resolution_status == "UNRESOLVED" for edge in edges)
    assert all(edge.repository_id == repository_id and edge.file_path == "Controller.java"
               and edge.end_line >= edge.start_line >= 1 for edge in edges)
    assert (java.read_bytes(), java.stat().st_mtime_ns) == before


def test_repeat_scan_is_idempotent_and_repository_scoped(setup):
    service, store, first_id, second_id, _ = setup
    first = service.scan(first_id)
    ids = set(store.data[first_id])
    second = service.scan(first_id)
    assert first.total_relationships == second.total_relationships
    assert set(store.data[first_id]) == ids
    empty = service.scan(second_id)
    assert empty.total_relationships == 0
    assert store.data[second_id] == {}
    assert service.list_relationships(
        first_id, symbol="demo.OrderService", relationship_type="CALLS",
        direction="incoming", limit=10,
    ).total_relationships == 0
    assert service.list_relationships(
        first_id, symbol="demo.OrderService.create", relationship_type="CALLS",
        direction="incoming", limit=10,
    ).total_relationships == 2
    assert service.list_relationships(
        second_id, symbol=None, relationship_type=None, direction="both", limit=10,
    ).relationships == []
    with pytest.raises(ApplicationError) as error:
        service.scan(uuid4())
    assert error.value.status_code == 404


def test_malformed_java_leaves_previous_snapshot_untouched(setup):
    service, store, repository_id, _, java = setup
    service.scan(repository_id)
    ids = set(store.data[repository_id])
    java.write_text("class Broken { void x( {")
    with pytest.raises(ApplicationError) as error:
        service.scan(repository_id)
    assert error.value.code == "JAVA_SYNTAX_INVALID"
    assert set(store.data[repository_id]) == ids


def test_api_scan_filter_and_openapi_contract(setup):
    service, _, repository_id, _, _ = setup
    app = FastAPI()
    app.add_exception_handler(ApplicationError, handle_application_error)
    app.include_router(create_router(service))
    client = TestClient(app)
    base = f"/api/v1/repositories/{repository_id}/dependencies"
    scanned = client.post(base + "/scan")
    assert scanned.status_code == 200
    assert scanned.json()["total_relationships"] > 0
    listed = client.get(base, params={
        "symbol": "demo.OrderService.create", "relationship_type": "CALLS",
        "direction": "incoming", "limit": 1,
    })
    assert listed.status_code == 200
    assert listed.json()["total_relationships"] == 2
    assert len(listed.json()["relationships"]) == 1
    assert client.get(base, params={"direction": "sideways"}).status_code == 422
    assert client.get(f"/api/v1/repositories/{uuid4()}/dependencies").status_code == 404
    spec = client.get("/openapi.json").json()
    assert "/api/v1/repositories/{repository_id}/dependencies/scan" in spec["paths"]
    assert "/api/v1/repositories/{repository_id}/dependencies" in spec["paths"]
    assert spec["paths"]["/api/v1/repositories/{repository_id}/dependencies"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("DependencyListResponse")


def test_unconfigured_app_documents_endpoints():
    client = TestClient(create_app())
    repository_id = uuid4()
    base = f"/api/v1/repositories/{repository_id}/dependencies"
    assert client.get("/docs").status_code == 200
    assert client.post(base + "/scan").status_code == 503
    assert client.get(base).status_code == 503


def test_dependency_migration_and_repository_scoped_indexes():
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    revisions = ScriptDirectory.from_config(config)
    assert revisions.get_current_head() == "20261031_0005"
    assert revisions.get_revision("20261031_0005").down_revision == "20261030_0004"
    table = DependencyRelationshipRow.__table__
    assert table.c.repository_id.foreign_keys
    assert {index.name for index in table.indexes} == {
        "ix_dependency_repository_source",
        "ix_dependency_repository_target",
        "ix_dependency_repository_type",
    }
    assert all(next(iter(index.columns)).name == "repository_id" for index in table.indexes)


def test_ambiguous_receiver_is_unresolved_instead_of_guessed():
    source = """package demo;
class Service { void run() {} }
class Controller {
  @Autowired Service service;
  void execute() { Service service = null; service.run(); Service.run(); }
}
"""
    syntax = JavaParserAdapter().parse_tree(source)
    unit = JavaSourceUnit("Controller.java", syntax, JavaSymbolExtractor().extract(syntax))
    edges = JavaDependencyExtractor().extract(uuid4(), [unit])
    calls = [edge for edge in edges if edge.relationship_type == "CALLS"]
    assert len(calls) == 2
    assert all(edge.resolution_status == "UNRESOLVED" for edge in calls)
    assert {edge.target_symbol for edge in calls} == {"service.run", "Service.run"}


def test_conflicting_visible_type_names_remain_unresolved():
    parser = JavaParserAdapter()
    extractor = JavaSymbolExtractor()
    sources = {
        "Local.java": "package demo; class Service {}",
        "External.java": "package external; class Service {}",
        "Controller.java": "package demo; import external.Service; class Controller { @Autowired Service service; }",
    }
    units = []
    for path, content in sources.items():
        syntax = parser.parse_tree(content)
        units.append(JavaSourceUnit(path, syntax, extractor.extract(syntax)))
    edges = JavaDependencyExtractor().extract(uuid4(), units)
    injection = next(edge for edge in edges if edge.relationship_type == "INJECTS")
    assert injection.target_symbol == "Service"
    assert injection.resolution_status == "UNRESOLVED"
