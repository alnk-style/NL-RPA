from __future__ import annotations

import asyncio
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.actions.protocol import StepStatus
from app.actions.registry import default_action_registry
from app.actions.runtime import ActionContext
from app.engine.dag import normalize_execution_plan
from app.engine.hybrid_executor import HybridExecutor
from app.engine.task_runtime import TaskRuntime
from app.protocols.structured import StepExecutor, StructuredStep
from app.services.email import EmailResult
from app.services.email import EmailService, EmailSettings
from tests.support.fake_smtp import FakeSmtpServer


def _settings(port: int, **overrides) -> EmailSettings:
    return EmailSettings(
        host="127.0.0.1",
        port=port,
        username=None,
        password=None,
        from_email="sender@example.com",
        start_tls=False,
        default_recipient=None,
        **overrides,
    )


def test_email_service_sends_mime_with_attachment_to_local_smtp(
    tmp_path: Path,
) -> None:
    attachment = tmp_path / "report.csv"
    attachment.write_bytes(b"name\nA\n")
    with FakeSmtpServer() as server:
        async def scenario():
            service = EmailService(_settings(server.port))
            message = EmailMessage()
            message["To"] = "recipient@example.com"
            message["Subject"] = "报告"
            message.set_content("body")
            return await service.send_message(
                message,
                attachments=[attachment],
            )

        result = asyncio.run(scenario())
    assert result.status == "success"
    assert result.provider_message_id
    assert server.messages
    assert b"report.csv" in server.messages[0]


def test_email_service_rejects_missing_smtp_and_attachment(
    tmp_path: Path,
) -> None:
    missing_config = asyncio.run(
        EmailService(
            EmailSettings(
                host=None,
                port=0,
                username=None,
                password=None,
                from_email=None,
                start_tls=False,
                default_recipient=None,
            )
        ).send_message(EmailMessage())
    )
    assert missing_config.status == "failed"
    assert missing_config.error_category == "delivery_failed"

    with FakeSmtpServer() as server:
        message = EmailMessage()
        message["To"] = "recipient@example.com"
        result = asyncio.run(
            EmailService(_settings(server.port)).send_message(
                message,
                attachments=[tmp_path / "missing.csv"],
            )
        )
    assert result.status == "failed"
    assert result.error_category == "artifact_missing"


def test_email_action_required_delivery_fails_and_optional_is_warning(
    tmp_path: Path,
) -> None:
    with FakeSmtpServer() as server:
        async def scenario():
            from app.actions.email import execute_email_action

            required = await execute_email_action(
                {
                    "to": ["recipient@example.com"],
                    "subject": "test",
                    "body": "body",
                    "attachments": [str(tmp_path / "missing.csv")],
                    "required_delivery": True,
                },
                email_service=EmailService(_settings(server.port)),
            )
            optional = await execute_email_action(
                {
                    "to": ["recipient@example.com"],
                    "subject": "test",
                    "body": "body",
                    "attachments": [str(tmp_path / "missing.csv")],
                    "required_delivery": False,
                },
                email_service=EmailService(_settings(server.port)),
            )
            return required, optional

        required, optional = asyncio.run(scenario())
    assert required.status is StepStatus.FAILED
    assert optional.status is StepStatus.WARNING


def test_email_action_rejects_invalid_recipient_before_smtp_send(
    tmp_path: Path,
) -> None:
    with FakeSmtpServer() as server:
        from app.actions.email import execute_email_action

        result = asyncio.run(
            execute_email_action(
                {
                    "to": ["not-an-email"],
                    "subject": "test",
                    "body": "body",
                    "attachments": [],
                    "required_delivery": True,
                },
                email_service=EmailService(_settings(server.port)),
            )
        )

    assert result.status is StepStatus.FAILED
    assert result.error is not None
    assert result.error.category == "delivery_failed"
    assert server.messages == []


