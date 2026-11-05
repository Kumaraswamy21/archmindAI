"""Grounded specialist evidence, citations, prompt releases, and parallel merge."""

from threading import Barrier
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.errors import ApplicationError
from app.modules.agentic.analysis_common import AgentEvidence, PromptedAnalyzer, prepare_evidence
from app.modules.agentic.evidence_verification import EvidenceVerifier
from app.modules.agentic.architecture_analysis import ArchitectureAnalysisAgent
from app.modules.agentic.dependency_analysis import DependencyAnalysisAgent
from app.modules.dependency_intelligence.schemas import DependencyListResponse, DependencyRelationship
from app.modules.rag.schemas import Citation, GeneratedAnswer
from app.modules.rag.schemas import AskRequest
from app.modules.rag.llm_provider import RagProviderTimeout
from app.prompts.loader import PromptLoader
from app.prompts.registry import PromptVersionNotFound


def relationship(repository_id, *, name="AuthenticationService", status="RESOLVED"):
    return DependencyRelationship(
        relationship_id="a" * 64, repository_id=repository_id,
        source_symbol="demo.Controller", target_symbol=f"demo.{name}",
        relationship_type="INJECTS", file_path="src/Controller.java",
        start_line=5, end_line=5, resolution_status=status,
    )


def provider_answer(*, evidence_id="E1", path="src/Controller.java", line=5, answer="Observed: The controller has a static injection edge [E1]."):
    return GeneratedAnswer(
        answer=answer,
        citations=[Citation(evidence_id=evidence_id, file_path=path,
                            start_line=line, end_line=line)],
        confidence="MEDIUM", limitations=["Static source cannot prove runtime wiring."],
    )


def test_all_specialist_prompts_are_released_and_selectable():
    loader = PromptLoader()
    for name in ("code_analysis", "dependency_analysis", "architecture_analysis"):
        active = loader.load(name)
        assert active.system_version == active.user_version == "1.1.0"
        assert "inline evidence" in active.system_template
        prompt = loader.load(name, system_version="1.0.0", user_version="1.0.0")
        system, user = prompt.render(
            repository_id="sample", question="What changed?", evidence_context="[]",
        )
        assert system and "What changed?" in user
        assert prompt.system_version == prompt.user_version == "1.0.0"
        with pytest.raises(PromptVersionNotFound):
            loader.load(name, system_version="9.9.9")


def test_code_specialist_uses_existing_rag_citation_path_with_new_prompt():
    from test_rag import service, source, generated

    repository_id = uuid4()
    rag, _, _, provider = service(
        repository_id, [source(repository_id)], generated(),
    )
    response = rag.answer(
        AskRequest(repository_id=repository_id, question="Explain processPayment."),
        prompt_name="code_analysis",
    )
    assert response.status == "ANSWERED"
    assert response.prompt_versions.name == "code_analysis"
    assert response.citations[0].evidence_id == "E1"
    assert "classes, methods" in provider.generate.call_args.args[0]


def test_dependency_agent_cites_persisted_static_edge_and_status():
    repository_id = uuid4()
    rows = DependencyListResponse(
        repository_id=repository_id, total_relationships=1,
        relationships=[relationship(repository_id)],
    )
    dependencies = SimpleNamespace(list_relationships=MagicMock(return_value=rows))
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate=MagicMock(return_value=provider_answer()),
    )
    agent = DependencyAnalysisAgent(
        dependencies, PromptedAnalyzer(PromptLoader(), provider),
    )
    result = agent.answer(repository_id, "What depends on AuthenticationService?")
    assert result.citations[0].file_path == "src/Controller.java"
    assert result.prompt_versions.name == "dependency_analysis"
    assert result.evidence_count == 1
    system, user = provider.generate.call_args.args
    assert "UNRESOLVED" in system
    assert "resolution_status=RESOLVED" in user
    assert "demo.AuthenticationService" in user


