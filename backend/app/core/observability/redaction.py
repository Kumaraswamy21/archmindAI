"""Allowlist metadata and omit source, prompts, questions, and secrets."""

import hashlib
import re
from typing import Any


_ALLOWED = {
    "request_id",
    "repository_id",
    "workflow_type",
    "agent_name",
    "retrieval_strategy",
    "embedding_model",
    "generation_model",
    "system_prompt_version",
    "user_prompt_template_version",
    "prompt_name",
    "latency_ms",
    "error_code",
    "error_category",
    "intent",
    "verification_status",
    "classifier",
    "question_length",
    "question_sha256",
    "citation_count",
    "confidence",
    "evidence_count",
    "result_count",
    "unsupported_claim_count",
    "selected_workflow",
    "correction_pass",
    "dataset_id",
    "dataset_version",
    "repository_snapshot_id",
    "evaluation_question_id",
    "reranker_version",
    "evaluation_run_id",
}
_SECRET_KEY = re.compile(
    r"(^|_)(api_key|secret|password|token|authorization|credential)s?(_|$)",
    re.I,
)


def question_fingerprint(question: str | None) -> dict[str, Any]:
    if question is None:
        return {}
    encoded = question.encode("utf-8")
    return {
        "question_length": len(question),
        "question_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def filter_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if value is None or key not in _ALLOWED:
            continue
        if _SECRET_KEY.search(key):
            continue
        if isinstance(value, (str, int, float, bool)):
            text = str(value)
            if len(text) > 500:
                continue
            safe[key] = value
    return safe
