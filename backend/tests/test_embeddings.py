"""Gemini adapter and embedding orchestration without live credentials."""

import hashlib
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.errors import ApplicationError
from app.main import create_app
from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.modules.vector_store.config import EmbeddingSettings
from app.modules.vector_store.embeddings.base import (
    EmbeddingDimensionError,
    EmbeddingRateLimitError,
    EmbeddingTimeoutError,
)
from app.modules.vector_store.embeddings.gemini import GeminiEmbeddingProvider
from app.modules.vector_store.input_builder import (
    EMBEDDING_INPUT_VERSION,
    build_document_input,
    embedding_input_hash,
)
from app.modules.vector_store.service import EmbeddingService
from app.modules.vector_store.schemas import EmbeddingGenerationResponse


def chunk(name: str = "run", content: str = "void run() {}") -> JavaChunk:
    return JavaChunk(
        chunk_id=hashlib.sha256(f"{name}{content}".encode()).hexdigest(),
        repository_id=uuid4(),
        file_path="src/OrderService.java",
        language="java",
        chunk_type="method",
        parent_chunk_id=None,
        sequence=None,
        split_strategy=None,
        start_byte=12,
        end_byte=12 + len(content.encode()),
        symbol_name=name,
        qualified_name=f"demo.OrderService.{name}",
        parent_symbol="OrderService",
        package_name="demo",
        imports=["java.util.List"],
        annotations=["@Service"],
        start_line=2,
        end_line=2,
        content=content,
        contextual_header=f"Package: demo | Class: OrderService | Method: {name} | Annotation: @Service",
    )


class FakeChunkStore:
    def __init__(self, chunks):
        self.chunks = chunks
        self.found = True

    def list_chunks(self, _repository_id, limit, offset):
        if not self.found:
            return None
        return len(self.chunks), self.chunks[offset : offset + limit]


class FakeMetadataStore:
    def __init__(self):
        self.records = {}

    def get_many(self, input_hashes, model, dimension):
        return {
            chunk_id: self.records[(chunk_id, model, dimension, input_hash)]
            for chunk_id, input_hash in input_hashes.items()
            if (chunk_id, model, dimension, input_hash) in self.records
        }

    def upsert_many(self, records):
        for record in records:
            self.records[(record.chunk_id, record.model_identifier, record.output_dimension, record.input_hash)] = record


class FakeProvider:
    def __init__(self, outcomes=None, model="gemini-embedding-2", dimension=128):
        self.model_identifier = model
        self.output_dimension = dimension
        self.calls = []
        self.outcomes = list(outcomes or [])

    def embed_documents(self, texts):
        self.calls.append(texts)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return [[0.1] * self.output_dimension for _ in texts]

    def embed_query(self, text):
        return [0.1] * self.output_dimension


def make_service(chunks, provider=None, metadata=None, **settings_values):
    provider = provider or FakeProvider()
    metadata = metadata or FakeMetadataStore()
    settings = EmbeddingSettings(
        gemini_api_key="fake-secret",
        embedding_dimension=provider.output_dimension,
        embedding_batch_size=2,
        **settings_values,
    )
    repository = SimpleNamespace(registered_record=lambda _id: object())
    delays = []
    service = EmbeddingService(
        repository, FakeChunkStore(chunks), metadata, provider, settings,
        sleep=delays.append,
    )
    return service, provider, metadata, delays


def test_document_input_is_exact_deterministic_and_versioned() -> None:
    item = chunk()
    expected = (
        "title: demo.OrderService.run | text: Package: demo | Class: OrderService "
        "| Method: run | Annotation: @Service\n"
        "File: src/OrderService.java | Kind: method\nvoid run() {}"
    )
    assert build_document_input(item) == expected
    assert embedding_input_hash(expected) == embedding_input_hash(expected)
    assert embedding_input_hash(expected) != embedding_input_hash(expected + " ")
    assert EMBEDDING_INPUT_VERSION == "1.0.0"
    assert "java.util.List" not in expected


def test_embedding_configuration_bounds_and_secret_masking() -> None:
    settings = EmbeddingSettings(gemini_api_key="PrivateKey")
    assert settings.embedding_model == "gemini-embedding-2"
    assert settings.embedding_dimension == 768
    assert "PrivateKey" not in repr(settings)
    with pytest.raises(ValidationError):
        EmbeddingSettings(gemini_api_key="key", embedding_batch_size=0)
    with pytest.raises(ValidationError):
        EmbeddingSettings(gemini_api_key="key", embedding_dimension=64)


