from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import async_playwright

from app.engine.runner import PlaywrightRunner
from app.models.browser_profile import BrowserProfile
from app.models.human_task import HumanTask
from app.services.human_tasks import VerificationResult


class BrowserLoginError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.reason = message


@dataclass
class _BrowserLoginSession:
    profile_id: int
    playwright: Any
    browser: Any
    context: Any
    page: Any
    task_ids: set[int]


class BrowserLoginVerifier:
    def __init__(self, sessions: "BrowserLoginSessionManager") -> None:
        self.sessions = sessions

    async def verify(
        self,
        task: HumanTask,
        request_verification: dict[str, Any],
    ) -> VerificationResult:
        del request_verification
        page = self.sessions.get_page(int(task.id))
        if page is None:
            return VerificationResult(
                ok=False,
                reason=(
                    "登录窗口已关闭或会话已丢失，请重新打开登录窗口。"
                ),
                login_state="session_unavailable",
            )
        payload = task.payload if isinstance(task.payload, dict) else {}
        verification = payload.get("verification")
        if not isinstance(verification, dict) or not verification:
            return VerificationResult(
                ok=False,
                reason="人工任务缺少服务端验证条件。",
                login_state="unknown",
            )

        verification_type = str(
            verification.get("type") or ""
        ).strip()
        current_url = _safe_display_url(
            str(getattr(page, "url", "") or "")
        )
        try:
            if verification_type == "web_logged_in":
                selector = str(
                    verification.get("success_selector") or ""
                ).strip()
                if selector:
                    try:
                        await page.locator(selector).wait_for(
                            state="visible",
                            timeout=5000,
                        )
                    except Exception:
                        return VerificationResult(
                            ok=False,
                            reason=(
                                "仍未找到登录成功标志 "
                                f"{selector}。"
                            ),
                            login_state="login_required",
                            details={"current_url": current_url},
                        )
                expected_url = str(
                    verification.get("url_contains") or ""
                ).strip()
                if expected_url and expected_url not in current_url:
                    return VerificationResult(
                        ok=False,
                        reason=f"当前页面不包含预期地址 {expected_url}。",
                        login_state="login_required",
                        details={"current_url": current_url},
                    )
                return VerificationResult(
                    ok=True,
                    login_state="ready",
                    details={
                        "type": verification_type,
                        "current_url": current_url,
                    },
                )

            if verification_type == "web_logged_out":
                password_count = int(
                    await page.locator(
                        "input[type='password']:visible"
                    ).count()
                )
                login_paths = verification.get("login_paths")
                if not isinstance(login_paths, (list, tuple)):
                    login_paths = []
                in_login_path = any(
                    str(path).strip()
                    and str(path).strip() in current_url
                    for path in login_paths
                )
                if password_count > 0 or in_login_path:
                    return VerificationResult(
                        ok=False,
                        reason="页面仍显示登录表单，请完成登录后再继续。",
                        login_state="login_required",
                        details={"current_url": current_url},
                    )
                return VerificationResult(
                    ok=True,
                    login_state="ready",
                    details={
                        "type": verification_type,
                        "current_url": current_url,
                    },
                )
        except Exception as exc:
            return VerificationResult(
                ok=False,
                reason=f"服务端验证失败：{exc}",
                login_state="unknown",
            )
        return VerificationResult(
            ok=False,
            reason="人工任务验证类型不受支持。",
            login_state="unknown",
        )


