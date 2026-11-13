from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field

from app.actions.protocol import ActionDefinition, StepResult


ActionHandler = Callable[[Any, dict[str, Any]], Awaitable[StepResult]]


class ActionValidation(BaseModel):
    valid: bool
    message: str = ""
    errors: list[str] = Field(default_factory=list)


class ActionCatalog(BaseModel):
    version: str
    actions: list[ActionDefinition]


@dataclass(frozen=True)
class RegisteredAction:
    definition: ActionDefinition
    handler: ActionHandler
    action_id: str


class ActionRegistry:
    def __init__(self, version: str) -> None:
        self.version = version
        self._actions: dict[tuple[str, str], RegisteredAction] = {}
        self._aliases: dict[tuple[str, str], tuple[str, str]] = {}

    def register(
        self,
        definition: ActionDefinition,
        handler: ActionHandler,
        *,
        aliases: tuple[str, ...] = (),
    ) -> None:
        key = (definition.executor, definition.id)
        if key in self._actions:
            raise ValueError(
                f"Action already registered: {definition.executor}.{definition.id}"
            )
        if not callable(handler):
            raise TypeError("Action handler must be callable")
        self._actions[key] = RegisteredAction(
            definition=definition.model_copy(
                update={"catalog_version": self.version}
            ),
            handler=handler,
            action_id=definition.id,
        )
        for alias in aliases:
            alias_key = (definition.executor, str(alias).strip())
            if not alias_key[1]:
                continue
            self._aliases[alias_key] = key

    def resolve(self, executor: str, action: str) -> RegisteredAction:
        normalized = (str(executor).strip().lower(), str(action).strip())
        key = self._aliases.get(normalized, normalized)
        registered = self._actions.get(key)
        if registered is None:
            raise KeyError(f"Unknown action: {normalized[0]}.{normalized[1]}")
        return RegisteredAction(
            definition=registered.definition,
            handler=registered.handler,
            action_id=normalized[1],
        )

    def validate(
        self,
        executor: str,
        action: str,
        params: dict[str, Any],
    ) -> ActionValidation:
        try:
            registered = self.resolve(executor, action)
        except KeyError as exc:
            return ActionValidation(valid=False, message=str(exc))
        if not isinstance(params, dict):
            return ActionValidation(
                valid=False,
                message="params must be an object",
            )

        validator = Draft202012Validator(
            registered.definition.params_schema
        )
        errors = sorted(
            validator.iter_errors(params),
            key=lambda item: list(item.absolute_path),
        )
        blocking = [
            error
            for error in errors
            if not _is_template_error(error, params)
        ]
        if blocking:
            messages = [
                _format_error(error) for error in blocking
            ]
            return ActionValidation(
                valid=False,
                message="; ".join(messages),
                errors=messages,
            )
        return ActionValidation(valid=True)

    async def execute(
        self,
        executor: str,
        action: str,
        params: dict[str, Any],
        *,
        context: Any,
    ) -> StepResult:
        validation = self.validate(executor, action, params)
        if not validation.valid:
            raise ValueError(validation.message)
        registered = self.resolve(executor, action)
        outcome = registered.handler(context, dict(params))
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return outcome

    def catalog(
        self,
        *,
        include_legacy: bool = False,
        executor: str | None = None,
    ) -> ActionCatalog:
        normalized_executor = (
            str(executor).strip().lower() if executor is not None else None
        )
        actions: list[ActionDefinition] = []
        for registered in self._actions.values():
            definition = registered.definition
            if not include_legacy and definition.legacy_only:
                continue
            if (
                normalized_executor is not None
                and definition.executor != normalized_executor
            ):
                continue
            actions.append(definition)
        actions.sort(key=lambda item: (item.executor, item.id))
        return ActionCatalog(version=self.version, actions=actions)


def _is_template_error(error: Any, params: dict[str, Any]) -> bool:
    if not error.absolute_path:
        return False
    value: Any = params
    for part in error.absolute_path:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and isinstance(part, int):
            if part >= len(value):
                return False
            value = value[part]
        else:
            return False
    return isinstance(value, str) and value.strip().startswith("${") and (
        value.strip().endswith("}")
    )


def _format_error(error: Any) -> str:
    path = ".".join(str(item) for item in error.absolute_path)
    prefix = f"{path}: " if path else ""
    return f"{prefix}{error.message}"


_DEFAULT_REGISTRY: ActionRegistry | None = None


def default_action_registry() -> ActionRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        from app.actions.catalog import build_default_registry

        _DEFAULT_REGISTRY = build_default_registry()
    return _DEFAULT_REGISTRY


__all__ = [
    "ActionCatalog",
    "ActionHandler",
    "ActionRegistry",
    "ActionValidation",
    "RegisteredAction",
    "default_action_registry",
]
