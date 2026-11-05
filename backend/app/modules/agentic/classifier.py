"""Cost-aware intent classification with released prompts for ambiguous queries."""

import re
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.core.observability import get_tracer
from app.errors import ApplicationError
from app.modules.rag.llm_provider import (
    RagProviderError, RagProviderInvalidResponse, RagProviderRateLimited,
    RagProviderTimeout,
)
from app.modules.rag.schemas import PromptVersions
from app.prompts.loader import PromptLoader
from app.prompts.registry import PromptError


Intent = Literal[
    "CODE_LOOKUP", "CODE_EXPLANATION", "DEPENDENCY_ANALYSIS",
    "ARCHITECTURE_ANALYSIS", "UNKNOWN",
]
CLASSIFIER_VERSION = "intent_supervisor_v1"


class IntentClassification(BaseModel):
    intent: Intent
    routing_reason: str = Field(min_length=1, max_length=240)
    classifier_version: str = CLASSIFIER_VERSION


class IntentDecision(BaseModel):
    classification: IntentClassification
    prompt_versions: PromptVersions | None = None
    model_identifier: str | None = None


class StructuredClassifierProvider(Protocol):
    model_identifier: str

    def generate_structured(
        self, system_prompt: str, user_prompt: str,
        response_schema: type[IntentClassification],
    ) -> IntentClassification: ...


_RULES: tuple[tuple[re.Pattern[str], Intent, str], ...] = (
    (re.compile(r"\b(?:what depends on|dependencies of|dependents of|what breaks if|what is affected if|impact of .+ changes)\b", re.I),
     "DEPENDENCY_ANALYSIS", "Explicit request for dependency relationships."),
    (re.compile(r"\b(?:architectural changes|architecture analysis|system architecture|replace .+ with .+|migration considerations)\b", re.I),
     "ARCHITECTURE_ANALYSIS", "Explicit request for architecture analysis."),
    (re.compile(r"^(?:where is|where are|where can i find|locate|find)\b", re.I),
     "CODE_LOOKUP", "Explicit request to locate source code."),
    (re.compile(r"^(?:explain|how does|how do)\b", re.I),
     "CODE_EXPLANATION", "Explicit request to explain code."),
)


class IntentClassifier:
    def __init__(
        self, prompt_loader: PromptLoader,
        provider: StructuredClassifierProvider,
        *, system_version: str | None = None, user_version: str | None = None,
    ) -> None:
        self._prompt_loader = prompt_loader
        self._provider = provider
        self._system_version = system_version
        self._user_version = user_version

    def classify(self, question: str) -> IntentDecision:
        for pattern, intent, reason in _RULES:
            if pattern.search(question):
                with get_tracer().span("intent_classification", metadata={
                    "classifier": "rules", "intent": intent,
                }) as span:
                    span.set(output={"intent": intent})
                return IntentDecision(classification=IntentClassification(
                    intent=intent, routing_reason=reason,
                ))
        try:
            prompt = self._prompt_loader.load(
                "query_classifier", system_version=self._system_version,
                user_version=self._user_version,
            )
            system_text, user_text = prompt.render(question=question)
        except PromptError as exc:
            raise ApplicationError(503, "PROMPT_CONFIGURATION_ERROR", "Classifier prompts are unavailable.") from exc
        with get_tracer().span(
            "intent_classification", as_type="generation",
            model=self._provider.model_identifier,
            metadata={
                "classifier": "llm",
                "generation_model": self._provider.model_identifier,
                "prompt_name": prompt.name,
                "system_prompt_version": prompt.system_version,
                "user_prompt_template_version": prompt.user_version,
            },
        ) as span:
            try:
                result = self._provider.generate_structured(
                    system_text, user_text, IntentClassification,
                )
            except RagProviderError as exc:
                span.record_error(exc)
                status = 429 if isinstance(exc, RagProviderRateLimited) else 504 if isinstance(exc, RagProviderTimeout) else 502
                code = "CLASSIFIER_INVALID_RESPONSE" if isinstance(exc, RagProviderInvalidResponse) else "CLASSIFIER_PROVIDER_FAILED"
                raise ApplicationError(status, code, "Intent classification failed.") from exc
            try:
                validated = IntentClassification.model_validate(result)
            except (TypeError, ValueError) as exc:
                raise ApplicationError(502, "CLASSIFIER_INVALID_RESPONSE", "Intent classification failed.") from exc
            if validated.classifier_version != CLASSIFIER_VERSION:
                raise ApplicationError(502, "CLASSIFIER_INVALID_RESPONSE", "Intent classification failed.")
            span.set(metadata={"intent": validated.intent}, output={"intent": validated.intent})
            return IntentDecision(
                classification=validated,
                prompt_versions=PromptVersions(
                    name=prompt.name, system=prompt.system_version, user=prompt.user_version,
                ),
                model_identifier=self._provider.model_identifier,
            )