def test_unresolved_and_missing_dependency_evidence_are_not_promoted_to_fact():
    repository_id = uuid4()
    rows = DependencyListResponse(
        repository_id=repository_id, total_relationships=1,
        relationships=[relationship(repository_id, status="UNRESOLVED")],
    )
    dependencies = SimpleNamespace(list_relationships=MagicMock(return_value=rows))
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate=MagicMock(return_value=provider_answer(
            answer="This edge is unresolved; runtime wiring is not confirmed.",
        )),
    )
    agent = DependencyAnalysisAgent(
        dependencies, PromptedAnalyzer(PromptLoader(), provider),
    )
    agent.answer(repository_id, "What depends on AuthenticationService?")
    assert "resolution_status=UNRESOLVED" in provider.generate.call_args.args[1]
    provider.generate.reset_mock()
    empty = agent.answer(repository_id, "What depends on MissingService?")
    assert empty.confidence == "INSUFFICIENT"
    assert empty.citations == []
    provider.generate.assert_not_called()


def test_unresolved_edge_cannot_be_reported_as_confirmed():
    repository_id = uuid4()
    dependencies = SimpleNamespace(list_relationships=MagicMock(return_value=DependencyListResponse(
        repository_id=repository_id, total_relationships=1,
        relationships=[relationship(repository_id, status="UNRESOLVED")],
    )))
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate=MagicMock(return_value=provider_answer(
            answer="AuthenticationService is definitely injected at runtime.",
        )),
    )
    agent = DependencyAnalysisAgent(dependencies, PromptedAnalyzer(PromptLoader(), provider))
    with pytest.raises(ApplicationError) as error:
        agent.answer(repository_id, "What depends on AuthenticationService?")
    assert error.value.code == "UNSUPPORTED_DEPENDENCY_CLAIM"


def test_invalid_citations_and_cross_repository_evidence_are_rejected():
    repository_id = uuid4()
    evidence = [AgentEvidence(
        key="edge:1", repository_id=repository_id, file_path="src/A.java",
        start_line=2, end_line=4, kind="static_dependency", content="A CALLS B; RESOLVED",
    )]
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate=MagicMock(return_value=provider_answer(
            path="src/Other.java", line=2,
        )),
    )
    analyzer = PromptedAnalyzer(PromptLoader(), provider)
    generated = analyzer.answer("dependency_analysis", repository_id, "Who calls B?", evidence)
    report = EvidenceVerifier().verify(repository_id, generated)
    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert report.unsupported_claims[0].reason == "Claim cites missing, invalid, or unreturned evidence."
    with pytest.raises(ApplicationError) as error:
        prepare_evidence(uuid4(), evidence)
    assert error.value.code == "INVALID_EVIDENCE"


def test_specialist_discards_citations_when_model_reports_insufficient_evidence():
    repository_id = uuid4()
    evidence = [AgentEvidence(
        key="edge:1", repository_id=repository_id, file_path="src/A.java",
        start_line=2, end_line=4, kind="static_dependency", content="A CALLS B; RESOLVED",
    )]
    answer = GeneratedAnswer(
        answer="Unsupported claim [E99].",
        citations=[Citation(evidence_id="E99", file_path="src/Other.java", start_line=1, end_line=1)],
        confidence="INSUFFICIENT", limitations=[],
    )
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash", generate=MagicMock(return_value=answer),
    )
    result = PromptedAnalyzer(PromptLoader(), provider).answer(
        "dependency_analysis", repository_id, "OAuth rotation?", evidence,
    )
    assert result.confidence == "INSUFFICIENT"
    assert result.citations == []
    assert "Unsupported claim" not in result.answer
    assert any("discarded" in item for item in result.limitations)


