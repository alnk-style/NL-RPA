from __future__ import annotations

import asyncio

from app.engine.human_verification import CompositeHumanVerifier
from app.models.human_task import HumanTask


class FakeDispatcher:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = 0

    async def inspect(self, request=None, *, timeout=None):
        del request, timeout
        self.calls += 1
        return self.result


def _task(verification: dict, **extra) -> HumanTask:
    return HumanTask(
        id=1,
        execution_id=1,
        step_id=2,
        kind="manual_confirmation",
        instruction="confirm",
        payload={
            "verification": verification,
            **extra,
        },
        status="pending",
    )


def test_desktop_window_verification_checks_title_and_process() -> None:
    dispatcher = FakeDispatcher(
        {
            "success": True,
            "windows": [
                {
                    "title": "个人自动化登录",
                    "process_name": "CustomApp.exe",
                }
            ],
            "error": None,
        }
    )
    verifier = CompositeHumanVerifier(desktop_dispatcher=dispatcher)
    task = _task(
        {
            "type": "desktop_window",
            "window_title": "个人自动化登录",
            "process_name": "CustomApp.exe",
        }
    )

    result = asyncio.run(verifier.verify(task, {}))

    assert result.ok is True
    assert result.details["window_title"] == "个人自动化登录"
    assert dispatcher.calls == 1


def test_desktop_window_verification_fails_when_window_missing() -> None:
    verifier = CompositeHumanVerifier(
        desktop_dispatcher=FakeDispatcher(
            {
                "success": True,
                "windows": [{"title": "其他窗口"}],
                "error": None,
            }
        )
    )
    task = _task(
        {
            "type": "desktop_window",
            "window_title": "目标窗口",
        }
    )

    result = asyncio.run(verifier.verify(task, {}))

    assert result.ok is False
    assert "目标窗口" in result.reason


def test_manual_confirmation_requires_follow_up_verification() -> None:
    verifier = CompositeHumanVerifier()
    task = _task({"type": "manual_confirmation"})

    result = asyncio.run(
        verifier.verify(task, {"manual_confirmed": True})
    )

    assert result.ok is False
    assert "业务验证" in result.reason


def test_manual_confirmation_with_follow_up_requires_explicit_evidence() -> None:
    verifier = CompositeHumanVerifier()
    task = _task(
        {"type": "manual_confirmation"},
        follow_up_verification={
            "type": "next_step_postcondition",
            "required": True,
        },
    )

    missing = asyncio.run(verifier.verify(task, {}))
    confirmed = asyncio.run(
        verifier.verify(task, {"manual_confirmed": True})
    )

    assert missing.ok is False
    assert "人工确认" in missing.reason
    assert confirmed.ok is True
    assert confirmed.details["follow_up_verification"] == {
        "type": "next_step_postcondition",
        "required": True,
    }
