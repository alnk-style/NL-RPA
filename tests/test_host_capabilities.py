from __future__ import annotations

import pytest

from app.host_capabilities import (
    normalize_capabilities,
    normalize_environment,
)
from desktop_agent.capabilities import (
    normalize_capabilities as normalize_desktop_capabilities,
    normalize_environment as normalize_desktop_environment,
)
from app.actions.validation import validate_workflow_draft


@pytest.mark.parametrize("value", ["local", "vm"])
def test_canonical_environments_are_accepted(value: str) -> None:
    assert normalize_environment(value) == value
    assert normalize_desktop_environment(value) == value


def test_legacy_isolated_environment_normalizes_to_vm() -> None:
    assert normalize_environment(" isolated ") == "vm"
    assert normalize_desktop_environment("ISOLATED") == "vm"


@pytest.mark.parametrize("value", ["", "cloud", "remote"])
def test_unknown_environment_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_environment(value)
    with pytest.raises(ValueError):
        normalize_desktop_environment(value)


def test_legacy_capabilities_map_to_canonical_capabilities() -> None:
    assert normalize_capabilities(
        ["window", "uia", "screenshot", "wps"]
    ) == [
        "interactive_desktop",
        "legacy_wps",
    ]
    assert normalize_desktop_capabilities(
        ["window", "unknown-capability"]
    ) == ["interactive_desktop"]


def test_empty_capabilities_remain_minimal() -> None:
    assert normalize_capabilities(None) == []
    assert normalize_capabilities([]) == []
    assert normalize_desktop_capabilities(None) == []


def test_vm_environment_does_not_imply_isolated_desktop() -> None:
    assert normalize_environment("vm") == "vm"
    assert "isolated_desktop" not in normalize_capabilities([])


def test_unknown_capabilities_are_ignored_without_failing() -> None:
    assert normalize_capabilities(
        ["isolated_desktop", "future_capability"]
    ) == ["isolated_desktop"]


def test_workflow_validation_accepts_canonical_host_requirement() -> None:
    result = validate_workflow_draft(
        {
            "execution_mode": "structured",
            "steps": [
                {
                    "step_id": 1,
                    "executor": "windows",
                    "action": "launch_app",
                    "host_requirement": "isolated_desktop",
                    "params": {"path": "notepad.exe"},
                }
            ],
        },
        executor_type="windows",
        include_legacy=True,
        validate_params=False,
    )
    assert result.valid is True


def test_workflow_validation_rejects_unknown_host_requirement() -> None:
    result = validate_workflow_draft(
        {
            "execution_mode": "structured",
            "steps": [
                {
                    "step_id": 1,
                    "executor": "windows",
                    "action": "launch_app",
                    "host_requirement": "remote_everywhere",
                    "params": {"path": "notepad.exe"},
                }
            ],
        },
        executor_type="windows",
        include_legacy=True,
        validate_params=False,
    )
    assert result.valid is False
    assert any(
        "host_requirement" in error
        for error in result.errors
    )


def test_web_step_cannot_declare_desktop_or_isolated_host() -> None:
    result = validate_workflow_draft(
        {
            "execution_mode": "structured",
            "steps": [
                {
                    "step_id": 1,
                    "executor": "web",
                    "action": "goto",
                    "host_requirement": "isolated_desktop",
                    "params": {"url": "https://example.com"},
                }
            ],
        },
        executor_type="web",
        validate_params=True,
    )
    assert result.valid is False
    assert any(
        "host_requirement" in error
        for error in result.errors
    )
