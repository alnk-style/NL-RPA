from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.execution_policy import (
    default_execution_policy,
    validate_execution_policy,
)


def test_default_execution_policy_matches_design() -> None:
    policy = default_execution_policy()
    assert policy.model_dump(mode="json") == {
        "mode": "interactive_preferred",
        "login_policy": "pause_and_resume",
        "visual_fallback": "on_locator_failure",
        "host_policy": "local_then_isolated",
        "failure_policy": "stop",
        "required_delivery": True,
        "max_duration_seconds": 1800,
        "human_handoff": True,
    }


@pytest.mark.parametrize("mode", ["background_if_safe", "isolated_preferred"])
def test_valid_execution_modes_are_accepted(mode: str) -> None:
    policy = validate_execution_policy({"mode": mode})
    assert policy.mode == mode


def test_unknown_execution_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_execution_policy({"mode": "automatic_everywhere"})


def test_max_duration_has_a_hard_upper_bound() -> None:
    with pytest.raises(ValidationError):
        validate_execution_policy({"max_duration_seconds": 86401})
