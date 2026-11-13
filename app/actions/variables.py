from __future__ import annotations

import re
from typing import Any


PATH_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
PLACEHOLDER_PATTERN = re.compile(r"\$\{([^}]+)\}")


class VariableScope:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, path: str, default: Any = None) -> Any:
        current: Any = self.values
        for part in str(path).split("."):
            if not isinstance(current, dict) or part not in current:
                if default is not None:
                    return default
                raise ValueError(f"Unknown variable: {path}")
            current = current[part]
        return current

    def set(self, path: str, value: Any) -> None:
        parts = str(path).split(".")
        if any(not PATH_PATTERN.fullmatch(part) for part in parts):
            raise ValueError(f"Invalid variable path: {path}")
        current = self.values
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                child = {}
                current[part] = child
            current = child
        current[parts[-1]] = value

    def resolve(self, value: Any) -> Any:
        return resolve_template(value, self.values)


def resolve_template(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        match = re.fullmatch(r"\$\{([^}]+)\}", stripped)
        if match:
            path = match.group(1).strip()
            if not PATH_PATTERN.fullmatch(path):
                raise ValueError(f"Unknown variable: {path}")
            return VariableScope(variables).get(path)
        return PLACEHOLDER_PATTERN.sub(
            lambda item: str(
                VariableScope(variables).get(item.group(1).strip())
            ),
            value,
        )
    if isinstance(value, list):
        return [resolve_template(item, variables) for item in value]
    if isinstance(value, tuple):
        return tuple(resolve_template(item, variables) for item in value)
    if isinstance(value, dict):
        return {
            key: resolve_template(item, variables)
            for key, item in value.items()
        }
    return value


__all__ = [
    "PATH_PATTERN",
    "PLACEHOLDER_PATTERN",
    "VariableScope",
    "resolve_template",
]
