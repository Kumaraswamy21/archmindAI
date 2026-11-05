"""Google Gen AI SDK adapter for distinct Gemini document and query embeddings."""

import math

import httpx

from app.core.observability import get_tracer
from app.modules.vector_store.embeddings.base import (
    EmbeddingDimensionError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingTimeoutError,
    EmbeddingUnavailableError,
)


class GeminiEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model_identifier: str,
        output_dimension: int,
        timeout_seconds: float,
        client: object | None = None,
    ) -> None:
        self.model_identifier = model_identifier
        self.output_dimension = output_dimension
        if client is None:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=int(timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        self._client = client

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        from google.genai import types

        contents = [
            types.Content(parts=[types.Part.from_text(text=text)])
            for text in texts
        ]
        response = self._request(contents)
        embeddings = getattr(response, "embeddings", None) or []
        if len(embeddings) != len(texts):
            raise EmbeddingDimensionError("Embedding response count did not match input count.")
        return [self._validate_vector(getattr(item, "values", None)) for item in embeddings]

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("Query text must not be empty.")
        from google.genai import types

        with get_tracer().span(
            "gemini.embedding", as_type="embedding", model=self.model_identifier,
            metadata={"embedding_model": self.model_identifier},
        ):
            content = types.Content(
                parts=[types.Part.from_text(text=f"task: code retrieval | query: {text}")]
            )
            response = self._request([content])
            embeddings = getattr(response, "embeddings", None) or []
            if len(embeddings) != 1:
                raise EmbeddingDimensionError("Embedding response count did not match input count.")
            return self._validate_vector(getattr(embeddings[0], "values", None))

    def _request(self, contents: list[object]) -> object:
        from google.genai import errors, types

        try:
            return self._client.models.embed_content(
                model=self.model_identifier,
                contents=contents,
                config=types.EmbedContentConfig(
                    output_dimensionality=self.output_dimension
                ),
            )
        except errors.APIError as exc:
            if exc.code == 429:
                raise EmbeddingRateLimitError("Gemini rate limit reached.") from None
            if exc.code in {500, 502, 503, 504}:
                raise EmbeddingUnavailableError("Gemini service is unavailable.") from None
            raise EmbeddingProviderError("Gemini embedding request failed.") from None
        except (httpx.TimeoutException, TimeoutError):
            raise EmbeddingTimeoutError("Gemini embedding request timed out.") from None
        except httpx.TransportError:
            raise EmbeddingUnavailableError("Gemini transport is unavailable.") from None
        except Exception:
            raise EmbeddingProviderError("Gemini embedding request failed.") from None

    def _validate_vector(self, values: object) -> list[float]:
        if not isinstance(values, (list, tuple)) or len(values) != self.output_dimension:
            raise EmbeddingDimensionError("Gemini returned an unexpected embedding dimension.")
        try:
            vector = [float(value) for value in values]
        except (TypeError, ValueError):
            raise EmbeddingDimensionError("Gemini returned invalid embedding values.") from None
        if not all(math.isfinite(value) for value in vector) or not any(vector):
            raise EmbeddingDimensionError("Gemini returned invalid embedding values.")
        return vector
