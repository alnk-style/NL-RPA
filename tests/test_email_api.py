from __future__ import annotations

import asyncio

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api import settings as settings_api
from app.config import settings
from app.database import Base, get_db
from app.models.app_setting import AppSetting
from app.models.credential import Credential
from app.models.user import User
from tests.support.fake_smtp import FakeSmtpServer


def _clear_smtp_defaults(monkeypatch) -> None:
    for name in (
        "SMTP_HOST",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "SMTP_FROM_EMAIL",
        "SMTP_DEFAULT_RECIPIENT",
    ):
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_START_TLS", True)


async def _database():
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
    return session_factory, engine


async def _seed(session: AsyncSession) -> User:
    user = User(
        id=1,
        username="email-owner",
        email="owner@example.com",
        hashed_password="hash",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _app(session: AsyncSession, user: User) -> FastAPI:
    app = FastAPI()
    app.include_router(settings_api.router)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[settings_api.get_current_user] = lambda: user
    return app


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    **kwargs,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path, **kwargs)


def test_email_settings_are_masked_and_password_is_encrypted(monkeypatch) -> None:
    _clear_smtp_defaults(monkeypatch)
    monkeypatch.setattr(
        settings,
        "CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )

    async def scenario():
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user = await _seed(session)
                app = _app(session, user)

                initial = await _request(
                    app,
                    "GET",
                    "/api/settings/email",
                )
                updated = await _request(
                    app,
                    "PUT",
                    "/api/settings/email",
                    json={
                        "host": "127.0.0.1",
                        "port": 2525,
                        "username": "smtp-user",
                        "password": "super-secret",
                        "from_email": "sender@example.com",
                        "start_tls": False,
                        "default_recipient": "fallback@example.com",
                        "credential_key": "smtp_default",
                    },
                )
                loaded = await _request(
                    app,
                    "GET",
                    "/api/settings/email",
                )

                setting = await session.scalar(
                    select(AppSetting).where(
                        AppSetting.user_id == user.id,
                        AppSetting.key == "email",
                    )
                )
                credential = await session.scalar(
                    select(Credential).where(
                        Credential.user_id == user.id,
                        Credential.scope == "email",
                        Credential.key == "smtp_default",
                    )
                )
                return (
                    initial,
                    updated,
                    loaded,
                    setting,
                    credential,
                )
        finally:
            await engine.dispose()

    initial, updated, loaded, setting, credential = asyncio.run(scenario())

    assert initial.status_code == 200
    assert initial.json()["configured"] is False
    assert initial.json()["password_configured"] is False

    for response in (updated, loaded):
        assert response.status_code == 200
        assert response.json()["password_configured"] is True
        assert "password" not in response.json()
        assert "super-secret" not in str(response.json())

    assert setting is not None
    assert "super-secret" not in str(setting.value)
    assert credential is not None
    assert credential.workflow_id is None
    assert credential.scope == "email"
    assert b"super-secret" not in bytes(credential.encrypted_value)


def test_email_settings_test_connection_and_test_send_use_saved_secret(
    monkeypatch,
) -> None:
    _clear_smtp_defaults(monkeypatch)
    monkeypatch.setattr(
        settings,
        "CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    with FakeSmtpServer() as server:
        async def scenario():
            session_factory, engine = await _database()
            try:
                async with session_factory() as session:
                    user = await _seed(session)
                    app = _app(session, user)
                    saved = await _request(
                        app,
                        "PUT",
                        "/api/settings/email",
                        json={
                            "host": server.host,
                            "port": server.port,
                            "password": "secret",
                            "from_email": "sender@example.com",
                            "start_tls": False,
                            "credential_key": "smtp_default",
                        },
                    )
                    connected = await _request(
                        app,
                        "POST",
                        "/api/settings/email/test",
                    )
                    sent = await _request(
                        app,
                        "POST",
                        "/api/settings/email/test-send",
                        json={"recipient": "recipient@example.com"},
                    )
                    return saved, connected, sent
            finally:
                await engine.dispose()

        saved, connected, sent = asyncio.run(scenario())

    assert saved.status_code == 200
    assert connected.status_code == 200
    assert connected.json()["status"] == "success"
    assert sent.status_code == 200
    assert sent.json()["status"] == "success", sent.json()
    assert sent.json()["provider_message_id"]
    assert server.messages


def test_email_settings_endpoints_never_report_unconfigured_smtp_as_success(
    monkeypatch,
) -> None:
    _clear_smtp_defaults(monkeypatch)
    async def scenario():
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user = await _seed(session)
                app = _app(session, user)
                connected = await _request(
                    app,
                    "POST",
                    "/api/settings/email/test",
                )
                sent = await _request(
                    app,
                    "POST",
                    "/api/settings/email/test-send",
                    json={"recipient": "recipient@example.com"},
                )
                return connected, sent
        finally:
            await engine.dispose()

    connected, sent = asyncio.run(scenario())

    for response in (connected, sent):
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert response.json()["error_category"] == "delivery_failed"
        assert response.json()["error"]