def test_gemini_uses_distinct_content_objects_and_validates_dimensions() -> None:
    calls = []
    class Models:
        def embed_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.1] * 128) for _ in kwargs["contents"]]
            )
    provider = GeminiEmbeddingProvider(
        api_key="fake", model_identifier="gemini-embedding-2",
        output_dimension=128, timeout_seconds=5,
        client=SimpleNamespace(models=Models()),
    )
    assert len(provider.embed_documents(["one", "two"])) == 2
    assert len(calls[0]["contents"]) == 2
    assert calls[0]["contents"][0].parts[0].text == "one"
    assert calls[0]["contents"][1].parts[0].text == "two"
    assert calls[0]["config"].output_dimensionality == 128
    assert provider.embed_documents([]) == []
    assert provider.embed_query("OrderService")[0] == 0.1
    assert calls[1]["contents"][0].parts[0].text == (
        "task: code retrieval | query: OrderService"
    )


def test_gemini_maps_rate_limit_timeout_and_bad_dimension() -> None:
    def provider_for(error=None, dimension=128):
        class Models:
            def embed_content(self, **_kwargs):
                if error is not None:
                    raise error
                return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0] * dimension)])
        return GeminiEmbeddingProvider(
            api_key="fake", model_identifier="gemini-embedding-2",
            output_dimension=128, timeout_seconds=5,
            client=SimpleNamespace(models=Models()),
        )
    with pytest.raises(EmbeddingRateLimitError):
        provider_for(errors.APIError(429, {"message": "quota"})).embed_documents(["x"])
    with pytest.raises(EmbeddingTimeoutError):
        provider_for(httpx.ReadTimeout("timed out")).embed_documents(["x"])
    with pytest.raises(EmbeddingDimensionError):
        provider_for(dimension=127).embed_documents(["x"])
    class ZeroModels:
        def embed_content(self, **_kwargs):
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.0] * 128)])
    zero_provider = GeminiEmbeddingProvider(
        api_key="fake", model_identifier="gemini-embedding-2",
        output_dimension=128, timeout_seconds=5,
        client=SimpleNamespace(models=ZeroModels()),
    )
    with pytest.raises(EmbeddingDimensionError):
        zero_provider.embed_documents(["x"])


def test_batch_generation_skips_unchanged_and_empty_content() -> None:
    items = [chunk("one"), chunk("two"), chunk("empty", "   ")]
    service, provider, metadata, _ = make_service(items)
    repository_id = uuid4()
    first = service.generate(repository_id)
    assert (first.total_chunks, first.generated, first.skipped, first.failed) == (3, 2, 1, 0)
    assert len(provider.calls) == 1
    assert len(provider.calls[0]) == 2
    assert {record.status for record in metadata.records.values()} == {"COMPLETED", "SKIPPED_EMPTY"}
    second = service.generate(repository_id)
    assert (second.generated, second.skipped, second.failed) == (0, 3, 0)
    assert len(provider.calls) == 1


def test_model_change_triggers_regeneration() -> None:
    metadata = FakeMetadataStore()
    item = chunk()
    first, _, _, _ = make_service([item], metadata=metadata)
    assert first.generate(uuid4()).generated == 1
    changed_provider = FakeProvider(model="gemini-embedding-2-revision")
    changed, _, _, _ = make_service([item], provider=changed_provider, metadata=metadata)
    assert changed.generate(uuid4()).generated == 1
    assert len(changed_provider.calls) == 1


def test_dimension_mismatch_is_failed_without_retry() -> None:
    provider = FakeProvider(outcomes=[[[0.1] * 127]])
    service, _, metadata, delays = make_service([chunk()], provider=provider)
    with pytest.raises(ApplicationError) as error:
        service.generate(uuid4())
    assert error.value.status_code == 502
    assert error.value.code == "EMBEDDING_DIMENSION_MISMATCH"
    assert len(provider.calls) == 1
    assert delays == []
    assert next(iter(metadata.records.values())).status == "FAILED"


def test_timeout_and_rate_limit_use_bounded_backoff() -> None:
    timeout = FakeProvider(outcomes=[EmbeddingTimeoutError()] * 3)
    service, _, metadata, delays = make_service([chunk()], provider=timeout)
    with pytest.raises(ApplicationError) as error:
        service.generate(uuid4())
    assert error.value.status_code == 504
    assert len(timeout.calls) == 3
    assert delays == [0.25, 0.5]
    assert next(iter(metadata.records.values())).error_code == "EMBEDDING_PROVIDER_TIMEOUT"

    rate = FakeProvider(outcomes=[EmbeddingRateLimitError(), [[0.1] * 128]])
    service, _, _, delays = make_service([chunk()], provider=rate)
    assert service.generate(uuid4()).generated == 1
    assert len(rate.calls) == 2
    assert delays == [0.25]

    exhausted = FakeProvider(outcomes=[EmbeddingRateLimitError()] * 3)
    service, _, _, _ = make_service([chunk()], provider=exhausted)
    with pytest.raises(ApplicationError) as error:
        service.generate(uuid4())
    assert error.value.status_code == 429


