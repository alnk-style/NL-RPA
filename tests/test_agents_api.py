from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api import agents_router
from app.api.agents import pairing_cache
from app.api.dependencies import get_current_user
from app.database import Base, get_db
from app.models.agent import DesktopAgent
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


async def _seed_user(session: AsyncSession) -> User:
    user = User(
        id=1,
        username="agent-user",
        email="agent-user@example.com",
        hashed_password="hash",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _app(
    session: AsyncSession,
    user: User | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(agents_router)

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


def test_generate_pairing_code() -> None:
    async def scenario() -> tuple[int, dict, int | None, int]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            pairing_cache.clear()

            response = await _request(
                _app(session, user),
                "POST",
                "/api/agents/pairing-code",
            )
            payload = response.json()
            consumed_user_id = pairing_cache.consume_code(payload["pairing_code"])
            return (
                response.status_code,
                payload,
                consumed_user_id,
                user.id,
            )
        finally:
            await session.close()
            await engine.dispose()

    status_code, payload, consumed_user_id, user_id = asyncio.run(scenario())

    assert status_code == 200
    assert len(payload["pairing_code"]) == 6
    assert payload["pairing_code"].isdigit()
    assert payload["expires_in"] == 300
    assert consumed_user_id == user_id


def test_pair_success() -> None:
    async def scenario() -> tuple[int, dict, DesktopAgent]:
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
                    "name": "DESKTOP-01",
                    "platform": "windows",
                    "version": "0.1.0",
                    "capabilities": ["window", "wps"],
                },
            )
            payload = response.json()
            agent = await session.get(
                DesktopAgent,
                payload["agent_id"],
            )
            assert agent is not None
            return response.status_code, payload, agent
        finally:
            await session.close()
            await engine.dispose()

    status_code, payload, agent = asyncio.run(scenario())

    assert status_code == 200
    assert payload["agent_id"] == agent.id
    assert len(payload["token"]) >= 32
    assert agent.user_id == 1
    assert agent.name == "DESKTOP-01"
    assert agent.status == "offline"
    assert (
        agent.token_hash == hashlib.sha256(payload["token"].encode("utf-8")).hexdigest()
    )


def test_pair_invalid_code() -> None:
    async def scenario() -> int:
        session, engine = await _database()
        try:
            await _seed_user(session)
            pairing_cache.clear()
            response = await _request(
                _app(session),
                "POST",
                "/api/agents/pair",
                json={
                    "pairing_code": "000000",
                    "name": "DESKTOP-01",
                    "platform": "windows",
                    "version": "0.1.0",
                    "capabilities": [],
                },
            )
            return response.status_code
        finally:
            await session.close()
            await engine.dispose()

    assert asyncio.run(scenario()) == 400


def test_pair_expired_code() -> None:
    async def scenario() -> int:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            pairing_cache.clear()
            pairing_cache.store_code("654321", user.id, ttl=-1)

            response = await _request(
                _app(session),
                "POST",
                "/api/agents/pair",
                json={
                    "pairing_code": "654321",
                    "name": "DESKTOP-01",
                    "platform": "windows",
                    "version": "0.1.0",
                    "capabilities": [],
                },
            )
            return response.status_code
        finally:
            await session.close()
            await engine.dispose()

    assert asyncio.run(scenario()) == 400


def test_list_agents() -> None:
    async def scenario() -> tuple[int, dict]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            session.add_all(
                [
                    DesktopAgent(
                        id=1,
                        user_id=user.id,
                        name="DESKTOP-01",
                        platform="windows",
                        version="0.1.0",
                        capabilities=["window"],
                        status="online",
                        token_hash="hash",
                        last_seen_at=datetime(2026, 9, 17, 10, 0, 0),
                    ),
                    DesktopAgent(
                        id=2,
                        user_id=user.id,
                        name="DESKTOP-02",
                        platform="windows",
                        version="0.1.0",
                        capabilities=["wps"],
                        status="offline",
                        token_hash="hash-2",
                    ),
                    DesktopAgent(
                        id=3,
                        user_id=2,
                        name="OTHER",
                        platform="windows",
                        version="0.1.0",
                        capabilities=[],
                        status="online",
                        token_hash="hash-3",
                    ),
                ]
            )
            await session.commit()

            response = await _request(
                _app(session, user),
                "GET",
                "/api/agents",
            )
            return response.status_code, response.json()
        finally:
            await session.close()
            await engine.dispose()

    status_code, payload = asyncio.run(scenario())

    assert status_code == 200
    assert len(payload["agents"]) == 2
    assert [agent["name"] for agent in payload["agents"]] == [
        "DESKTOP-02",
        "DESKTOP-01",
    ]
    assert payload["agents"][1]["status"] == "online"
    assert payload["agents"][1]["last_seen_at"] is not None


def test_list_agents_uses_live_connection_state() -> None:
    async def scenario() -> tuple[int, dict]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            session.add(
                DesktopAgent(
                    id=1,
                    user_id=user.id,
                    name="DESKTOP-01",
                    platform="windows",
                    version="0.1.0",
                    capabilities=["window"],
                    status="online",
                    token_hash="hash",
                )
            )
            await session.commit()

            app = _app(session, user)
            app.state.connection_manager = SimpleNamespace(
                is_online=lambda agent_id: False,
            )
            response = await _request(app, "GET", "/api/agents")
            return response.status_code, response.json()
        finally:
            await session.close()
            await engine.dispose()

    status_code, payload = asyncio.run(scenario())

    assert status_code == 200
    assert payload["agents"][0]["status"] == "offline"


def test_revoke_agent() -> None:
    async def scenario() -> tuple[int, dict, DesktopAgent]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            agent = DesktopAgent(
                user_id=user.id,
                name="DESKTOP-01",
                platform="windows",
                version="0.1.0",
                capabilities=["window"],
                status="online",
                token_hash="token-hash",
            )
            session.add(agent)
            await session.commit()
            await session.refresh(agent)

            response = await _request(
                _app(session, user),
                "POST",
                f"/api/agents/{agent.id}/revoke",
            )
            await session.refresh(agent)
            return response.status_code, response.json(), agent
        finally:
            await session.close()
            await engine.dispose()

    status_code, payload, agent = asyncio.run(scenario())

    assert status_code == 200
    assert payload == {"status": "revoked"}
    assert agent.status == "offline"
    assert agent.token_hash == ""


def test_delete_agent() -> None:
    async def scenario() -> tuple[int, dict, bool, AsyncMock]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            agent = DesktopAgent(
                user_id=user.id,
                name="DESKTOP-01",
                platform="windows",
                version="0.1.0",
                capabilities=["window"],
                status="online",
                token_hash="token-hash",
            )
            session.add(agent)
            await session.commit()
            await session.refresh(agent)

            disconnect = AsyncMock()
            app = _app(session, user)
            app.state.agent_hub = SimpleNamespace(
                disconnect=disconnect,
            )
            response = await _request(
                app,
                "DELETE",
                f"/api/agents/{agent.id}",
            )
            deleted_agent = await session.get(DesktopAgent, agent.id)
            return (
                response.status_code,
                response.json(),
                deleted_agent is None,
                disconnect,
            )
        finally:
            await session.close()
            await engine.dispose()

    status_code, payload, deleted, disconnect = asyncio.run(scenario())

    assert status_code == 200
    assert payload == {"status": "revoked"}
    assert deleted is True
    disconnect.assert_awaited_once_with(1)
