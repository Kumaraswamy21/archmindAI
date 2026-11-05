"""Pure macro retrieval metrics over actual ranked source symbols."""

from dataclasses import dataclass

from app.modules.evaluation.schemas import (
    AgentDatasetQuestion,
    AgentEvaluationMetrics,
    AgentQuestionResult,
)


@dataclass(frozen=True)
class MetricValues:
    recall_at_5: float
    mrr: float
    average_latency_ms: float


def calculate_metrics(
    expected: list[set[str]], ranked: list[list[str]], latencies_ms: list[float]
) -> MetricValues:
    count = len(expected)
    if count == 0 or len(ranked) != count or len(latencies_ms) != count:
        raise ValueError(
            "Metric inputs must contain the same nonzero number of queries."
        )
    if any(not relevant for relevant in expected) or any(
        value < 0 for value in latencies_ms
    ):
        raise ValueError("Relevant symbols and nonnegative latencies are required.")
    recalls = []
    reciprocal_ranks = []
    for relevant, results in zip(expected, ranked, strict=True):
        recalls.append(len(relevant & set(results[:5])) / len(relevant))
        first = next(
            (
                position
                for position, symbol in enumerate(results, start=1)
                if symbol in relevant
            ),
            None,
        )
        reciprocal_ranks.append(1 / first if first is not None else 0.0)
    return MetricValues(
        recall_at_5=sum(recalls) / count,
        mrr=sum(reciprocal_ranks) / count,
        average_latency_ms=sum(latencies_ms) / count,
    )


def calculate_agent_metrics(
    questions: list[AgentDatasetQuestion],
    results: list[AgentQuestionResult],
) -> AgentEvaluationMetrics:
    count = len(questions)
    if (
        count == 0
        or len(results) != count
        or any(
            question.id != result.question_id
            for question, result in zip(questions, results, strict=True)
        )
    ):
        raise ValueError(
            "Agent metric inputs must have matching nonempty question IDs."
        )
    completed = sum(result.status == "COMPLETED" for result in results)
    cited = sum(result.citation_count for result in results)
    valid = sum(result.valid_citation_count for result in results)
    supported = sum(result.supported_claim_count for result in results)
    unsupported = sum(result.unsupported_claim_count for result in results)
    assertions = supported + unsupported
    abstention_cases = [
        result
        for question, result in zip(questions, results, strict=True)
        if question.expect_abstention
    ]
    correct_abstentions = sum(
        result.status == "COMPLETED"
        and result.answer_status == "INSUFFICIENT_EVIDENCE"
        and result.citation_count == 0
        for result in abstention_cases
    )
    return AgentEvaluationMetrics(
        intent_accuracy=sum(
            question.expected_intent == result.classified_intent
            for question, result in zip(questions, results, strict=True)
        )
        / count,
        routing_accuracy=sum(
            question.expected_workflow == result.selected_workflow
            for question, result in zip(questions, results, strict=True)
        )
        / count,
        workflow_completion_rate=completed / count,
        workflow_failure_rate=(count - completed) / count,
        average_end_to_end_latency_ms=sum(result.latency_ms for result in results)
        / count,
        citation_validity_rate=valid / cited if cited else None,
        claim_support_rate=supported / assertions if assertions else None,
        unsupported_claim_rate=unsupported / assertions if assertions else None,
        correct_abstention_rate=correct_abstentions / len(abstention_cases)
        if abstention_cases
        else None,
    )