def test_partial_batch_failure_returns_counts_and_retries_failed_only() -> None:
    items = [chunk("one"), chunk("two"), chunk("three")]
    provider = FakeProvider(
        outcomes=[EmbeddingDimensionError(), [[0.1] * 128]]
    )
    service, _, metadata, _ = make_service(items, provider=provider)
    result = service.generate(uuid4())
    assert (result.generated, result.skipped, result.failed) == (1, 0, 2)
    assert [record.status for record in metadata.records.values()].count("FAILED") == 2
    assert [record.status for record in metadata.records.values()].count("COMPLETED") == 1
    assert len(provider.calls) == 2


def test_changed_input_failure_keeps_previous_completed_vector() -> None:
    original = chunk("run", "void run() {}")
    metadata = FakeMetadataStore()
    first, _, _, _ = make_service([original], metadata=metadata)
    assert first.generate(uuid4()).generated == 1
    old_record = next(iter(metadata.records.values()))
    assert old_record.embedding is not None

    # Keep the chunk ID to exercise the historical input-hash key directly.
    changed = original.model_copy(update={"content": "void run() { changed(); }"})
    provider = FakeProvider(outcomes=[EmbeddingDimensionError()])
    second, _, _, _ = make_service([changed], provider=provider, metadata=metadata)
    with pytest.raises(ApplicationError):
        second.generate(uuid4())
    assert len(metadata.records) == 2
    assert metadata.records[
        (original.chunk_id, old_record.model_identifier,
         old_record.output_dimension, old_record.input_hash)
    ].embedding is not None
    assert any(record.status == "FAILED" for record in metadata.records.values())


def test_missing_repository_and_empty_repository() -> None:
    service, _, _, _ = make_service([])
    assert service.generate(uuid4()).total_chunks == 0
    service._chunk_store.found = False
    service._repository_service.registered_record = lambda _id: None
    with pytest.raises(ApplicationError) as error:
        service.generate(uuid4())
    assert error.value.status_code == 404


def test_database_failure_is_controlled() -> None:
    service, _, _, _ = make_service([chunk()])
    def fail(*_args):
        raise OperationalError("SELECT", {}, Exception("private database detail"))
    service._chunk_store.list_chunks = fail
    with pytest.raises(ApplicationError) as error:
        service.generate(uuid4())
    assert error.value.status_code == 503
    assert error.value.code == "DATABASE_UNAVAILABLE"
    assert "private database detail" not in error.value.message


def test_embeddings_openapi_and_unconfigured_response() -> None:
    client = TestClient(create_app())
    path = f"/api/v1/repositories/{uuid4()}/embeddings"
    response = client.post(path)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EMBEDDING_NOT_CONFIGURED"
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/repositories/{repository_id}/embeddings"]["post"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EmbeddingGenerationResponse"
    }
    assert {"404", "429", "502", "503", "504"} <= set(operation["responses"])
    response_fields = schema["components"]["schemas"]["EmbeddingGenerationResponse"]["properties"]
    assert set(response_fields) == {
        "repository_id", "total_chunks", "generated", "skipped", "failed", "model"
    }
    assert "vector" not in json_dump(schema["components"]["schemas"]["EmbeddingGenerationResponse"])


def test_embeddings_api_success_has_counts_without_vectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://placeholder:placeholder@db.example.invalid/archmind_test",
    )
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    repository_id = uuid4()
    monkeypatch.setattr(
        EmbeddingService,
        "generate",
        lambda _self, _id: EmbeddingGenerationResponse(
            repository_id=repository_id,
            total_chunks=2, generated=1, skipped=1, failed=0,
            model="gemini-embedding-2",
        ),
    )
    client = TestClient(create_app())
    response = client.post(f"/api/v1/repositories/{repository_id}/embeddings")
    assert response.status_code == 200
    assert response.json() == {
        "repository_id": str(repository_id), "total_chunks": 2,
        "generated": 1, "skipped": 1, "failed": 0,
        "model": "gemini-embedding-2",
    }


def json_dump(value):
    import json
    return json.dumps(value)
