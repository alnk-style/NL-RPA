from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api import agents_router, hosts_router
from app.api.agents import pairing_cache
from app.api.dependencies import get_current_user
from app.database import Base, get_db
from app.models.agent import DesktopAgent
from app.models.desktop_task import DesktopTask
from app.models.user import User


async def _database() -> tuple[AsyncSession, object]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return session_factory(), engine


async def _seed_user(session: AsyncSession, user_id: int = 1) -> User:
    user = User(
        id=user_id,
        username=f"host-user-{user_id}",
        email=f"host-{user_id}@example.com",
        hashed_password="hash",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _app(session: AsyncSession, user: User | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(agents_router)
    app.include_router(hosts_router)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    if user is not None:

        async def override_user() -> User:
            return user

        app.dependency_overrides[get_current_user] = override_user
    return app


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    **kwargs,
):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, **kwargs)


def test_pair_normalizes_vm_environment_and_capabilities() -> None:
    async def scenario() -> tuple[int, DesktopAgent]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            pairing_cache.clear()
            pairing_cache.store_code("123456", user.id)
            response = await _request(
                _app(session),
                "POST",
                "/api/agents/pair",
                json={
                    "pairing_code": "123456",
                    "name": "vm-agent",
                    "platform": "windows",
                    "version": "0.2.0",
                    "environment": "isolated",
                    "capabilities": ["window", "isolated_desktop"],
                    "connection_security": "wss",
                },
            )
            payload = response.json()
            agent = await session.get(DesktopAgent, payload["agent_id"])
            assert agent is not None
            return response.status_code, agent
        finally:
            await session.close()
            await engine.dispose()

    status_code, agent = asyncio.run(scenario())
    assert status_code == 200
    assert agent.environment == "vm"
    assert agent.capabilities == [
        "interactive_desktop",
        "isolated_desktop",
    ]


def test_pair_rejects_unknown_environment() -> None:
    async def scenario() -> int:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            pairing_cache.clear()
            pairing_cache.store_code("123456", user.id)
            response = await _request(
                _app(session),
                "POST",
                "/api/agents/pair",
                json={
                    "pairing_code": "123456",
                    "name": "bad-agent",
                    "platform": "windows",
                    "version": "0.2.0",
                    "environment": "cloud",
                    "capabilities": [],
                },
            )
            return response.status_code
        finally:
            await session.close()
            await engine.dispose()

    assert asyncio.run(scenario()) == 422


def test_hosts_list_is_scoped_and_reports_busy_vm_access() -> None:
    async def scenario() -> tuple[int, dict]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            agent = DesktopAgent(
                id=1,
                user_id=user.id,
                name="vm-agent",
                platform="windows",
                version="0.2.0",
                environment="vm",
                capabilities=["interactive_desktop", "isolated_desktop"],
                status="online",
                token_hash="hash",
                last_seen_at=datetime(2026, 11, 11, 10, 0, 0),
                human_access_url="https://console.example.test/vm/1",
                human_access_instructions="Open the trusted console.",
                connection_security="wss",
            )
            session.add(agent)
            await session.flush()
            session.add(
                DesktopTask(
                    workflow_id=1,
                    execution_log_id=1,
                    agent_id=1,
                    status="running",
                    actions=[],
                )
            )
            await session.commit()

            app = _app(session, user)
            app.state.connection_manager = SimpleNamespace(
                is_online=lambda agent_id: True,
                get_session_id=lambda agent_id: "session-1",
            )
            response = await _request(app, "GET", "/api/hosts")
            return response.status_code, response.json()
        finally:
            await session.close()
            await engine.dispose()

    status_code, payload = asyncio.run(scenario())
    assert status_code == 200
    assert len(payload["hosts"]) == 1
    host = payload["hosts"][0]
    assert host["host_id"] == 1
    assert host["environment"] == "vm"
    assert host["status"] == "busy"
    assert host["active_task_count"] == 1
    assert host["human_access_info"]["url"].startswith("https://")
    assert host["connection_security"] == "wss"


def test_host_access_configuration_is_user_scoped() -> None:
    async def scenario() -> tuple[int, dict]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            session.add(
                DesktopAgent(
                    id=1,
                    user_id=user.id,
                    name="vm-agent",
                    platform="windows",
                    version="0.2.0",
                    environment="vm",
                    capabilities=["isolated_desktop"],
                    status="online",
                    token_hash="hash",
                )
            )
            await session.commit()
            response = await _request(
                _app(session, user),
                "PATCH",
                "/api/hosts/1",
                json={
                    "human_access_url": "https://console.example.test/vm/1",
                    "human_access_instructions": "Use the approved console.",
                },
            )
            return response.status_code, response.json()
        finally:
            await session.close()
            await engine.dispose()

    status_code, payload = asyncio.run(scenario())
    assert status_code == 200
    assert payload["human_access_info"]["url"].startswith("https://")
    assert "approved console" in payload["human_access_info"]["instructions"]
