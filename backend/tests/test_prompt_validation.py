"""The CI audit checks inactive releases and unregistered prompt files."""

import shutil
from pathlib import Path

import pytest

from app.prompts.registry import PromptError
from app.prompts.validate import validate_prompt_tree


PROMPTS = Path(__file__).resolve().parents[1] / "app" / "prompts"


def test_all_releases_are_valid():
    assert validate_prompt_tree(PROMPTS) == 16


@pytest.mark.parametrize("change", ["inactive_hash", "orphan", "active", "duplicate"])
def test_audit_rejects_registry_and_tree_drift(tmp_path, change):
    root = tmp_path / "prompts"
    shutil.copytree(PROMPTS, root)
    registry = root / "registry.toml"
    if change == "inactive_hash":
        (root / "templates" / "code_analysis" / "v1.0.0" / "system.md").write_text(
            "changed"
        )
    elif change == "orphan":
        (root / "templates" / "code_analysis" / "v2.0.0").mkdir()
        (root / "templates" / "code_analysis" / "v2.0.0" / "user.md").write_text(
            "orphan"
        )
    elif change == "active":
        registry.write_text(
            registry.read_text().replace('system = "1.1.0"', 'system = "9.9.9"', 1)
        )
    else:
        registry.write_text(registry.read_text() + "\n[prompts.rag_answer.active]\n")
    with pytest.raises(PromptError):
        validate_prompt_tree(root)
