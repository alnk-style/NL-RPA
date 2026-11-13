from __future__ import annotations

import asyncio

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.api.visual import router as visual_router
from app.database import Base, get_db
from app.config import settings
from app.models.user import User


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


def _app(factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = FastAPI()
    app.include_router(visual_router)

    async def override_db():
        async with factory() as session:
            yield session

    async def override_user():
        async with factory() as session:
            user = await session.get(User, 1)
            assert user is not None
            return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return app


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    json: dict | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=json)


def test_visual_settings_api_defaults_disabled_and_hides_secret(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    async def scenario():
        factory, engine = await _database()
        try:
            async with factory() as session:
                session.add(
                    User(
                        id=1,
                        username="visual-api",
                        email="visual-api@example.com",
                        hashed_password="hash",
                    )
                )
                await session.commit()
            app = _app(factory)
            initial = await _request(
                app,
                "GET",
                "/api/settings/visual",
            )
            saved = await _request(
                app,
                "PUT",
                "/api/settings/visual",
                json={
                    "enabled": True,
                    "provider": "remote_vlm",
                    "endpoint": "https://visual.example.com/locate",
                    "model": "fake-vlm",
                    "confidence_threshold": 0.92,
                    "max_candidates": 3,
                    "max_attempts": 2,
                    "screenshot_scope": "viewport",
                    "redaction_policy": {
                        "mask_password_fields": True,
                        "mask_sensitive_text": True,
                        "custom_rects": [],
                    },
                    "api_key": "top-secret",
                },
            )
            loaded = await _request(
                app,
                "GET",
                "/api/settings/visual",
            )
            return initial, saved, loaded
        finally:
            await engine.dispose()

    initial, saved, loaded = asyncio.run(scenario())

    assert initial.status_code == 200
    assert initial.json()["enabled"] is False
    assert initial.json()["provider"] == "disabled"
    assert saved.status_code == 200
    assert "top-secret" not in saved.text
    assert saved.json()["api_key_configured"] is True
    assert loaded.json()["provider"] == "remote_vlm"
    assert loaded.json()["confidence_threshold"] == 0.92


def test_visual_self_test_disabled_provider_does_not_call_network() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            async with factory() as session:
                session.add(
                    User(
                        id=1,
                        username="visual-api",
                        email="visual-api@example.com",
                        hashed_password="hash",
                    )
                )
                await session.commit()
            app = _app(factory)
            return await _request(
                app,
                "POST",
                "/api/settings/visual/test",
            )
        finally:
            await engine.dispose()

    response = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
