"""Audit every released runtime prompt, including inactive versions."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from app.prompts.loader import _placeholders
from app.prompts.registry import PROMPT_NAME, SEMVER, PromptError

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {"system", "user"}


def validate_prompt_tree(root: Path) -> int:
    """Raise PromptError on invalid structure, metadata, or released bytes."""
    registry_path = root / "registry.toml"
    try:
        data = tomllib.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise PromptError("Prompt registry is missing or invalid TOML.") from exc
    if (
        set(data) != {"prompts"}
        or not isinstance(data["prompts"], dict)
        or not data["prompts"]
    ):
        raise PromptError("Prompt registry must contain a nonempty prompts table.")

    templates_root = root / "templates"
    expected_files: set[Path] = set()
    releases = 0
    for name, record in data["prompts"].items():
        if (
            not isinstance(name, str)
            or not PROMPT_NAME.fullmatch(name)
            or not isinstance(record, dict)
        ):
            raise PromptError("Prompt name or record is invalid.")
        if set(record) != {"active", "system", "user"}:
            raise PromptError(
                f"Prompt {name} must define active, system, and user tables."
            )
        active = record["active"]
        if not isinstance(active, dict) or set(active) != _KINDS:
            raise PromptError(
                f"Prompt {name} must select active system and user versions."
            )
        for kind in sorted(_KINDS):
            versions = record[kind]
            if not isinstance(versions, dict) or not versions:
                raise PromptError(f"Prompt {name}/{kind} has no releases.")
            if not isinstance(active[kind], str) or active[kind] not in versions:
                raise PromptError(
                    f"Prompt {name}/{kind} has an invalid active version."
                )
            for version, release in versions.items():
                if not isinstance(version, str) or not SEMVER.fullmatch(version):
                    raise PromptError(
                        f"Prompt {name}/{kind} has an invalid semantic version."
                    )
                if not isinstance(release, dict) or set(release) != {
                    "sha256",
                    "variables",
                }:
                    raise PromptError(
                        f"Prompt {name}/{kind}/{version} has invalid metadata."
                    )
                digest, variables = release["sha256"], release["variables"]
                if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                    raise PromptError(
                        f"Prompt {name}/{kind}/{version} has an invalid digest."
                    )
                if (
                    not isinstance(variables, list)
                    or len(variables) != len(set(map(str, variables)))
                    or any(
                        not isinstance(item, str) or not item.isidentifier()
                        for item in variables
                    )
                ):
                    raise PromptError(
                        f"Prompt {name}/{kind}/{version} has invalid variables."
                    )
                path = templates_root / name / f"v{version}" / f"{kind}.md"
                expected_files.add(path)
                if not path.is_file() or path.is_symlink():
                    raise PromptError(
                        f"Prompt {name}/{kind}/{version} template is missing."
                    )
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != digest:
                    raise PromptError(
                        f"Prompt {name}/{kind}/{version} digest differs from released bytes."
                    )
                try:
                    placeholders = _placeholders(content.decode("utf-8"))
                except UnicodeError as exc:
                    raise PromptError(
                        f"Prompt {name}/{kind}/{version} is not UTF-8."
                    ) from exc
                if placeholders != set(variables):
                    raise PromptError(
                        f"Prompt {name}/{kind}/{version} variables differ from template."
                    )
                releases += 1

    actual_files = {
        path
        for path in templates_root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_files != expected_files:
        raise PromptError("Prompt templates contain unregistered or unexpected files.")
    for path in templates_root.rglob("*"):
        if path.is_dir() and not path.is_symlink() and path != templates_root:
            relative = path.relative_to(templates_root).parts
            if len(relative) == 1 and relative[0] in data["prompts"]:
                continue
            if (
                len(relative) == 2
                and relative[0] in data["prompts"]
                and relative[1].startswith("v")
                and SEMVER.fullmatch(relative[1][1:])
            ):
                continue
            raise PromptError("Prompt templates contain an invalid directory.")
    return releases


def main() -> None:
    root = Path(__file__).resolve().parent
    count = validate_prompt_tree(root)
    print(f"Validated {count} released prompt templates.")


if __name__ == "__main__":
    main()
