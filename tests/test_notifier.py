from __future__ import annotations

# ruff: noqa: E402

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "test-encryption-key")

from app.notifications import NotificationService
from app.notifications import notifier as notifier_module
from app.services.email import EmailResult


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        SMTP_HOST="smtp.example.com",
        SMTP_PORT=587,
        SMTP_USER="sender@example.com",
        SMTP_PASSWORD="secret",
    )


def _execution(
    status: str,
    *,
    screenshots: list[str] | None = None,
) -> SimpleNamespace:
    started_at = datetime(2026, 9, 16, 12, 0, 0)
    return SimpleNamespace(
        id=9,
        workflow_id=7,
        status=status,
        error_message=None if status == "success" else "Browser crashed",
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=2.5),
        screenshots=screenshots or [],
        step_logs=[],
    )


def _workflow(notification_config: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        name="Order sync",
        notification_config=notification_config,
    )


def _http_client(*, post_error: Exception | None = None) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(
        side_effect=post_error,
        return_value=SimpleNamespace(raise_for_status=MagicMock()),
    )
    return client


def test_notify_always(tmp_path: Path) -> None:
    async def scenario() -> tuple[
        AsyncMock,
        MagicMock,
    ]:
        screenshot = tmp_path / "latest.png"
        screenshot.write_bytes(b"image")
        service = NotificationService(_config())
        execution = _execution(
            "success",
            screenshots=[str(screenshot)],
        )
        workflow = _workflow(
            {
                "email": "recipient@example.com",
                "webhook_url": "https://hooks.example.com/run",
                "notify_on": "always",
            }
        )
        client = _http_client()
        email_service = SimpleNamespace(
            send_message=AsyncMock(
                return_value=EmailResult(status="success")
            )
        )

        with (
            patch.object(
                service,
                "_build_email_service",
                return_value=email_service,
            ),
            patch.object(
                notifier_module.httpx,
                "AsyncClient",
                return_value=client,
            ) as client_cls,
        ):
            await service.notify(execution, workflow)

        client_cls.assert_called_once_with(timeout=5.0)
        return email_service.send_message, client

    send_email, client = asyncio.run(scenario())

    send_email.assert_awaited_once()
    message = send_email.await_args.args[0]
    assert message["To"] == "recipient@example.com"
    assert any(
        part.get_content_type().startswith("image/")
        for part in message.walk()
    )
    client.post.assert_awaited_once()
    webhook_url, = client.post.await_args.args
    payload = client.post.await_args.kwargs["json"]
    assert webhook_url == "https://hooks.example.com/run"
    assert payload == {
        "workflow_id": 7,
        "status": "success",
        "error_message": None,
        "started_at": "2026-09-16T12:00:00",
        "finished_at": "2026-09-16T12:00:02.500000",
    }


def test_notify_failure_only_success() -> None:
    async def scenario() -> tuple[AsyncMock, AsyncMock]:
        service = NotificationService(_config())
        execution = _execution("success")
        workflow = _workflow(
            {
                "email": "recipient@example.com",
                "webhook_url": "https://hooks.example.com/run",
                "notify_on": "failure_only",
            }
        )

        with (
            patch.object(
                service,
                "_send_email",
                new=AsyncMock(),
            ) as send_email,
            patch.object(
                service,
                "_send_webhook",
                new=AsyncMock(),
            ) as send_webhook,
        ):
            await service.notify(execution, workflow)

        return send_email, send_webhook

    send_email, send_webhook = asyncio.run(scenario())

    send_email.assert_not_awaited()
    send_webhook.assert_not_awaited()


def test_notify_failure_only_failed() -> None:
    async def scenario() -> tuple[AsyncMock, AsyncMock]:
        service = NotificationService(_config())
        execution = _execution("failed")
        workflow = _workflow(
            {
                "email": "recipient@example.com",
                "webhook_url": "https://hooks.example.com/run",
                "notify_on": "failure_only",
            }
        )

        with (
            patch.object(
                service,
                "_send_email",
                new=AsyncMock(),
            ) as send_email,
            patch.object(
                service,
                "_send_webhook",
                new=AsyncMock(),
            ) as send_webhook,
        ):
            await service.notify(execution, workflow)

        return send_email, send_webhook

    send_email, send_webhook = asyncio.run(scenario())

    send_email.assert_awaited_once()
    send_webhook.assert_awaited_once()


def test_send_webhook_failure() -> None:
    async def scenario() -> MagicMock:
        service = NotificationService(_config())
        execution = _execution("failed")
        workflow = _workflow({})
        client = _http_client(
            post_error=httpx.ConnectError("network unavailable"),
        )

        with patch.object(
            notifier_module.httpx,
            "AsyncClient",
            return_value=client,
        ):
            await service._send_webhook(
                "https://hooks.example.com/run",
                execution,
                workflow,
            )

        return client

    client = asyncio.run(scenario())

    client.post.assert_awaited_once()


def test_notification_email_reuses_generic_email_service() -> None:
    async def scenario():
        service = NotificationService(_config())
        email_service = SimpleNamespace(
            send_message=AsyncMock(),
        )
        service._build_email_service = MagicMock(
            return_value=email_service
        )
        await service._send_email(
            "recipient@example.com",
            _execution("failed"),
            _workflow({}),
        )
        return email_service

    email_service = asyncio.run(scenario())

    email_service.send_message.assert_awaited_once()
