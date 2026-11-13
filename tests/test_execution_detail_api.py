from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api import executions as executions_api
from app.api.dependencies import get_current_user
from app.database import Base, get_db
from app.models.delivery_record import DeliveryRecord
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion


async def _database():
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
    return factory, engine


async def _seed(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        user = User(
            id=1,
            username="execution-detail",
            email="detail@example.com",
            hashed_password="hash",
        )
        workflow = Workflow(
            user_id=1,
            name="Versioned report",
            natural_language_input="generate report",
            status="published",
        )
        session.add_all([user, workflow])
        await session.flush()
        version = WorkflowVersion(
            workflow_id=workflow.id,
            version=3,
            plan={"execution_mode": "structured", "steps": []},
            validation={"valid": True},
            action_catalog_version="2026-10-28.1",
        )
        session.add(version)
        await session.flush()
        workflow.active_version_id = version.id
        execution = ExecutionLog(
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            status="failed",
            metrics={"variables": {"large_rows": [{"id": 1}]}},
            step_logs=[],
            artifacts=[],
        )
        session.add(execution)
        await session.flush()
        session.add_all(
            [
                DeliveryRecord(
                    execution_id=execution.id,
                    step_id=2,
                    channel="email",
                    status="failed",
                    recipient=["user@example.com"],
                    error="SMTP unavailable",
                ),
                DeliveryRecord(
                    execution_id=execution.id,
                    step_id=3,
                    channel="email",
                    status="success",
                    recipient=["ops@example.com"],
                    provider_message_id="<message@example.com>",
                ),
            ]
        )
        await session.commit()
        return int(execution.id)


def _app(session: AsyncSession) -> FastAPI:
    app = FastAPI()
    app.include_router(executions_api.router)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[executions_api.get_workspace_context] = (
        lambda: SimpleNamespace(
            isolation_enabled=False,
            workspace_id=None,
        )
    )
    return app


async def _request(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.get(path)


def test_execution_detail_exposes_version_variables_and_deliveries() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            execution_id = await _seed(factory)
            async with factory() as session:
                app = _app(session)
                detail = await _request(
                    app,
                    f"/api/executions/{execution_id}",
                )
                legacy_detail = await _request(
                    app,
                    f"/api/executions/{execution_id}/detail",
                )
                listing = await _request(app, "/api/executions")
            return detail, legacy_detail, listing
        finally:
            await engine.dispose()

    detail, legacy_detail, listing = asyncio.run(scenario())

    assert detail.status_code == 200
    assert legacy_detail.status_code == 200
    assert legacy_detail.json()["workflow_version"] == 3
    payload = detail.json()
    assert payload["workflow_version_id"] > 0
    assert payload["workflow_version"] == 3
    assert payload["workflow_name"] == "Versioned report"
    assert payload["variables"]["large_rows"] == [{"id": 1}]
    assert [item["status"] for item in payload["deliveries"]] == [
        "failed",
        "success",
    ]
    assert any(
        item["status"] == "failed" and item["error"] == "SMTP unavailable"
        for item in payload["deliveries"]
    )

    assert listing.status_code == 200
    item = listing.json()[0]
    assert item["workflow_version"] == 3
    assert item["workflow_name"] == "Versioned report"
