from __future__ import annotations

from app.actions.registry import default_action_registry


def test_every_public_catalog_action_has_a_handler_and_closed_schema() -> None:
    registry = default_action_registry()
    for item in registry.catalog(include_legacy=False).actions:
        assert registry.resolve(item.executor, item.id).handler is not None
        assert item.params_schema["type"] == "object"
        assert item.params_schema["additionalProperties"] is False
        assert item.catalog_version


def test_public_catalog_contains_only_implemented_generic_actions() -> None:
    registry = default_action_registry()
    ids = {
        (item.executor, item.id)
        for item in registry.catalog(include_legacy=False).actions
    }
    assert ("web", "goto") in ids
    assert ("windows", "launch_app") in ids
    assert ("mcp", "call_tool") in ids
    assert ("web", "code_step") not in ids
    assert ("windows", "fetch_kugou_lyrics") not in ids


def test_default_registry_version_is_stable() -> None:
    assert default_action_registry().version == "2026-10-28.1"
