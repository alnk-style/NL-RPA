from __future__ import annotations

import asyncio
import logging
import mimetypes
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from app.services.email import EmailService, EmailSettings

if TYPE_CHECKING:
    from app.config import Settings
    from app.models.execution import ExecutionLog
    from app.models.workflow import Workflow


logger = logging.getLogger(__name__)


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class NotificationService:
    def __init__(self, config: Settings):
        self.config = config
        self.smtp_host = getattr(config, "SMTP_HOST", None)
        self.smtp_port = int(getattr(config, "SMTP_PORT", 587))
        self.smtp_user = getattr(config, "SMTP_USER", None)
        self.smtp_password = getattr(config, "SMTP_PASSWORD", None)
        self.from_email = (
            getattr(config, "SMTP_FROM_EMAIL", None)
            or getattr(config, "SMTP_FROM", None)
            or self.smtp_user
        )
        self.smtp_start_tls = bool(
            getattr(config, "SMTP_START_TLS", True)
        )

    async def notify(
        self,
        execution: ExecutionLog,
        workflow: Workflow,
    ) -> None:
        notification_config = workflow.notification_config
        if not isinstance(notification_config, dict):
            return

        if (
            notification_config.get("notify_on") == "failure_only"
            and execution.status == "success"
        ):
            return

        tasks = []
        to_email = notification_config.get("email")
        if to_email:
            tasks.append(
                self._send_email(str(to_email), execution, workflow)
            )

        webhook_url = notification_config.get("webhook_url")
        if webhook_url:
            tasks.append(
                self._send_webhook(
                    str(webhook_url),
                    execution,
                    workflow,
                )
            )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_email(
        self,
        to_email: str,
        execution: ExecutionLog,
        workflow: Workflow,
    ) -> None:
        if not self.smtp_host:
            logger.warning("SMTP_HOST is not configured; email skipped")
            return

        success = execution.status == "success"
        subject = (
            f"[NL-RPA] 流程 {workflow.name} "
            f"执行{'成功' if success else '失败'}"
        )
        body = self._build_email_body(execution, workflow)

        message = EmailMessage()
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)
        self._attach_latest_screenshot(message, execution)

        try:
            result = await self._build_email_service().send_message(message)
            if result.status != "success":
                logger.warning(
                    "Failed to send workflow %s notification email: %s",
                    workflow.id,
                    result.error,
                )
        except Exception:
            logger.exception(
                "Failed to send workflow %s notification email",
                workflow.id,
            )

    async def _send_webhook(
        self,
        webhook_url: str,
        execution: ExecutionLog,
        workflow: Workflow,
    ) -> None:
        payload = {
            "workflow_id": workflow.id,
            "status": execution.status,
            "error_message": execution.error_message,
            "started_at": _isoformat(execution.started_at),
            "finished_at": _isoformat(execution.finished_at),
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(webhook_url, json=payload)
                response.raise_for_status()
        except Exception:
            logger.exception(
                "Failed to send workflow %s webhook notification",
                workflow.id,
            )

    async def send_alert(
        self,
        subject: str,
        body: str,
        *,
        email: str | None = None,
        webhook_url: str | None = None,
        payload: dict | None = None,
    ) -> dict[str, bool]:
        """发送运维告警，复用现有 SMTP / Webhook 通道。

        返回每个通道是否真正投递成功；未配置的通道返回 False，
        不抛异常，调用方可以安全地做最佳努力通知。
        """

        results = {"email": False, "webhook": False}
        tasks: list[tuple[str, Any]] = []
        if email:
            tasks.append(
                ("email", self._send_alert_email(email, subject, body))
            )
        if webhook_url:
            tasks.append(
                (
                    "webhook",
                    self._send_alert_webhook(
                        webhook_url,
                        subject,
                        body,
                        payload,
                    ),
                )
            )
        if not tasks:
            return results

        completed = await asyncio.gather(
            *(task for _, task in tasks),
            return_exceptions=True,
        )
        for (channel, _task), outcome in zip(tasks, completed):
            results[channel] = outcome is True
        return results

    async def _send_alert_email(
        self,
        to_email: str,
        subject: str,
        body: str,
    ) -> bool:
        if not self.smtp_host:
            logger.warning(
                "SMTP_HOST is not configured; alert email skipped"
            )
            return False

        message = EmailMessage()
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)
        try:
            result = await self._build_email_service().send_message(message)
            if result.status != "success":
                logger.warning(
                    "Failed to send alert email: %s",
                    result.error,
                )
                return False
        except Exception:
            logger.exception("Failed to send alert email")
            return False
        return True

    def _build_email_service(self) -> EmailService:
        return EmailService(EmailSettings.from_config(self.config))

    async def _send_alert_webhook(
        self,
        webhook_url: str,
        subject: str,
        body: str,
        payload: dict | None,
    ) -> bool:
        alert_payload = dict(payload or {})
        alert_payload.setdefault("subject", subject)
        alert_payload.setdefault("message", body)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    webhook_url,
                    json=alert_payload,
                )
                response.raise_for_status()
        except Exception:
            logger.exception("Failed to send alert webhook")
            return False
        return True

    @staticmethod
    def _build_email_body(
        execution: ExecutionLog,
        workflow: Workflow,
    ) -> str:
        duration = "未知"
        if execution.started_at and execution.finished_at:
            duration_seconds = (
                execution.finished_at - execution.started_at
            ).total_seconds()
            duration = f"{duration_seconds:.2f} 秒"

        return "\n".join(
            [
                f"流程：{workflow.name}",
                f"状态：{'成功' if execution.status == 'success' else '失败'}",
                f"执行时间：{_isoformat(execution.started_at) or '未知'}",
                f"完成时间：{_isoformat(execution.finished_at) or '未知'}",
                f"耗时：{duration}",
                f"错误信息：{execution.error_message or '无'}",
            ]
        )

    @staticmethod
    def _attach_latest_screenshot(
        message: EmailMessage,
        execution: ExecutionLog,
    ) -> None:
        screenshots = execution.screenshots or []
        if not screenshots:
            return

        screenshot_path = Path(str(screenshots[-1]))
        try:
            image_data = screenshot_path.read_bytes()
        except OSError:
            logger.warning(
                "Screenshot attachment not found: %s",
                screenshot_path,
            )
            return

        content_type, _encoding = mimetypes.guess_type(
            screenshot_path.name
        )
        subtype = (
            content_type.split("/", maxsplit=1)[1]
            if content_type and content_type.startswith("image/")
            else "png"
        )
        message.add_attachment(
            image_data,
            maintype="image",
            subtype=subtype,
            filename=screenshot_path.name,
        )


__all__ = ["NotificationService"]
