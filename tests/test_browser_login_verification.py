from __future__ import annotations

import asyncio

from app.engine.browser_login import BrowserLoginVerifier
from app.models.human_task import HumanTask


class FakeLocator:
    def __init__(self, *, count: int = 0, fail: bool = False) -> None:
        self._count = count
        self.fail = fail
        self.waited = False

    async def count(self) -> int:
        return self._count

    async def wait_for(self, **kwargs) -> None:
        del kwargs
        self.waited = True
        if self.fail:
            raise RuntimeError("selector not found")


class FakePage:
    def __init__(
        self,
        *,
        url: str,
        password_count: int = 0,
        selector_visible: bool = False,
    ) -> None:
        self.url = url
        self.password_count = password_count
        self.selector_visible = selector_visible
        self.selector_waited = False

    def locator(self, selector: str) -> FakeLocator:
        if "password" in selector:
            return FakeLocator(count=self.password_count)
        if selector == "[data-user]":
            return FakeLocator(
                count=1 if self.selector_visible else 0,
                fail=not self.selector_visible,
            )
        return FakeLocator()


class FakeSessions:
    def __init__(self, page: FakePage | None) -> None:
        self.page = page

    def get_page(self, task_id: int):
        del task_id
        return self.page


def _task(payload: dict) -> HumanTask:
    return HumanTask(
        id=1,
        execution_id=1,
        step_id=2,
        kind="login_required",
        instruction="login",
        payload=payload,
        status="pending",
    )


def test_web_login_verifier_accepts_only_declared_success_state() -> None:
    async def scenario():
        task = _task(
            {
                "verification": {
                    "type": "web_logged_in",
                    "success_selector": "[data-user]",
                    "url_contains": "/home",
                }
            }
        )
        verifier = BrowserLoginVerifier(
            FakeSessions(
                FakePage(
                    url="https://example.com/home",
                    selector_visible=True,
                )
            )
        )
        return await verifier.verify(task, {})

    result = asyncio.run(scenario())

    assert result.ok is True
    assert result.login_state == "ready"
    assert result.details["type"] == "web_logged_in"


def test_web_login_verifier_rejects_missing_selector() -> None:
    async def scenario():
        task = _task(
            {
                "verification": {
                    "type": "web_logged_in",
                    "success_selector": "[data-user]",
                }
            }
        )
        verifier = BrowserLoginVerifier(
            FakeSessions(
                FakePage(
                    url="https://example.com/home",
                    selector_visible=False,
                )
            )
        )
        return await verifier.verify(task, {})

    result = asyncio.run(scenario())

    assert result.ok is False
    assert result.login_state == "login_required"
    assert "登录成功标志" in result.reason


def test_web_login_verifier_rejects_open_login_form() -> None:
    async def scenario():
        task = _task(
            {
                "verification": {
                    "type": "web_logged_out",
                    "login_paths": ["/login"],
                }
            }
        )
        verifier = BrowserLoginVerifier(
            FakeSessions(
                FakePage(
                    url="https://example.com/login",
                    password_count=1,
                )
            )
        )
        return await verifier.verify(task, {})

    result = asyncio.run(scenario())

    assert result.ok is False
    assert result.login_state == "login_required"
    assert "登录表单" in result.reason


def test_missing_browser_session_keeps_task_pending() -> None:
    async def scenario():
        task = _task(
            {
                "verification": {
                    "type": "web_logged_in",
                    "success_selector": "[data-user]",
                }
            }
        )
        verifier = BrowserLoginVerifier(FakeSessions(None))
        return await verifier.verify(task, {})

    result = asyncio.run(scenario())

    assert result.ok is False
    assert result.login_state == "session_unavailable"
    assert "登录窗口" in result.reason
