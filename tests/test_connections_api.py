from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api import connections as connections_api
from app.api.dependencies import get_current_user
from app.database import Base, get_db
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
    async with factory() as session:
        session.add(
            User(
                id=1,
                username="connections",
                email="connections@example.com",
                hashed_password="hash",
            )
        )
        await session.commit()
    return factory, engine


def _app(session: AsyncSession) -> FastAPI:
    app = FastAPI()
    app.include_router(connections_api.router)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User(id=1)
    return app


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    payload: dict | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=payload)


def test_browser_profiles_are_user_scoped_and_do_not_expose_secrets() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            async with factory() as session:
                app = _app(session)
                created = await _request(
                    app,
                    "POST",
                    "/api/browser-profiles",
                    {
                        "name": "Automation",
                        "user_data_dir": "C:/rpa-profile",
                        "profile_directory": "Default",
                    },
                )
                listed = await _request(
                    app,
                    "GET",
                    "/api/browser-profiles",
                )
                return created, listed
        finally:
            await engine.dispose()

    created, listed = asyncio.run(scenario())

    assert created.status_code == 201
    assert created.json()["login_state"] == "unknown"
    assert "password" not in created.json()
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["Automation"]


def test_export_directories_require_existing_absolute_paths(tmp_path) -> None:
    directory = tmp_path / "exports"
    directory.mkdir()

    async def scenario():
        factory, engine = await _database()
        try:
            async with factory() as session:
                app = _app(session)
                relative = await _request(
                    app,
                    "PUT",
                    "/api/settings/export-directories",
                    {"directories": ["relative/path"]},
                )
                saved = await _request(
                    app,
                    "PUT",
                    "/api/settings/export-directories",
                    {"directories": [str(directory)]},
                )
                loaded = await _request(
                    app,
                    "GET",
                    "/api/settings/export-directories",
                )
                return relative, saved, loaded
        finally:
            await engine.dispose()

    relative, saved, loaded = asyncio.run(scenario())

    assert relative.status_code == 422
    assert saved.status_code == 200
    assert saved.json()["directories"] == [str(directory.resolve())]
    assert loaded.json()["directories"] == [str(directory.resolve())]
