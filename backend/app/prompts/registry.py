"""Read and validate the central released-prompt registry."""

import re
from dataclasses import dataclass
from importlib.resources import files

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


PROMPT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class PromptError(ValueError):
    """A public-safe prompt configuration or rendering error."""


class PromptNotFound(PromptError):
    pass


class PromptVersionNotFound(PromptError):
    pass


class PromptIntegrityError(PromptError):
    pass


class PromptVariableError(PromptError):
    pass


@dataclass(frozen=True)
class PromptRelease:
    name: str
    kind: str
    version: str
    sha256: str
    variables: frozenset[str]


class PromptRegistry:
    def __init__(self, data: dict | None = None) -> None:
        if data is None:
            data = tomllib.loads(files("app.prompts").joinpath("registry.toml").read_text())
        self._prompts = data.get("prompts", {})

    def release(self, name: str, kind: str, version: str | None = None) -> PromptRelease:
        if not PROMPT_NAME.fullmatch(name) or name not in self._prompts:
            raise PromptNotFound(f"Prompt '{name}' is not registered.")
        if kind not in {"system", "user"}:
            raise PromptError("Prompt kind must be system or user.")
        record = self._prompts[name]
        selected = version if version is not None else record.get("active", {}).get(kind)
        if not isinstance(selected, str) or not SEMVER.fullmatch(selected):
            raise PromptVersionNotFound(f"Invalid or missing {kind} prompt version.")
        release = record.get(kind, {}).get(selected)
        if not isinstance(release, dict):
            raise PromptVersionNotFound(
                f"Prompt '{name}' has no released {kind} version {selected}."
            )
        digest = release.get("sha256")
        variables = release.get("variables")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PromptIntegrityError("Prompt registry has an invalid digest.")
        if not isinstance(variables, list) or any(
            not isinstance(item, str) or not item.isidentifier() for item in variables
        ):
            raise PromptVariableError("Prompt registry has invalid variables.")
        return PromptRelease(name, kind, selected, digest, frozenset(variables))
