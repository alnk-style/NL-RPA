from __future__ import annotations

from typing import Any

from app.models.human_task import HumanTask
from app.services.human_tasks import VerificationResult


class CompositeHumanVerifier:
    def __init__(
        self,
        *,
        browser_verifier: Any | None = None,
        desktop_dispatcher: Any | None = None,
    ) -> None:
        self.browser_verifier = browser_verifier
        self.desktop_dispatcher = desktop_dispatcher

    async def verify(
        self,
        task: HumanTask,
        request_verification: dict[str, Any],
    ) -> VerificationResult:
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
        if verification_type in {
            "web_logged_in",
            "web_logged_out",
        }:
            if self.browser_verifier is None:
                return VerificationResult(
                    ok=False,
                    reason="浏览器登录状态验证器不可用。",
                    login_state="session_unavailable",
                )
            return await self.browser_verifier.verify(
                task,
                request_verification,
            )
        if verification_type == "desktop_window":
            return await self._verify_desktop_window(
                verification,
            )
        if verification_type == "manual_confirmation":
            return self._verify_manual_confirmation(
                task,
                request_verification,
            )
        return VerificationResult(
            ok=False,
            reason=f"不支持的验证类型：{verification_type}",
            login_state="unknown",
        )

    async def _verify_desktop_window(
        self,
        verification: dict[str, Any],
    ) -> VerificationResult:
        if self.desktop_dispatcher is None:
            return VerificationResult(
                ok=False,
                reason="桌面窗口检查器不可用。",
                login_state="session_unavailable",
            )
        try:
            inspected = await self.desktop_dispatcher.inspect(
                {"max_elements": 0},
                timeout=10,
            )
        except Exception as exc:
            return VerificationResult(
                ok=False,
                reason=f"桌面窗口检查失败：{exc}",
                login_state="unknown",
            )
        if not isinstance(inspected, dict) or not inspected.get("success"):
            return VerificationResult(
                ok=False,
                reason=(
                    "桌面 Agent 未能检查窗口："
                    f"{inspected.get('error') if isinstance(inspected, dict) else 'unknown'}"
                ),
                login_state="environment_unavailable",
            )
        expected_title = str(
            verification.get("window_title") or ""
        ).strip().casefold()
        expected_process = str(
            verification.get("process_name") or ""
        ).strip().casefold()
        windows = inspected.get("windows")
        for window in windows if isinstance(windows, list) else []:
            if not isinstance(window, dict):
                continue
            title = str(window.get("title") or "").casefold()
            process = str(window.get("process_name") or "").casefold()
            if expected_title and expected_title not in title:
                continue
            if expected_process and expected_process != process:
                continue
            return VerificationResult(
                ok=True,
                login_state="ready",
                details={
                    "type": "desktop_window",
                    "window_title": window.get("title"),
                    "process_name": window.get("process_name"),
                    "handle": window.get("handle"),
                },
            )
        return VerificationResult(
            ok=False,
            reason=(
                "未找到符合条件的目标窗口："
                f"{verification.get('window_title') or '任意标题'}"
            ),
            login_state="environment_unavailable",
        )

    @staticmethod
    def _verify_manual_confirmation(
        task: HumanTask,
        request_verification: dict[str, Any],
    ) -> VerificationResult:
        payload = task.payload if isinstance(task.payload, dict) else {}
        follow_up = payload.get("follow_up_verification")
        if not isinstance(follow_up, dict) or not follow_up:
            return VerificationResult(
                ok=False,
                reason=(
                    "人工确认缺少后续业务验证条件，"
                    "不能仅凭点击完成。"
                ),
                login_state="manual_confirmation_required",
            )
        if request_verification.get("manual_confirmed") is not True:
            return VerificationResult(
                ok=False,
                reason="需要明确提交人工确认证据。",
                login_state="manual_confirmation_required",
            )
        return VerificationResult(
            ok=True,
            login_state="ready",
            details={
                "type": "manual_confirmation",
                "manual_confirmed": True,
                "follow_up_verification": dict(follow_up),
            },
        )


__all__ = ["CompositeHumanVerifier"]
