from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("JWT_SECRET_KEY", "test-encryption-key")

from app.api import credentials as credentials_api
from app.api.credentials import router as credentials_router
from app.config import settings
from app.database import Base, get_db
from app.models.credential import Credential
from app.models.user import User
from app.models.workflow import Workflow


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


async def _seed(session: AsyncSession) -> tuple[User, Workflow]:
    user = User(
        id=1,
        username="tester",
        email="tester@example.com",
        hashed_password="hash",
    )
    workflow = Workflow(
        user_id=1,
        name="Credential workflow",
        natural_language_input="登录示例站点",
    )
    session.add_all([user, workflow])
    await session.commit()
    await session.refresh(workflow)
    return user, workflow


def _app(session: AsyncSession, user: User) -> FastAPI:
    app = FastAPI()
    app.include_router(credentials_router)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[credentials_api.get_current_user] = (
        lambda: user
    )
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


def test_credentials_crud_never_returns_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )

    async def scenario() -> tuple[dict, dict, int, list[str]]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user, workflow = await _seed(session)
                app = _app(session, user)

                stored = await _request(
                    app,
                    "PUT",
                    (
                        f"/api/workflows/{workflow.id}"
                        "/credentials/username"
                    ),
                    json={"value": "automation-user"},
                )
                listed = await _request(
                    app,
                    "GET",
                    f"/api/workflows/{workflow.id}/credentials",
                )
                deleted = await _request(
                    app,
                    "DELETE",
                    (
                        f"/api/workflows/{workflow.id}"
                        "/credentials/username"
                    ),
                )
                after = await _request(
                    app,
                    "GET",
                    f"/api/workflows/{workflow.id}/credentials",
                )
                return (
                    stored.json(),
                    listed.json(),
                    deleted.status_code,
                    after.json()["keys"],
                )
        finally:
            await engine.dispose()

    stored, listed, delete_status, keys_after = asyncio.run(scenario())

    assert stored["status"] == "saved"
    assert stored["key"] == "username"
    assert "automation-user" not in str(stored)
    assert listed["keys"] == ["username"]
    assert "automation-user" not in str(listed)
    assert delete_status == 200
    assert keys_after == []


def test_credentials_are_owned_by_workflow_user() -> None:
    async def scenario() -> tuple[int, int]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user, workflow = await _seed(session)
                other_user = User(
                    id=2,
                    username="other",
                    email="other@example.com",
                    hashed_password="hash",
                )
                session.add(other_user)
                await session.commit()

                app = _app(session, other_user)
                read = await _request(
                    app,
                    "GET",
                    f"/api/workflows/{workflow.id}/credentials",
                )
                write = await _request(
                    app,
                    "PUT",
                    (
                        f"/api/workflows/{workflow.id}"
                        "/credentials/token"
                    ),
                    json={"value": "secret"},
                )
                return read.status_code, write.status_code
        finally:
            await engine.dispose()

    read_status, write_status = asyncio.run(scenario())

    assert read_status == 404
    assert write_status == 404


def test_credentials_reject_invalid_key_and_missing_delete() -> None:
    async def scenario() -> tuple[int, int]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user, workflow = await _seed(session)
                app = _app(session, user)
                invalid = await _request(
                    app,
                    "PUT",
                    (
                        f"/api/workflows/{workflow.id}"
                        "/credentials/bad%20key"
                    ),
                    json={"value": "secret"},
                )
                missing = await _request(
                    app,
                    "DELETE",
                    (
                        f"/api/workflows/{workflow.id}"
                        "/credentials/missing"
                    ),
                )
                return invalid.status_code, missing.status_code
        finally:
            await engine.dispose()

    invalid_status, missing_status = asyncio.run(scenario())

    assert invalid_status == 422
    assert missing_status == 404


def test_credential_rows_store_ciphertext(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )

    async def scenario() -> bytes:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user, workflow = await _seed(session)
                app = _app(session, user)
                await _request(
                    app,
                    "PUT",
                    (
                        f"/api/workflows/{workflow.id}"
                        "/credentials/password"
                    ),
                    json={"value": "super-secret"},
                )
                row = await session.get(Credential, 1)
                assert row is not None
                return bytes(row.encrypted_value)
        finally:
            await engine.dispose()

    encrypted = asyncio.run(scenario())

    assert b"super-secret" not in encrypted
