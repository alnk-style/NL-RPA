from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.actions.protocol import (
    ActionDefinition,
    StepResult,
    StepStatus,
)
from app.actions.registry import ActionRegistry


async def _echo_handler(context: object, params: dict) -> StepResult:
    return StepResult(status=StepStatus.SUCCESS, value=params)


def _definition(
    *,
    action_id: str = "echo",
    executor: str = "transform",
    legacy_only: bool = False,
) -> ActionDefinition:
    return ActionDefinition(
        id=action_id,
        executor=executor,
        title="Echo",
        description="Return params",
        category="test",
        risk_level="read_only",
        params_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
        output_schema={"type": "object"},
        requires={
            "interactive_desktop": False,
            "visual": False,
            "network": False,
        },
        catalog_version="test",
        legacy_only=legacy_only,
    )


def test_registry_validates_required_and_unknown_params() -> None:
    registry = ActionRegistry(version="test")
    registry.register(_definition(), _echo_handler)

    assert registry.validate("transform", "echo", {"text": "ok"}).valid
    missing = registry.validate("transform", "echo", {})
    assert not missing.valid
    assert "text" in missing.message
    unknown = registry.validate("transform", "echo", {"text": "ok", "extra": 1})
    assert not unknown.valid
    assert "extra" in unknown.message


def test_registry_accepts_variable_placeholders_during_static_validation() -> None:
    registry = ActionRegistry(version="test")
    registry.register(_definition(), _echo_handler)
    result = registry.validate(
        "transform",
        "echo",
        {"text": "${source.text}"},
    )
    assert result.valid


def test_registry_rejects_duplicate_handlers_and_catalog_entries() -> None:
    registry = ActionRegistry(version="test")
    registry.register(_definition(), _echo_handler)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_definition(), _echo_handler)


def test_registry_alias_resolves_and_executes_handler() -> None:
    registry = ActionRegistry(version="test")
    registry.register(
        _definition(action_id="goto", executor="web"),
        _echo_handler,
        aliases=("navigate",),
    )

    resolved = registry.resolve("web", "navigate")
    assert resolved.definition.id == "goto"
    assert resolved.action_id == "navigate"


def test_registry_catalog_can_hide_legacy_only_actions() -> None:
    registry = ActionRegistry(version="test")
    registry.register(_definition(action_id="public"), _echo_handler)
    registry.register(
        _definition(action_id="legacy", legacy_only=True),
        _echo_handler,
    )

    public_ids = {item.id for item in registry.catalog().actions}
    all_ids = {
        item.id
        for item in registry.catalog(include_legacy=True).actions
    }
    assert public_ids == {"public"}
    assert all_ids == {"public", "legacy"}


def test_registry_async_execute_validates_before_handler() -> None:
    registry = ActionRegistry(version="test")
    registry.register(_definition(), _echo_handler)

    async def scenario() -> StepResult:
        return await registry.execute(
            "transform",
            "echo",
            {"text": "hello"},
            context=object(),
        )

    result = asyncio.run(scenario())
    assert result.status is StepStatus.SUCCESS
    assert result.value == {"text": "hello"}
