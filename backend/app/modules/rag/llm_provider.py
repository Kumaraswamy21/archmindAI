"""Gemini structured-output adapter for grounded answers."""

from time import perf_counter
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.observability import get_tracer
from app.core.retry import retry_transient
from app.modules.rag.schemas import GeneratedAnswer


class RagProviderError(Exception):
    code = "RAG_PROVIDER_FAILED"


class RagProviderTimeout(RagProviderError):
    code = "RAG_PROVIDER_TIMEOUT"


class RagProviderRateLimited(RagProviderError):
    code = "RAG_PROVIDER_RATE_LIMITED"


class RagProviderInvalidResponse(RagProviderError):
    code = "RAG_PROVIDER_INVALID_RESPONSE"


class AnswerProvider(Protocol):
    model_identifier: str

    def generate(self, system_prompt: str, user_prompt: str) -> GeneratedAnswer: ...


StructuredResponse = TypeVar("StructuredResponse", bound=BaseModel)


class GeminiAnswerProvider:
    def __init__(
        self, *, api_key: str, model_identifier: str,
        timeout_seconds: float, client: object | None = None,
    ) -> None:
        self.model_identifier = model_identifier
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

    def generate(self, system_prompt: str, user_prompt: str) -> GeneratedAnswer:
        return self.generate_structured(system_prompt, user_prompt, GeneratedAnswer)

    def generate_structured(
        self, system_prompt: str, user_prompt: str,
        response_schema: type[StructuredResponse],
    ) -> StructuredResponse:
        from google.genai import errors, types

        started = perf_counter()
        with get_tracer().span(
            "gemini.generation", as_type="generation", model=self.model_identifier,
            metadata={"generation_model": self.model_identifier},
        ) as span:
            def _call():
                try:
                    return self._client.models.generate_content(
                        model=self.model_identifier,
                        contents=user_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            response_mime_type="application/json",
                            response_schema=response_schema,
                            temperature=0,
                        ),
                    )
                except errors.APIError as exc:
                    if exc.code == 429:
                        raise RagProviderRateLimited("Gemini rate limit reached.") from None
                    if exc.code == 504:
                        raise RagProviderTimeout("Gemini answer generation timed out.") from None
                    raise RagProviderError("Gemini answer generation failed.") from None
                except (httpx.TimeoutException, TimeoutError):
                    raise RagProviderTimeout("Gemini answer generation timed out.") from None
                except httpx.TransportError:
                    raise RagProviderError("Gemini answer generation failed.") from None
                except Exception:
                    raise RagProviderError("Gemini answer generation failed.") from None

            try:
                response = retry_transient(_call)
            except RagProviderError as exc:
                span.record_error(exc)
                raise
            usage = _usage_from_gemini(response)
            span.set(
                metadata={"latency_ms": round(max(0.0, (perf_counter() - started) * 1000.0), 3)},
                usage=usage,
            )
            parsed = getattr(response, "parsed", None)
            try:
                if parsed is not None:
                    return response_schema.model_validate(parsed)
                return response_schema.model_validate_json(response.text)
            except (ValidationError, TypeError, ValueError, AttributeError):
                span.record_error(RagProviderInvalidResponse("Gemini returned an invalid structured answer."))
                raise RagProviderInvalidResponse("Gemini returned an invalid structured answer.") from None


def _usage_from_gemini(response: Any) -> dict[str, int] | None:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None
    mapping = (
        ("prompt_token_count", "prompt_tokens"),
        ("candidates_token_count", "completion_tokens"),
        ("total_token_count", "total_tokens"),
    )
    usage: dict[str, int] = {}
    for source, dest in mapping:
        value = getattr(meta, source, None)
        if isinstance(value, int):
            usage[dest] = value
    return usage or None