class BrowserLoginSessionManager:
    def __init__(
        self,
        *,
        runner_factory: Any | None = None,
    ) -> None:
        self._runner_factory = runner_factory or PlaywrightRunner
        self._sessions: dict[int, _BrowserLoginSession] = {}
        self._task_to_profile: dict[int, int] = {}
        self._lock = asyncio.Lock()

    def get_page(self, task_id: int) -> Any | None:
        profile_id = self._task_to_profile.get(int(task_id))
        if profile_id is None:
            return None
        session = self._sessions.get(profile_id)
        return session.page if session is not None else None

    async def open_login(
        self,
        task: HumanTask,
        profile: BrowserProfile,
        *,
        url: str | None = None,
    ) -> dict[str, Any]:
        payload = task.payload if isinstance(task.payload, dict) else {}
        target_url = str(
            url or payload.get("current_url") or ""
        ).strip()
        if target_url and not _is_http_url(target_url):
            raise BrowserLoginError(
                "invalid_url",
                "登录窗口只支持 http 或 https 地址。",
            )

        async with self._lock:
            existing = self._sessions.get(int(profile.id))
            if existing is not None:
                existing.task_ids.add(int(task.id))
                self._task_to_profile[int(task.id)] = int(profile.id)
                if target_url:
                    await existing.page.goto(
                        target_url,
                        wait_until="domcontentloaded",
                    )
                return self._session_payload(task, profile, existing)

            runner = self._runner_factory(self._runner_config(profile))
            playwright = await async_playwright().start()
            browser = None
            context = None
            try:
                browser, context = await runner.launch_browser(playwright)
                pages = list(getattr(context, "pages", []) or [])
                page = pages[0] if pages else await context.new_page()
                if target_url:
                    await page.goto(
                        target_url,
                        wait_until="domcontentloaded",
                    )
            except Exception as exc:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                try:
                    await playwright.stop()
                except Exception:
                    pass
                raise BrowserLoginError(
                    "browser_unavailable",
                    f"无法打开可见登录窗口：{exc}",
                ) from exc

            session = _BrowserLoginSession(
                profile_id=int(profile.id),
                playwright=playwright,
                browser=browser,
                context=context,
                page=page,
                task_ids={int(task.id)},
            )
            self._sessions[int(profile.id)] = session
            self._task_to_profile[int(task.id)] = int(profile.id)
            return self._session_payload(task, profile, session)

    async def close_task(self, task_id: int) -> None:
        async with self._lock:
            profile_id = self._task_to_profile.pop(int(task_id), None)
            if profile_id is None:
                return
            session = self._sessions.get(profile_id)
            if session is None:
                return
            session.task_ids.discard(int(task_id))
            if session.task_ids:
                return
            self._sessions.pop(profile_id, None)
            await self._close_session(session)

    async def shutdown(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._task_to_profile.clear()
        for session in sessions:
            await self._close_session(session)

    @staticmethod
    async def _close_session(session: _BrowserLoginSession) -> None:
        if session.context is not None:
            try:
                await session.context.close()
            except Exception:
                pass
        if session.browser is not None:
            try:
                await session.browser.close()
            except Exception:
                pass
        try:
            await session.playwright.stop()
        except Exception:
            pass

    @staticmethod
    def _session_payload(
        task: HumanTask,
        profile: BrowserProfile,
        session: _BrowserLoginSession,
    ) -> dict[str, Any]:
        return {
            "human_task_id": int(task.id),
            "browser_profile_id": int(profile.id),
            "profile_name": profile.name,
            "status": "open",
            "current_url": str(getattr(session.page, "url", "") or ""),
        }

    @staticmethod
    def _runner_config(profile: BrowserProfile) -> Any:
        user_data_dir = str(profile.user_data_dir or "").strip()
        profile_directory = str(
            profile.profile_directory or "Default"
        ).strip()
        if not user_data_dir:
            default_dir = Path.home() / ".nl-rpa" / "edge-profile"
            user_data_dir = str(default_dir)
        data_dir = Path(user_data_dir).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / profile_directory).mkdir(
            parents=True,
            exist_ok=True,
        )
        user_data_dir = str(data_dir)
        return SimpleNamespace(
            EXECUTION_TIMEOUT=300,
            SCREENSHOT_ENABLED=True,
            SCREENSHOT_TIMEOUT_MS=15000,
            SCREENSHOT_FULL_PAGE=True,
            BROWSER_MODE="edge",
            EDGE_EXECUTABLE_PATH="",
            EDGE_USER_DATA_DIR=user_data_dir,
            EDGE_PROFILE_DIRECTORY=profile_directory,
            BROWSER_HEADLESS=False,
        )


def _is_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _safe_display_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


__all__ = [
    "BrowserLoginError",
    "BrowserLoginSessionManager",
    "BrowserLoginVerifier",
]
