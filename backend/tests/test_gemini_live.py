"""Opt-in, harmless Gemini smoke test with explicit credentials."""

import os

import pytest

from app.modules.vector_store.config import EmbeddingSettings
from app.modules.vector_store.embeddings.gemini import GeminiEmbeddingProvider


@pytest.mark.skipif(
    os.environ.get("ARCHMIND_RUN_GEMINI_SMOKE_TEST") != "1",
    reason="Live Gemini test requires explicit opt-in and a valid API key.",
)
def test_live_query_embedding_dimension() -> None:
    settings = EmbeddingSettings()
    provider = GeminiEmbeddingProvider(
        api_key=settings.gemini_api_key.get_secret_value(),
        model_identifier=settings.embedding_model,
        output_dimension=settings.embedding_dimension,
        timeout_seconds=settings.embedding_request_timeout_seconds,
    )
    vector = provider.embed_query("test source symbol")
    assert len(vector) == settings.embedding_dimension
