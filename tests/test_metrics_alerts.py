"""阈值告警：规则、真实本地 SMTP / Webhook 投递与冷却。"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import models  # noqa: F401
from app.database import Base
from app.metrics.alerts import (
    MetricsAlertService,
    evaluate_alerts,
    resolve_thresholds,
)
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.notifications import NotificationService


def test_evaluate_alerts_uses_comparators_and_severity() -> None:
    metrics = {
        "locator_failure_rate": {"value": 0.4},
        "first_generation_success_rate": {"value": 0.9},
        "execution_duration_delta_ratio": {"value": None},
    }
    thresholds = {
        "locator_failure_rate": {
            "comparator": "gt",
            "threshold": 0.15,
            "severity": "critical",
        },
        "first_generation_success_rate": {
            "comparator": "lt",
            "threshold": 0.6,
        },
        "execution_duration_delta_ratio": {
            "comparator": "gt",
            "threshold": 1.2,
        },
    }
    alerts = evaluate_alerts(metrics, thresholds)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.metric == "locator_failure_rate"
    assert alert.severity == "critical"
    assert alert.observed == 0.4


def test_resolve_thresholds_merges_json_override() -> None:
    config = SimpleNamespace(
        METRICS_ALERT_THRESHOLDS=json.dumps(
            {"locator_failure_rate": {"threshold": 0.05}}
        )
    )
    thresholds = resolve_thresholds(config)
    assert thresholds["locator_failure_rate"]["threshold"] == 0.05
    assert thresholds["locator_failure_rate"]["comparator"] == "gt"

    invalid = resolve_thresholds(
        SimpleNamespace(METRICS_ALERT_THRESHOLDS="{not-json")
    )
    assert invalid["locator_failure_rate"]["threshold"] == 0.15


class FakeSMTPServer:
    """最小 SMTP 会话，用于验证告警邮件真的发出。"""

    def __init__(self) -> None:
        self.messages: list[bytes] = []
        self._server: asyncio.AbstractServer | None = None
        self.port: int | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle,
            host="127.0.0.1",
            port=0,
        )
        self.port = int(self._server.sockets[0].getsockname()[1])

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        writer.write(b"220 fake-esmtp ready\r\n")
        await writer.drain()
        data_lines: list[bytes] = []
        in_data = False
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                if in_data:
                    if line.rstrip(b"\r\n") == b".":
                        in_data = False
                        self.messages.append(b"".join(data_lines))
                        data_lines = []
                        writer.write(b"250 queued\r\n")
                    else:
                        data_lines.append(line)
                else:
                    command = line.strip().upper()
                    if command.startswith((b"EHLO", b"HELO")):
                        writer.write(b"250-fake-esmtp\r\n250 SIZE 1048576\r\n")
                    elif command.startswith((b"MAIL FROM", b"RCPT TO")):
                        writer.write(b"250 OK\r\n")
                    elif command == b"DATA":
                        in_data = True
                        writer.write(b"354 end with .\r\n")
                    elif command == b"RSET":
                        writer.write(b"250 OK\r\n")
                    elif command == b"QUIT":
                        writer.write(b"221 bye\r\n")
                        await writer.drain()
                        break
                    else:
                        writer.write(b"250 OK\r\n")
                await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass


def test_send_alert_email_reaches_local_smtp() -> None:
    async def scenario() -> tuple[dict, list[bytes], FakeSMTPServer]:
        server = FakeSMTPServer()
        await server.start()
        try:
            service = NotificationService(
                SimpleNamespace(
                    SMTP_HOST="127.0.0.1",
                    SMTP_PORT=server.port,
                    SMTP_USER=None,
                    SMTP_PASSWORD=None,
                    SMTP_FROM_EMAIL="alerts@example.com",
                    SMTP_START_TLS=False,
                )
            )
            result = await service.send_alert(
                "[NL-RPA] 测试告警",
                "定位失败率超过阈值",
                email="ops@example.com",
            )
            await asyncio.sleep(0.05)
            return result, list(server.messages), server
        finally:
            await server.stop()

    result, messages, server = asyncio.run(scenario())
    assert result["email"] is True
    assert result["webhook"] is False
    assert len(messages) == 1
    assert server.messages
    text = messages[0].decode("utf-8", errors="replace")
    assert "ops@example.com" in text


def test_send_alert_webhook_posts_payload() -> None:
    captured: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            captured.append(json.loads(raw.decode("utf-8")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args) -> None:  # noqa: D102
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        service = NotificationService(SimpleNamespace())
        result = asyncio.run(
            service.send_alert(
                "subject",
                "body",
                webhook_url=(
                    f"http://127.0.0.1:{httpd.server_address[1]}/hook"
                ),
                payload={"type": "metrics_alert", "alerts": []},
            )
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert result["webhook"] is True
    assert len(captured) == 1
    assert captured[0]["type"] == "metrics_alert"
    assert captured[0]["alerts"] == []
    assert captured[0]["subject"] == "subject"
    assert captured[0]["message"] == "body"


@pytest.mark.asyncio
async def test_alert_service_check_once_and_cooldown(tmp_path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path.as_posix()}/alerts.db"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(
            User(id=1, username="u", email="u@x", hashed_password="x")
        )
        workflow = Workflow(
            user_id=1,
            name="w",
            natural_language_input="demo",
        )
        session.add(workflow)
        await session.commit()
        await session.refresh(workflow)
        session.add(
            ExecutionLog(
                workflow_id=workflow.id,
                status="failed",
                started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=9),
                step_logs=[
                    {
                        "step_index": 0,
                        "status": "failed",
                        "failure_category": "locator_not_found",
                    }
                ],
            )
        )
        await session.commit()

    notifier = SimpleNamespace(
        send_alert=AsyncMock(
            return_value={"email": True, "webhook": False}
        )
    )
    config = SimpleNamespace(
        METRICS_ALERT_THRESHOLDS=json.dumps(
            {"locator_failure_rate": {"threshold": 0.1}}
        ),
        METRICS_ALERT_WINDOW_SECONDS=3600,
        METRICS_ALERT_COOLDOWN_SECONDS=3600,
        METRICS_ALERT_EMAIL="ops@example.com",
        METRICS_ALERT_WEBHOOK="",
    )
    service = MetricsAlertService(
        notifier,
        config,
        session_factory=session_factory,
    )

    first = await service.check_once(now=now)
    assert first["execution_count"] == 1
    fired_metrics = [alert["metric"] for alert in first["fired"]]
    assert "locator_failure_rate" in fired_metrics
    assert first["delivered"] == {"email": True, "webhook": False}
    notifier.send_alert.assert_awaited_once()

    second = await service.check_once(now=now)
    assert second["fired"] == []
    assert notifier.send_alert.await_count == 1
    await engine.dispose()
