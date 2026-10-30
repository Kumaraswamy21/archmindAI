"""Released prompt loading, independent versions, integrity, and rendering."""

import hashlib

import pytest

from app.prompts.loader import PromptLoader
from app.prompts.registry import (
    PromptIntegrityError, PromptNotFound, PromptRegistry,
    PromptVariableError, PromptVersionNotFound,
)


def test_active_prompt_loads_and_renders_runtime_values():
    loaded = PromptLoader().load("rag_answer")
    assert (loaded.system_version, loaded.user_version) == ("1.0.0", "1.0.0")
    system, user = loaded.render(
        repository_id="repo-123", question="Where is OrderService?",
        evidence_context='{"content":"private source"}',
    )
    assert "untrusted" in system
    assert "Where is OrderService?" in user
    assert '"private source"' in user
    assert "private source" not in loaded.user_template


def test_missing_prompt_version_variable_and_invalid_selection():
    loader = PromptLoader()
    with pytest.raises(PromptNotFound):
        loader.load("unknown")
    with pytest.raises(PromptVersionNotFound):
        loader.load("rag_answer", system_version="9.9.9")
    with pytest.raises(PromptVersionNotFound):
        loader.load("rag_answer", user_version="9.9.9")
    with pytest.raises(PromptVersionNotFound):
        loader.load("rag_answer", user_version="../../secrets")
    with pytest.raises(PromptVariableError, match="evidence_context"):
        loader.load("rag_answer").render(repository_id="r", question="q")


def test_released_hash_catches_template_edit():
    registry = PromptRegistry()
    active = registry.release("rag_answer", "system")
    altered = {"prompts": {"rag_answer": {
        "active": {"system": "1.0.0", "user": "1.0.0"},
        "system": {"1.0.0": {"sha256": "0" * 64, "variables": []}},
        "user": {"1.0.0": {
            "sha256": registry.release("rag_answer", "user").sha256,
            "variables": ["repository_id", "question", "evidence_context"],
        }},
    }}}
    assert active.sha256 != "0" * 64
    with pytest.raises(PromptIntegrityError):
        PromptLoader(PromptRegistry(altered)).load("rag_answer")


def test_independent_version_selection_and_rollback(monkeypatch, tmp_path):
    root = tmp_path / "prompts"
    current = root / "templates" / "rag_answer" / "v1.0.0"
    previous = root / "templates" / "rag_answer" / "v0.9.0"
    current.mkdir(parents=True)
    previous.mkdir(parents=True)
    (current / "system.md").write_text("Current system")
    (current / "user.md").write_text("Current ${question}")
    (previous / "system.md").write_text("Previous system")
    (previous / "user.md").write_text("Previous ${question}")
    def release(path, variables):
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "variables": variables,
        }
    data = {"prompts": {"rag_answer": {
        "active": {"system": "1.0.0", "user": "1.0.0"},
        "system": {
            "1.0.0": release(current / "system.md", []),
            "0.9.0": release(previous / "system.md", []),
        },
        "user": {
            "1.0.0": release(current / "user.md", ["question"]),
            "0.9.0": release(previous / "user.md", ["question"]),
        },
    }}}
    monkeypatch.setattr("app.prompts.loader.files", lambda _package: root)
    loaded = PromptLoader(PromptRegistry(data)).load(
        "rag_answer", system_version="0.9.0", user_version="1.0.0"
    )
    assert loaded.render(question="Q") == ("Previous system", "Current Q")
    rollback = PromptLoader(PromptRegistry(data)).load(
        "rag_answer", system_version="0.9.0", user_version="0.9.0"
    )
    assert rollback.render(question="Q") == ("Previous system", "Previous Q")
