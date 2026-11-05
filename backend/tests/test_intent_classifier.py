"""Deterministic and structured intent classification contracts."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.errors import ApplicationError
from app.modules.agentic.classifier import (
    IntentClassification, IntentClassifier, CLASSIFIER_VERSION,
)
from app.modules.rag.llm_provider import (
    GeminiAnswerProvider, RagProviderError, RagProviderInvalidResponse,
    RagProviderRateLimited, RagProviderTimeout,
)
from app.prompts.loader import PromptLoader


def classifier(output=None, *, error=None):
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate_structured=MagicMock(return_value=output, side_effect=error),
    )
    return IntentClassifier(PromptLoader(), provider), provider


@pytest.mark.parametrize(("question", "intent"), [
    ("Where is OrderService implemented?", "CODE_LOOKUP"),
    ("Explain how createOrder works.", "CODE_EXPLANATION"),
    ("What depends on AuthenticationService?", "DEPENDENCY_ANALYSIS"),
    ("What architectural changes are required to replace RabbitMQ with Kafka?", "ARCHITECTURE_ANALYSIS"),
    ("Explain what depends on AuthenticationService.", "DEPENDENCY_ANALYSIS"),
])
def test_clear_intents_use_rules_without_gemini(question, intent):
    service, provider = classifier()
    decision = service.classify(question)
    assert decision.classification.intent == intent
    assert decision.classification.classifier_version == CLASSIFIER_VERSION
    assert decision.prompt_versions is None
    provider.generate_structured.assert_not_called()


def test_ambiguous_question_uses_released_prompt_and_structured_output():
    service, provider = classifier(IntentClassification(
        intent="UNKNOWN", routing_reason="Question is too vague.",
    ))
    decision = service.classify("Could you look at this?")
    assert decision.classification.intent == "UNKNOWN"
    assert decision.prompt_versions.model_dump() == {
        "name": "query_classifier", "system": "1.0.0", "user": "1.0.0",
    }
    assert decision.model_identifier == "gemini-2.5-flash"
    system, user, schema = provider.generate_structured.call_args.args
    assert "CODE_LOOKUP" in system
    assert "Could you look at this?" in user
    assert schema is IntentClassification


def test_classifier_prompt_version_selection_and_missing_release():
    service, _ = classifier(IntentClassification(intent="UNKNOWN", routing_reason="Vague."))
    assert service.classify("Something?").prompt_versions.system == "1.0.0"
    service, _ = classifier()
    service._system_version = "9.9.9"
    with pytest.raises(ApplicationError) as error:
        service.classify("Something?")
    assert error.value.code == "PROMPT_CONFIGURATION_ERROR"


@pytest.mark.parametrize(("invalid", "expected"), [
    ({"intent": "MADE_UP", "routing_reason": "x", "classifier_version": CLASSIFIER_VERSION}, "CLASSIFIER_INVALID_RESPONSE"),
    ({"intent": "UNKNOWN", "routing_reason": "x", "classifier_version": "wrong"}, "CLASSIFIER_INVALID_RESPONSE"),
])
def test_invalid_structured_output_is_controlled(invalid, expected):
    service, _ = classifier(invalid)
    with pytest.raises(ApplicationError) as error:
        service.classify("Something?")
    assert error.value.status_code == 502
    assert error.value.code == expected


@pytest.mark.parametrize(("failure", "status"), [
    (RagProviderError(), 502),
    (RagProviderInvalidResponse(), 502),
    (RagProviderRateLimited(), 429),
    (RagProviderTimeout(), 504),
])
def test_provider_failures_are_controlled(failure, status):
    service, _ = classifier(error=failure)
    with pytest.raises(ApplicationError) as error:
        service.classify("Something?")
    assert error.value.status_code == status


def test_shared_gemini_adapter_validates_classifier_schema():
    client = MagicMock()
    client.models.generate_content.return_value.parsed = {
        "intent": "UNKNOWN", "routing_reason": "Unclear request.",
        "classifier_version": CLASSIFIER_VERSION,
    }
    provider = GeminiAnswerProvider(
        api_key="test-only", model_identifier="gemini-2.5-flash",
        timeout_seconds=5, client=client,
    )
    result = provider.generate_structured("system", "user", IntentClassification)
    assert result.intent == "UNKNOWN"
    assert client.models.generate_content.call_args.kwargs["config"].response_schema is IntentClassification
