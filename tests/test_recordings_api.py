from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api import recordings as recordings_api
from app.api.dependencies import get_current_user
from app.api.recordings import router as recordings_router
from app.config import settings
from app.database import Base, get_db
from app.models.user import User
from app.models.workflow import Workflow


async def _seed_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            User(
                id=1,
                username="recorder",
                email="recorder@example.com",
                hashed_password="hash",
            )
        )
        session.add(
            User(
                id=2,
                username="other",
                email="other@example.com",
                hashed_password="hash",
            )
        )
        await session.commit()


async def _prepare() -> tuple[async_sessionmaker[AsyncSession], Any]:
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
    await _seed_user(session_factory)
    return session_factory, engine


def _app(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: int = 1,
    dispatcher: Any | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(recordings_router)
    app.state.desktop_dispatcher = dispatcher

    async def override_db():
        async with session_factory() as session:
            yield session

    async def override_user() -> User:
        async with session_factory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return app


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=json_body)


@pytest.fixture(autouse=True)
def _clean_sessions():
    recordings_api.recording_sessions.clear()
    yield
    recordings_api.recording_sessions.clear()


def test_recording_api_requires_switch(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RECORDER_MODE", False)

    async def scenario() -> httpx.Response:
        session_factory, engine = await _prepare()
        try:
            app = _app(session_factory)
            return await _request(
                app,
                "POST",
                "/api/recordings",
                {"mode": "web"},
            )
        finally:
            await engine.dispose()

    response = asyncio.run(scenario())

    assert response.status_code == 403
    assert "RECORDER_MODE" in response.json()["detail"]


def test_recording_api_compiles_web_draft_and_saves_workflow(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "RECORDER_MODE", True)
    monkeypatch.setattr(
        recordings_api,
        "_recording_dir",
        lambda: tmp_path / "recordings",
    )

    async def scenario() -> tuple[httpx.Response, httpx.Response, Path]:
        session_factory, engine = await _prepare()
        try:
            app = _app(session_factory)
            started = await _request(
                app,
                "POST",
                "/api/recordings",
                {
                    "mode": "web",
                    "name": "登录流程",
                    "goal": "登录示例站点",
                    "start_url": "https://example.com/login",
                },
            )
            assert started.status_code == 201
            recording_id = started.json()["id"]

            appended = await _request(
                app,
                "POST",
                f"/api/recordings/{recording_id}/events",
                {
                    "events": [
                        {
                            "kind": "click",
                            "locators": [
                                {
                                    "strategy": "role",
                                    "value": "button",
                                    "name": "登录",
                                }
                            ],
                        },
                        {
                            "kind": "fill",
                            "locators": [
                                {
                                    "strategy": "label",
                                    "value": "用户名",
                                }
                            ],
                            "value": "alice",
                        },
                    ]
                },
            )
            assert appended.status_code == 200
            assert appended.json()["event_count"] == 3

            preview = await _request(
                app,
                "GET",
                f"/api/recordings/{recording_id}",
            )
            assert preview.status_code == 200
            draft = preview.json()["draft"]
            assert draft is not None
            assert [
                step["action"] for step in draft["steps"]
            ] == ["goto", "click", "fill"]

            finished = await _request(
                app,
                "POST",
                f"/api/recordings/{recording_id}/finish",
                {"create_workflow": True},
            )
            assert finished.status_code == 200
            payload = finished.json()
            assert payload["status"] == "finished"
            assert payload["workflow_id"] is not None
            recording_path = Path(payload["recording_path"])

            async with session_factory() as session:
                workflow = await session.get(
                    Workflow,
                    payload["workflow_id"],
                )
                assert workflow is not None
                assert workflow.status == "draft"
                assert workflow.executor_type == "web"
                assert workflow.execution_mode == "structured"
                assert workflow.active_version_id is None
                assert workflow.structured_plan["steps"][0]["action"] == (
                    "goto"
                )
                assert (
                    workflow.structured_plan["execution_mode"]
                    == "structured"
                )
            return finished, preview, recording_path
        finally:
            await engine.dispose()

    finished, _preview, recording_path = asyncio.run(scenario())

    assert recording_path.is_file()
    stored = json.loads(recording_path.read_text(encoding="utf-8"))
    assert stored["draft"]["executor_type"] == "web"
    assert len(stored["events"]) == 3
    assert finished.json()["draft"]["warnings"] == []


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def pick(
        self,
        request: dict[str, Any],
        *,
        agent_id: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "request": request,
                "agent_id": agent_id,
                "timeout": timeout,
            }
        )
        return {
            "success": True,
            "element": {
                "name": "保存",
                "automation_id": "saveBtn",
                "control_type": "Button",
            },
            "window": {"title": "记事本", "pid": 100},
            "point": {"x": 30, "y": 40},
            "error": None,
        }


