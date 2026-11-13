from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.credential import Credential
from app.models.user import User
from app.visual.settings import (
    VisualSettingsUpdate,
    load_visual_settings,
    save_visual_settings,
    visual_settings_payload,
)


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


def _config() -> SimpleNamespace:
    from cryptography.fernet import Fernet

    return SimpleNamespace(
        CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )


def test_visual_settings_default_to_disabled() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            async with factory() as session:
                session.add(
                    User(
                        id=1,
                        username="visual-user",
                        email="visual@example.com",
                        hashed_password="hash",
                    )
                )
                await session.commit()
                return await load_visual_settings(
                    session,
                    _config(),
                    1,
                )
        finally:
            await engine.dispose()

    settings = asyncio.run(scenario())

    assert settings.enabled is False
    assert settings.provider == "disabled"
    assert settings.max_candidates == 3
    assert settings.confidence_threshold == 0.9


def test_remote_visual_settings_require_https_and_do_not_echo_key() -> None:
    with pytest.raises(ValidationError):
        VisualSettingsUpdate(
            enabled=True,
            provider="remote_vlm",
            endpoint="http://insecure.example.com/visual",
        )
    with pytest.raises(ValidationError):
        VisualSettingsUpdate(
            enabled=True,
            provider="remote_vlm",
            endpoint="https://visual.example.com/locate",
            confidence_threshold=1.1,
        )

    async def scenario():
        factory, engine = await _database()
        config = _config()
        try:
            async with factory() as session:
                session.add(
                    User(
                        id=1,
                        username="visual-user",
                        email="visual@example.com",
                        hashed_password="hash",
                    )
                )
                await session.commit()
                payload = VisualSettingsUpdate(
                    enabled=True,
                    provider="remote_vlm",
                    endpoint="https://visual.example.com/locate",
                    model="fake-vlm",
                    api_key="super-secret-key",
                    max_candidates=3,
                    max_attempts=2,
                )
                loaded = await save_visual_settings(
                    session,
                    config,
                    1,
                    payload,
                )
                await session.commit()
                response = visual_settings_payload(
                    loaded,
                    api_key_configured=True,
                )
                credential = (
                    await session.execute(
                        select(Credential).where(
                            Credential.user_id == 1,
                            Credential.scope == "visual",
                        )
                    )
                ).scalar_one()
                assert bytes(credential.encrypted_value) != b"super-secret-key"
                return response
        finally:
            await engine.dispose()

    response = asyncio.run(scenario())

    assert response["provider"] == "remote_vlm"
    assert response["api_key_configured"] is True
    assert "super-secret-key" not in str(response)
