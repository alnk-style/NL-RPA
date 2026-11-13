from __future__ import annotations

from app.actions.protocol import StepStatus
from app.actions.runtime import apply_empty_policy


def test_empty_list_fails_by_default() -> None:
    result = apply_empty_policy([])
    assert result.status is StepStatus.FAILED
    assert result.error_category == "data_empty"


def test_empty_list_can_be_explicitly_allowed() -> None:
    result = apply_empty_policy([], allow_empty=True)
    assert result.status is StepStatus.SUCCESS
    assert result.error_category is None


def test_empty_list_can_be_an_explicit_warning() -> None:
    result = apply_empty_policy(
        [],
        allow_empty=True,
        empty_policy="warning",
    )
    assert result.status is StepStatus.WARNING
    assert result.error_category == "data_empty"
