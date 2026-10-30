"""Load immutable prompt releases and render required runtime variables."""

import hashlib
from dataclasses import dataclass
from importlib.resources import files
from string import Template

from app.prompts.registry import (
    PromptIntegrityError,
    PromptRegistry,
    PromptVariableError,
    PromptVersionNotFound,
)


def _placeholders(template: str) -> frozenset[str]:
    names: set[str] = set()
    for match in Template.pattern.finditer(template):
        if match.group("invalid") is not None:
            raise PromptVariableError("Prompt template contains invalid placeholder syntax.")
        name = match.group("named") or match.group("braced")
        if name:
            names.add(name)
    return frozenset(names)


@dataclass(frozen=True)
class LoadedPrompt:
    name: str
    system_version: str
    user_version: str
    system_template: str
    user_template: str
    system_variables: frozenset[str]
    user_variables: frozenset[str]

    def render(self, **variables: str) -> tuple[str, str]:
        required = self.system_variables | self.user_variables
        missing = required - variables.keys()
        if missing:
            raise PromptVariableError(
                "Missing prompt variables: " + ", ".join(sorted(missing))
            )
        if any(not isinstance(variables[key], str) for key in required):
            raise PromptVariableError("Prompt variables must be strings.")
        try:
            return (
                Template(self.system_template).substitute(variables),
                Template(self.user_template).substitute(variables),
            )
        except (KeyError, ValueError) as exc:
            raise PromptVariableError("Prompt rendering failed.") from exc


class PromptLoader:
    def __init__(self, registry: PromptRegistry | None = None) -> None:
        self._registry = registry or PromptRegistry()

    def load(
        self, name: str, *, system_version: str | None = None,
        user_version: str | None = None,
    ) -> LoadedPrompt:
        system = self._registry.release(name, "system", system_version)
        user = self._registry.release(name, "user", user_version)
        templates = []
        for release in (system, user):
            path = files("app.prompts").joinpath(
                "templates", name, f"v{release.version}", f"{release.kind}.md"
            )
            if not path.is_file():
                raise PromptVersionNotFound(
                    f"Prompt '{name}' has no {release.kind} template file for {release.version}."
                )
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != release.sha256:
                raise PromptIntegrityError("Released prompt content does not match registry digest.")
            text = content.decode("utf-8")
            if _placeholders(text) != release.variables:
                raise PromptVariableError("Prompt template variables differ from registry.")
            templates.append(text)
        return LoadedPrompt(
            name, system.version, user.version,
            templates[0], templates[1], system.variables, user.variables,
        )
