from __future__ import annotations

from app.actions.protocol import (
    StepStatus,
    coerce_legacy_step_result,
)


def test_step_status_has_the_five_stable_values() -> None:
    assert {status.value for status in StepStatus} == {
        "success",
        "warning",
        "failed",
        "cancelled",
        "awaiting_user",
    }


def test_legacy_step_result_preserves_payload_and_human_action() -> None:
    result = coerce_legacy_step_result(
        {
            "success": False,
            "message": "需要登录",
            "text": "",
            "artifacts": [{"path": "report.csv"}],
            "verification": {"passed": False},
            "human_action": {"kind": "login_required"},
            "error_category": "auth_required",
        }
    )

    assert result.status is StepStatus.AWAITING_USER
    assert result.error is not None
    assert result.error.category == "auth_required"
    assert result.artifacts[0]["path"] == "report.csv"
    assert result.verification == {"passed": False}
    assert result.human_action == {"kind": "login_required"}


def test_unknown_legacy_status_fails_closed() -> None:
    result = coerce_legacy_step_result(
        {"status": "definitely_unknown", "message": "bad state"}
    )
    assert result.status is StepStatus.FAILED
    assert result.error is not None
    assert result.error.category == "unknown"