def test_recording_api_pick_builds_desktop_draft(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "RECORDER_MODE", True)
    monkeypatch.setattr(
        recordings_api,
        "_recording_dir",
        lambda: tmp_path / "recordings",
    )

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        session_factory, engine = await _prepare()
        try:
            dispatcher = _FakeDispatcher()
            app = _app(session_factory, dispatcher=dispatcher)
            started = await _request(
                app,
                "POST",
                "/api/recordings",
                {"mode": "windows", "name": "WPS 操作"},
            )
            recording_id = started.json()["id"]

            picked = await _request(
                app,
                "POST",
                f"/api/recordings/{recording_id}/pick",
                {},
            )
            assert picked.status_code == 200
            body = picked.json()
            assert body["picked"]["event"]["kind"] == "desktop_click"
            assert body["picked"]["event"]["params"] == {
                "automation_id": "saveBtn",
                "name": "保存",
                "control_type": "Button",
            }
            assert [
                step["action"] for step in body["draft"]["steps"]
            ] == ["activate_window", "click_element"]
            assert body["draft"]["plan"]["required_capabilities"] == [
                "uia",
                "window",
            ]

            finished = await _request(
                app,
                "POST",
                f"/api/recordings/{recording_id}/finish",
                {"create_workflow": True},
            )
            assert finished.status_code == 200
            assert dispatcher.calls == [
                {"request": {}, "agent_id": None, "timeout": None}
            ]
            return picked, finished
        finally:
            await engine.dispose()

    picked, finished = asyncio.run(scenario())

    assert picked.status_code == 200
    assert finished.json()["status"] == "finished"
    assert finished.json()["draft"]["plan"]["executor_type"] == "windows"


def test_recording_api_uses_dispatcher_from_app_state(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "RECORDER_MODE", True)

    async def scenario() -> httpx.Response:
        session_factory, engine = await _prepare()
        try:
            app = _app(session_factory, dispatcher=None)
            started = await _request(
                app,
                "POST",
                "/api/recordings",
                {"mode": "windows"},
            )
            recording_id = started.json()["id"]
            return await _request(
                app,
                "POST",
                f"/api/recordings/{recording_id}/pick",
                {},
            )
        finally:
            await engine.dispose()

    response = asyncio.run(scenario())

    assert response.status_code == 503


def test_recording_api_rejects_invalid_draft_and_hides_other_users(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "RECORDER_MODE", True)
    monkeypatch.setattr(
        recordings_api,
        "_recording_dir",
        lambda: tmp_path / "recordings",
    )

    async def scenario() -> tuple[
        httpx.Response,
        httpx.Response,
        httpx.Response,
        httpx.Response,
    ]:
        session_factory, engine = await _prepare()
        try:
            app = _app(session_factory)
            started = await _request(
                app,
                "POST",
                "/api/recordings",
                {"mode": "web"},
            )
            recording_id = started.json()["id"]
            await _request(
                app,
                "POST",
                f"/api/recordings/{recording_id}/events",
                {"events": [{"kind": "click", "locators": []}]},
            )

            invalid = await _request(
                app,
                "POST",
                f"/api/recordings/{recording_id}/finish",
                {"create_workflow": False},
            )

            other_app = _app(session_factory, user_id=2)
            hidden = await _request(
                other_app,
                "GET",
                f"/api/recordings/{recording_id}",
            )

            discarded = await _request(
                app,
                "DELETE",
                f"/api/recordings/{recording_id}",
            )
            missing = await _request(
                app,
                "GET",
                f"/api/recordings/{recording_id}",
            )
            return invalid, discarded, hidden, missing
        finally:
            await engine.dispose()

    invalid, discarded, hidden, missing = asyncio.run(scenario())

    assert invalid.status_code == 422
    assert "没有生成任何可用步骤" in invalid.json()["detail"]
    assert discarded.status_code == 200
    assert discarded.json()["status"] == "deleted"
    assert hidden.status_code == 404
    assert missing.status_code == 404


def test_recording_api_rejects_events_after_finish(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "RECORDER_MODE", True)

    async def scenario() -> httpx.Response:
        session_factory, engine = await _prepare()
        try:
            app = _app(session_factory)
            started = await _request(
                app,
                "POST",
                "/api/recordings",
                {"mode": "web"},
            )
            recording_id = started.json()["id"]
            await _request(
                app,
                "POST",
                f"/api/recordings/{recording_id}/events",
                {
                    "events": [
                        {"kind": "navigate", "url": "https://example.com"}
                    ]
                },
            )
            await _request(
                app,
                "POST",
                f"/api/recordings/{recording_id}/finish",
                {"create_workflow": False},
            )
            return await _request(
                app,
                "POST",
                f"/api/recordings/{recording_id}/events",
                {
                    "events": [
                        {"kind": "navigate", "url": "https://example.com"}
                    ]
                },
            )
        finally:
            await engine.dispose()

    response = asyncio.run(scenario())

    assert response.status_code == 409