def test_email_attachment_outside_allowed_roots_is_rejected(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("secret\n", encoding="utf-8")

    with FakeSmtpServer() as server:
        message = EmailMessage()
        message["To"] = "recipient@example.com"
        message.set_content("body")
        result = asyncio.run(
            EmailService(
                _settings(server.port),
                allowed_roots=[allowed],
            ).send_message(
                message,
                attachments=[outside],
            )
        )
        messages = server.messages

    assert result.status == "failed"
    assert result.error_category == "permission_denied"
    assert messages == []


def test_task_runtime_email_action_passes_execution_id_to_delivery_record(
    tmp_path: Path,
) -> None:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.models.delivery_record import DeliveryRecord
    from app.models.execution import ExecutionLog
    from app.models.user import User
    from app.models.workflow import Workflow

    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with factory() as session:
            session.add(
                User(
                    id=1,
                    username="runtime-email",
                    email="runtime@example.com",
                    hashed_password="hash",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="Email workflow",
                natural_language_input="send a report",
            )
            session.add(workflow)
            await session.flush()
            execution = ExecutionLog(
                workflow_id=workflow.id,
                status="running",
                started_at=datetime.utcnow(),
            )
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
            execution_id = execution.id

        with FakeSmtpServer() as server:
            runtime = TaskRuntime(
                action_registry=default_action_registry(),
                action_services={
                    "email_service": EmailService(
                        _settings(server.port),
                        session_factory=factory,
                    )
                },
            )
            result = await runtime.execute(
                {
                    "steps": [
                        {
                            "step_id": 1,
                            "executor": "email",
                            "action": "send",
                            "params": {
                                "to": ["recipient@example.com"],
                                "subject": "Report",
                                "body": "Attached",
                                "required_delivery": True,
                            },
                        }
                    ]
                },
                {"execution_id": execution_id},
            )

        async with factory() as session:
            record = await session.scalar(
                select(DeliveryRecord).where(
                    DeliveryRecord.execution_id == execution_id
                )
            )
        await engine.dispose()
        return result, record

    from sqlalchemy import select

    result, record = asyncio.run(scenario())

    assert result.success is True
    assert record is not None
    assert record.status == "success"
    assert record.step_id == 1


def test_missing_attachment_failure_record_lists_requested_attachment(
    tmp_path: Path,
) -> None:
    from datetime import datetime

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.models.delivery_record import DeliveryRecord
    from app.models.execution import ExecutionLog
    from app.models.user import User
    from app.models.workflow import Workflow

    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with factory() as session:
            session.add(
                User(
                    id=1,
                    username="attachment-owner",
                    email="owner@example.com",
                    hashed_password="hash",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="Attachment workflow",
                natural_language_input="send attachment",
            )
            session.add(workflow)
            await session.flush()
            execution = ExecutionLog(
                workflow_id=workflow.id,
                status="running",
                started_at=datetime.utcnow(),
            )
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
            execution_id = execution.id

        missing = tmp_path / "missing.csv"
        with FakeSmtpServer() as server:
            service = EmailService(
                _settings(server.port),
                session_factory=factory,
            )
            message = EmailMessage()
            message["To"] = "recipient@example.com"
            message.set_content("body")
            result = await service.send_message(
                message,
                attachments=[missing],
                execution_id=execution_id,
                step_id=3,
            )

        async with factory() as session:
            record = await session.scalar(
                select(DeliveryRecord).where(
                    DeliveryRecord.execution_id == execution_id
                )
            )
        await engine.dispose()
        return result, record, missing

    result, record, missing = asyncio.run(scenario())

    assert result.status == "failed"
    assert record is not None
    assert record.status == "failed"
    assert record.error
    assert record.attachment_paths
    assert record.attachment_paths[0]["path"] == str(missing.resolve())
    assert record.attachment_paths[0]["exists"] is False


def test_email_attaches_csv_xlsx_zip_and_keeps_bcc_private(
    tmp_path: Path,
) -> None:
    from app.actions.files import execute_file_action

    async def build_attachments():
        csv_result = await execute_file_action(
            "write_csv",
            {
                "path": str(tmp_path / "report.csv"),
                "rows": [{"name": "A"}],
            },
            variables={},
            allowed_roots=[tmp_path],
        )
        xlsx_result = await execute_file_action(
            "write_xlsx",
            {
                "path": str(tmp_path / "report.xlsx"),
                "rows": [{"name": "A"}],
            },
            variables={},
            allowed_roots=[tmp_path],
        )
        zip_result = await execute_file_action(
            "zip",
            {
                "destination": str(tmp_path / "bundle.zip"),
                "sources": [
                    csv_result.artifacts[0]["path"],
                    xlsx_result.artifacts[0]["path"],
                ],
            },
            variables={},
            allowed_roots=[tmp_path],
        )
        return [
            csv_result.artifacts[0]["path"],
            xlsx_result.artifacts[0]["path"],
            zip_result.artifacts[0]["path"],
        ]

    attachments = asyncio.run(build_attachments())
    with FakeSmtpServer() as server:
        message = EmailMessage()
        message["To"] = "recipient@example.com"
        message["Bcc"] = "hidden@example.com"
        message["Subject"] = "Files"
        message.set_content("Attached")
        result = asyncio.run(
            EmailService(_settings(server.port)).send_message(
                message,
                attachments=attachments,
            )
        )
        messages = server.messages
        envelopes = server.envelopes

    assert result.status == "success"
    assert {
        item["name"]
        for item in result.attachment_checksums or []
    } == {"report.csv", "report.xlsx", "bundle.zip"}
    assert any("hidden@example.com" in row for row in envelopes)
    assert b"Bcc: hidden@example.com" not in messages[0]


def test_email_action_resolves_credential_key_through_provider() -> None:
    email_service = SimpleNamespace(
        send_message=AsyncMock(
            return_value=EmailResult(
                status="success",
                provider_message_id="<message@example.com>",
                attachment_checksums=[],
            )
        )
    )

    async def provider(credential_key: str):
        assert credential_key == "smtp_work"
        return email_service

    async def scenario():
        registry = default_action_registry()
        return await registry.execute(
            "email",
            "send",
            {
                "to": ["recipient@example.com"],
                "subject": "Report",
                "body": "Body",
                "credential_key": "smtp_work",
            },
            context=ActionContext(
                workflow=None,
                execution_id=7,
                step_id=4,
                services={"email_service_provider": provider},
            ),
        )

    result = asyncio.run(scenario())

    assert result.status is StepStatus.SUCCESS
    assert result.value == "<message@example.com>"
    email_service.send_message.assert_awaited_once()


def test_hybrid_executor_routes_email_only_plan_to_task_runtime() -> None:
    step = StructuredStep(
        step_id=1,
        executor="email",
        action="send",
        params={
            "to": ["recipient@example.com"],
            "subject": "Report",
            "body": "Body",
        },
    )
    assert step.executor is StepExecutor.EMAIL

    nodes = normalize_execution_plan(
        {
            "steps": [
                {
                    "step_id": 1,
                    "executor": "email",
                    "action": "send",
                    "params": {
                        "to": ["recipient@example.com"],
                        "subject": "Report",
                        "body": "Body",
                    },
                }
            ]
        },
        default_executor="email",
    )

    assert HybridExecutor._uses_task_runtime(nodes) is True


def test_hybrid_executor_runs_saved_email_settings(monkeypatch) -> None:
    from datetime import datetime

    from cryptography.fernet import Fernet
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import StaticPool

    from app.config import get_settings
    from app.database import Base
    from app.models.delivery_record import DeliveryRecord
    from app.models.execution import ExecutionLog
    from app.models.user import User
    from app.models.workflow import Workflow
    from app.services.email import save_email_settings

    config = get_settings()
    monkeypatch.setattr(
        config,
        "CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    for name in (
        "SMTP_HOST",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "SMTP_FROM_EMAIL",
        "SMTP_DEFAULT_RECIPIENT",
    ):
        monkeypatch.setattr(config, name, None)

    async def scenario(server: FakeSmtpServer):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with factory() as session:
            session.add(
                User(
                    id=1,
                    username="hybrid-email",
                    email="hybrid@example.com",
                    hashed_password="hash",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="Hybrid mail",
                natural_language_input="send report",
            )
            session.add(workflow)
            await session.flush()
            execution = ExecutionLog(
                workflow_id=workflow.id,
                status="running",
                started_at=datetime.utcnow(),
            )
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
            await save_email_settings(
                session,
                config,
                user_id=1,
                value={
                    "host": server.host,
                    "port": server.port,
                    "from_email": "sender@example.com",
                    "start_tls": False,
                    "credential_key": "smtp_default",
                },
                password="secret",
            )
            await session.commit()
            execution_id = execution.id

        executor = HybridExecutor(
            None,
            session_factory=factory,
            config=config,
        )
        result = await executor.execute(
            SimpleNamespace(
                id=workflow.id,
                user_id=1,
                name="Hybrid mail",
                executor_type="email",
                required_capabilities=[],
                dag_definition=None,
                structured_plan={
                    "steps": [
                        {
                            "step_id": 1,
                            "executor": "email",
                            "action": "send",
                            "params": {
                                "to": ["recipient@example.com"],
                                "subject": "Report",
                                "body": "Attached",
                            },
                        }
                    ]
                },
            ),
            {"execution_id": execution_id},
        )
        async with factory() as session:
            record = await session.scalar(
                select(DeliveryRecord).where(
                    DeliveryRecord.execution_id == execution_id
                )
            )
        await engine.dispose()
        return result, record

    with FakeSmtpServer() as server:
        result, record = asyncio.run(scenario(server))

    assert result.success is True
    assert record is not None
    assert record.status == "success"
