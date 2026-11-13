from __future__ import annotations

import asyncio
from email.message import EmailMessage
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
from app.services.email import EmailService, EmailSettings
from tests.support.fake_smtp import FakeSmtpServer
from unittest.mock import AsyncMock, patch


def test_delivery_record_is_written_for_action_email() -> None:
    async def scenario() -> tuple[str, int]:
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
                    username="u",
                    email="u@example.com",
                    hashed_password="x",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="mail",
                natural_language_input="send",
            )
            session.add(workflow)
            await session.flush()
            execution = ExecutionLog(
                workflow_id=workflow.id,
                status="running",
            )
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
            execution_id = execution.id

        with FakeSmtpServer() as server:
            service = EmailService(
                EmailSettings(
                    host="127.0.0.1",
                    port=server.port,
                    username=None,
                    password=None,
                    from_email="sender@example.com",
                    start_tls=False,
                    default_recipient=None,
                ),
                session_factory=factory,
            )
            message = EmailMessage()
            message["To"] = "recipient@example.com"
            message["Subject"] = "hello"
            message.set_content("body")
            result = await service.send_message(
                message,
                execution_id=execution_id,
                step_id=2,
            )

        async with factory() as session:
            record = await session.get(DeliveryRecord, result.delivery_record_id)
            assert record is not None
            status = record.status
            assert record.execution_id == execution_id
            assert record.channel == "email"
        await engine.dispose()
        return status, result.delivery_record_id

    status, record_id = asyncio.run(scenario())
    assert status == "success"
    assert record_id > 0


def test_failed_email_deliveries_have_explicit_records(
    tmp_path,
) -> None:
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
                    username="delivery-errors",
                    email="owner@example.com",
                    hashed_password="hash",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="Delivery errors",
                natural_language_input="send",
            )
            session.add(workflow)
            await session.flush()
            executions = [
                ExecutionLog(
                    workflow_id=workflow.id,
                    status="running",
                    started_at=datetime.utcnow(),
                )
                for _ in range(5)
            ]
            session.add_all(executions)
            await session.commit()
            for execution in executions:
                await session.refresh(execution)
            execution_ids = [item.id for item in executions]

        valid_settings = EmailSettings(
            host="127.0.0.1",
            port=2525,
            username=None,
            password=None,
            from_email="sender@example.com",
            start_tls=False,
            default_recipient=None,
        )
        unconfigured = EmailSettings(
            host=None,
            port=2525,
            username=None,
            password=None,
            from_email="sender@example.com",
            start_tls=False,
            default_recipient=None,
        )
        empty = tmp_path / "empty.csv"
        empty.write_bytes(b"")

        def message(recipient: str = "recipient@example.com"):
            item = EmailMessage()
            item["To"] = recipient
            item["Subject"] = "failure"
            item.set_content("body")
            return item

        results = []
        results.append(
            await EmailService(
                unconfigured,
                session_factory=factory,
            ).send_message(
                message(),
                execution_id=execution_ids[0],
                step_id=1,
            )
        )
        results.append(
            await EmailService(
                valid_settings,
                session_factory=factory,
            ).send_message(
                message("not-an-email"),
                execution_id=execution_ids[1],
                step_id=2,
            )
        )
        results.append(
            await EmailService(
                valid_settings,
                session_factory=factory,
            ).send_message(
                message(),
                attachments=[empty],
                execution_id=execution_ids[2],
                step_id=3,
            )
        )
        with patch(
            "app.services.email.aiosmtplib.send",
            new=AsyncMock(side_effect=RuntimeError("authentication failed")),
        ):
            results.append(
                await EmailService(
                    valid_settings,
                    session_factory=factory,
                ).send_message(
                    message(),
                    execution_id=execution_ids[3],
                    step_id=4,
                )
            )
        with patch(
            "app.services.email.aiosmtplib.send",
            new=AsyncMock(side_effect=TimeoutError("SMTP timeout")),
        ):
            results.append(
                await EmailService(
                    valid_settings,
                    session_factory=factory,
                ).send_message(
                    message(),
                    execution_id=execution_ids[4],
                    step_id=5,
                )
            )

        async with factory() as session:
            records = []
            for execution_id in execution_ids:
                records.append(
                    await session.scalar(
                        select(DeliveryRecord).where(
                            DeliveryRecord.execution_id == execution_id
                        )
                    )
                )
        await engine.dispose()
        return results, records

    from sqlalchemy import select

    results, records = asyncio.run(scenario())

    assert all(result.status == "failed" for result in results)
    assert all(record is not None for record in records)
    assert [record.status for record in records] == ["failed"] * 5
    assert [record.step_id for record in records] == [1, 2, 3, 4, 5]
    assert results[0].error_category == "delivery_failed"
    assert results[1].error_category == "delivery_failed"
    assert results[2].error_category == "artifact_missing"
    assert results[3].error_category == "delivery_failed"
    assert results[4].error_category == "delivery_failed"
    assert records[2].attachment_paths[0]["size"] == 0


def test_email_retry_reuses_delivery_record_and_counts_attempts() -> None:
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
                    username="retry-owner",
                    email="owner@example.com",
                    hashed_password="hash",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="Retry delivery",
                natural_language_input="send",
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

        settings = EmailSettings(
            host="127.0.0.1",
            port=2525,
            username=None,
            password=None,
            from_email="sender@example.com",
            start_tls=False,
            default_recipient=None,
        )
        message = EmailMessage()
        message["To"] = "recipient@example.com"
        message.set_content("body")
        service = EmailService(settings, session_factory=factory)
        first = await service.send_message(
            message,
            execution_id=execution_id,
            step_id=4,
        )
        second = await service.send_message(
            message,
            execution_id=execution_id,
            step_id=4,
        )
        async with factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(DeliveryRecord).where(
                            DeliveryRecord.execution_id == execution_id
                        )
                    )
                ).all()
            )
        await engine.dispose()
        return first, second, rows

    from sqlalchemy import select

    first, second, rows = asyncio.run(scenario())

    assert first.status == "failed"
    assert second.status == "failed"
    assert len(rows) == 1
    assert rows[0].recipient["attempt_count"] == 2
