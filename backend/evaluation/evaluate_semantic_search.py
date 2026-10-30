"""Small labeled live retrieval check for a prepared Java sample repository.

Usage: python evaluation/evaluate_semantic_search.py REPOSITORY_UUID [API_BASE_URL]
The registered and indexed repository must contain the labeled symbols.
"""

import json
import sys
import time
from pathlib import Path
from uuid import UUID

import httpx


def evaluate(repository_id: UUID, base_url: str) -> dict[str, float | int]:
    cases = json.loads(Path(__file__).with_name("semantic_search_cases.json").read_text())
    reciprocal_ranks = []
    latencies = []
    with httpx.Client(base_url=base_url, timeout=30) as client:
        for case in cases:
            start = time.perf_counter()
            response = client.post("/api/v1/search/semantic", json={
                "repository_id": str(repository_id), "query": case["query"], "top_k": 5,
            })
            latencies.append((time.perf_counter() - start) * 1000)
            response.raise_for_status()
            names = [item["symbol_name"] for item in response.json()["results"]]
            rank = names.index(case["expected_symbol"]) + 1 if case["expected_symbol"] in names else None
            reciprocal_ranks.append(1 / rank if rank else 0.0)
    return {
        "questions": len(cases),
        "recall_at_5": sum(rank > 0 for rank in reciprocal_ranks) / len(cases),
        "mrr": sum(reciprocal_ranks) / len(cases),
        "average_query_latency_ms": sum(latencies) / len(latencies),
    }


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: python evaluation/evaluate_semantic_search.py REPOSITORY_UUID [API_BASE_URL]")
    identifier = UUID(sys.argv[1])
    url = sys.argv[2] if len(sys.argv) == 3 else "http://127.0.0.1:8000"
    print(json.dumps(evaluate(identifier, url), indent=2))