def test_architecture_collects_sources_in_parallel_then_merges_deterministically():
    repository_id = uuid4()
    barrier = Barrier(2, timeout=2)
    code_evidence = AgentEvidence(
        key="code:1", repository_id=repository_id,
        file_path="src/Queue.java", start_line=2, end_line=8,
        kind="source_code", content="class Queue { RabbitMQ broker; }",
    )
    dep_evidence = AgentEvidence(
        key="edge:1", repository_id=repository_id,
        file_path="src/Queue.java", start_line=3, end_line=3,
        kind="static_dependency", content="demo.Queue IMPORTS RabbitMQ; UNRESOLVED",
    )

    def code_collect(*_args):
        barrier.wait()
        return [code_evidence]

    def dependency_collect(*_args):
        barrier.wait()
        return [dep_evidence, dep_evidence], []

    code = SimpleNamespace(collect=MagicMock(side_effect=code_collect))
    dependency = SimpleNamespace(collect=MagicMock(side_effect=dependency_collect))
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate=MagicMock(return_value=provider_answer(
            evidence_id="E1", path="src/Queue.java", line=3,
            answer="Observed: Queue references RabbitMQ. Proposed: assess Kafka migration; no change was performed.",
        )),
    )
    agent = ArchitectureAnalysisAgent(
        code, dependency, PromptedAnalyzer(PromptLoader(), provider),
    )
    result = agent.answer(repository_id, "What changes are needed to replace RabbitMQ with Kafka?", 5)
    assert result.prompt_versions.name == "architecture_analysis"
    assert result.evidence_count == 2
    assert "Proposed:" in result.answer
    assert "no change was performed" in result.answer
    user = provider.generate.call_args.args[1]
    assert user.index("source_code") < user.index("static_dependency")


def test_context_limit_reports_omission_without_truncation():
    repository_id = uuid4()
    items = [
        AgentEvidence(str(index), repository_id, f"src/{index}.java", 1, 1,
                      "source_code", "x" * 100)
        for index in range(3)
    ]
    context, included, limitations = prepare_evidence(repository_id, items, max_chars=400)
    assert 0 < len(included) < len(items)
    assert limitations
    assert context.count("x" * 100) == len(included)


def test_architecture_rejects_claim_that_proposed_migration_was_performed():
    repository_id = uuid4()
    source = AgentEvidence(
        key="code:1", repository_id=repository_id,
        file_path="src/Queue.java", start_line=2, end_line=8,
        kind="source_code", content="class Queue { RabbitMQ broker; }",
    )
    code = SimpleNamespace(collect=lambda *_args: [source])
    dependency = SimpleNamespace(collect=lambda *_args: ([], []))
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate=MagicMock(return_value=provider_answer(
            path="src/Queue.java", line=3,
            answer="We migrated the repository from RabbitMQ to Kafka.",
        )),
    )
    agent = ArchitectureAnalysisAgent(
        code, dependency, PromptedAnalyzer(PromptLoader(), provider),
    )
    with pytest.raises(ApplicationError) as error:
        agent.answer(repository_id, "What changes are needed to replace RabbitMQ with Kafka?", 5)
    assert error.value.code == "UNSUPPORTED_CHANGE_CLAIM"


def test_architecture_collector_failure_does_not_return_partial_answer():
    repository_id = uuid4()
    code = SimpleNamespace(collect=MagicMock(side_effect=ApplicationError(
        503, "DATABASE_UNAVAILABLE", "Source evidence is unavailable.",
    )))
    dependency = SimpleNamespace(collect=MagicMock(return_value=([], [])))
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate=MagicMock(return_value=provider_answer()),
    )
    agent = ArchitectureAnalysisAgent(
        code, dependency, PromptedAnalyzer(PromptLoader(), provider),
    )
    with pytest.raises(ApplicationError) as error:
        agent.answer(repository_id, "What changes are needed to replace RabbitMQ with Kafka?", 5)
    assert error.value.code == "DATABASE_UNAVAILABLE"
    provider.generate.assert_not_called()


def test_specialist_provider_timeout_is_controlled():
    repository_id = uuid4()
    evidence = [AgentEvidence(
        key="edge:1", repository_id=repository_id,
        file_path="src/Controller.java", start_line=5, end_line=5,
        kind="static_dependency", content="demo.Controller INJECTS demo.Service; RESOLVED",
    )]
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate=MagicMock(side_effect=RagProviderTimeout()),
    )
    with pytest.raises(ApplicationError) as error:
        PromptedAnalyzer(PromptLoader(), provider).answer(
            "dependency_analysis", repository_id, "What depends on Service?", evidence,
        )
    assert error.value.status_code == 504
