"""Pure macro retrieval metrics over actual ranked source symbols."""

from dataclasses import dataclass


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
        raise ValueError("Metric inputs must contain the same nonzero number of queries.")
    if any(not relevant for relevant in expected) or any(value < 0 for value in latencies_ms):
        raise ValueError("Relevant symbols and nonnegative latencies are required.")
    recalls = []
    reciprocal_ranks = []
    for relevant, results in zip(expected, ranked, strict=True):
        recalls.append(len(relevant & set(results[:5])) / len(relevant))
        first = next((position for position, symbol in enumerate(results, start=1) if symbol in relevant), None)
        reciprocal_ranks.append(1 / first if first is not None else 0.0)
    return MetricValues(
        recall_at_5=sum(recalls) / count,
        mrr=sum(reciprocal_ranks) / count,
        average_latency_ms=sum(latencies_ms) / count,
    )
