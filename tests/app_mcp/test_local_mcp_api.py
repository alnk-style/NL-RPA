from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import models
from app.api.dependencies import get_current_user
from app.api.local_apps import get_local_app_manager, router
from app.api.workspace_deps import get_workspace_context
from app.database import Base, get_db
from app.engine.app_mcp.local_manager import LocalAppManager
from app.models.mcp import MCPServer
from app.security.credentials import decrypt_value
from app.security.workspaces import WorkspaceContext

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERIC_SOURCE = PROJECT_ROOT / "mcp_apps" / "generic_desktop"


class ApiDesktopConnector:
    def __init__(self) -> None:
        self.actions: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.closed = False

    async def capabilities(self) -> dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "data": {"actions": ["activate_window"]},
        }

    async def auth_status(self) -> dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "data": {"connected": True},
        }

    async def observe(
        self,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "data": {"windows": [], "elements": []},
        }

    async def act(
        self,
        action: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.actions.append((dict(action), dict(context or {})))
        return {
            "success": True,
            "error": None,
            "data": {"action": action.get("action")},
        }

    async def verify(
        self,
        assertion: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"success": True, "error": None, "data": {}}

    async def aclose(self) -> None:
        self.closed = True


class ApiDiscoveryRegistry:
    async def test_server(
        self,
        server_id: int,
        *,
        user_id: int | None = None,
        workspace_id: int | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        return {"success": True, "tool_count": 5}


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        CREDENTIAL_ENCRYPTION_KEY=(
            "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
        ),
        WORKSPACE_ISOLATION=True,
    )


def _copy_apps(root: Path) -> None:
    root.mkdir()
    shutil.copytree(GENERIC_SOURCE, root / "generic_desktop")
    second = root / "second_desktop"
    shutil.copytree(GENERIC_SOURCE, second)
    manifest_path = second / "manifest.yaml"
    payload = yaml.safe_load(
        manifest_path.read_text(encoding="utf-8")
    )
    payload["id"] = "second_desktop"
    payload["name"] = "Second Desktop"
    manifest_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _build_client(
    tmp_path: Path,
) -> tuple[
    TestClient,
    LocalAppManager,
    async_sessionmaker[AsyncSession],
    Any,
    ApiDesktopConnector,
]:
    apps_root = tmp_path / "mcp_apps"
    _copy_apps(apps_root)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}",
    )

    async def seed() -> async_sessionmaker[AsyncSession]:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with factory() as session:
            session.add_all(
                [
                    models.User(
                        id=7,
                        username="owner",
                        email="owner@example.com",
                        hashed_password="hash",
                    ),
                    models.User(
                        id=8,
                        username="other",
                        email="other@example.com",
                        hashed_password="hash",
                    ),
                    models.Workspace(
                        id=12,
                        owner_id=7,
                        name="Owner",
                        slug="default",
                        is_default=True,
                    ),
                    models.Workspace(
                        id=13,
                        owner_id=8,
                        name="Other",
                        slug="default",
                        is_default=True,
                    ),
                ]
            )
            await session.commit()
        return factory

    session_factory = asyncio.run(seed())
    connector = ApiDesktopConnector()
    manager = LocalAppManager(
        project_root=PROJECT_ROOT,
        apps_root=apps_root,
        logs_root=tmp_path / "logs",
        session_factory=session_factory,
        registry=ApiDiscoveryRegistry(),
        connectors={"desktop": connector, "windows": connector},
        settings=_settings(),
        platform_name="win32",
    )
    asyncio.run(manager.scan())

    app = FastAPI()
    app.include_router(router)
    app.state.local_app_manager = manager

    async def override_db():
        async with session_factory() as session:
            yield session

    async def override_user(request: Request) -> SimpleNamespace:
        username = request.headers.get("x-test-user", "owner")
        user_id = 7 if username == "owner" else 8
        async with session_factory() as session:
            user = await session.get(models.User, user_id)
            assert user is not None
            return user

    async def override_workspace(
        request: Request,
    ) -> WorkspaceContext:
        user = await override_user(request)
        workspace_id = int(request.headers.get("x-workspace-id", "12"))
        async with session_factory() as session:
            workspace = await session.get(models.Workspace, workspace_id)
        assert workspace is not None
        return WorkspaceContext(
            user=user,  # type: ignore[arg-type]
            workspace=workspace,
            isolation_enabled=True,
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_workspace_context] = override_workspace
    app.dependency_overrides[get_local_app_manager] = lambda: manager
    return (
        TestClient(app, base_url="http://127.0.0.1:8000"),
        manager,
        session_factory,
        engine,
        connector,
    )


async def _token_for(
    session_factory: async_sessionmaker[AsyncSession],
    server_id: int,
) -> str:
    async with session_factory() as session:
        server = await session.get(MCPServer, server_id)
        assert server is not None
        assert server.secret_encrypted is not None
        return decrypt_value(_settings(), server.secret_encrypted)


def _owner_headers() -> dict[str, str]:
    return {"X-Test-User": "owner", "X-Workspace-Id": "12"}


def _other_headers() -> dict[str, str]:
    return {"X-Test-User": "other", "X-Workspace-Id": "13"}


def test_in_process_endpoint_tokens_context_and_cross_app_isolation(
    tmp_path: Path,
) -> None:
    client, manager, session_factory, engine, connector = _build_client(
        tmp_path
    )
    try:
        first = client.post(
            "/api/local-apps/generic_desktop/start",
            headers=_owner_headers(),
        )
        second = client.post(
            "/api/local-apps/second_desktop/start",
            headers=_owner_headers(),
        )
        assert first.status_code == 200
        assert second.status_code == 200
        first_body = first.json()
        second_body = second.json()
        assert manager.in_process_runtime("generic_desktop") is not None
        assert manager.in_process_runtime("second_desktop") is not None
        assert manager.in_process_runtime(
            "generic_desktop"
        ) is not manager.in_process_runtime("second_desktop")
        assert first_body["app"]["url"] == (
            "http://127.0.0.1:8000"
            "/api/local-apps/generic_desktop/mcp"
        )
        first_token = asyncio.run(
            _token_for(session_factory, first_body["server_id"])
        )
        second_token = asyncio.run(
            _token_for(session_factory, second_body["server_id"])
        )
        assert first_token != second_token
        repeated = client.post(
            "/api/local-apps/generic_desktop/start",
            headers=_owner_headers(),
        )
        assert repeated.status_code == 200
        assert asyncio.run(
            _token_for(session_factory, first_body["server_id"])
        ) == first_token

        unauthorized = client.post(
            "/api/local-apps/generic_desktop/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
            },
        )
        assert unauthorized.status_code == 401
        wrong = client.post(
            "/api/local-apps/generic_desktop/mcp",
            headers={"Authorization": f"Bearer {second_token}"},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
            },
        )
        assert wrong.status_code == 401

        auth = {"Authorization": f"Bearer {first_token}"}
        invalid_shape = client.post(
            "/api/local-apps/generic_desktop/mcp",
            headers=auth,
            json=[],
        )
        assert invalid_shape.status_code == 422
        listed = client.post(
            "/api/local-apps/generic_desktop/mcp",
            headers=auth,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/list",
            },
        )
        assert listed.status_code == 200
        names = {
            tool["name"]
            for tool in listed.json()["result"]["tools"]
        }
        assert "desktop_probe" in names

        probe = client.post(
            "/api/local-apps/generic_desktop/mcp",
            headers=auth,
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "desktop_probe",
                    "arguments": {},
                },
            },
        )
        assert probe.status_code == 200
        assert probe.json()["result"]["isError"] is False

        missing_context = client.post(
            "/api/local-apps/generic_desktop/mcp",
            headers=auth,
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "desktop_activate",
                    "arguments": {"title": "Notepad"},
                },
            },
        )
        missing_result = missing_context.json()["result"]
        assert missing_result["isError"] is True
        assert (
            missing_result["structuredContent"]["error"]["code"]
            == "missing_context"
        )

        acted = client.post(
            "/api/local-apps/generic_desktop/mcp",
            headers={
                **auth,
                "X-NL-RPA-Workflow-Id": "21",
                "X-NL-RPA-Execution-Id": "22",
                "X-NL-RPA-User-Id": "999",
                "X-NL-RPA-Workspace-Id": "999",
            },
            json={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "desktop_activate",
                    "arguments": {"title": "Notepad"},
                },
            },
        )
        assert acted.json()["result"]["isError"] is False
        action, call_context = connector.actions[-1]
        assert action["action"] == "activate_window"
        assert call_context["workflow_id"] == 21
        assert call_context["user_id"] == 7
        assert call_context["workspace_id"] == 12
        assert call_context["dispatch_context"]["execution_log_id"] == 22

        info = client.get(
            "/api/local-apps/generic_desktop/mcp",
            headers=auth,
        )
        assert info.status_code == 200
        assert info.json()["app_id"] == "generic_desktop"
        assert info.json()["runtime"] == "in_process"
    finally:
        asyncio.run(manager.close_all())
        client.close()
        asyncio.run(engine.dispose())


def test_binding_api_rejects_owner_spoof_and_conflicts(
    tmp_path: Path,
) -> None:
    client, manager, _session_factory, engine, _connector = _build_client(
        tmp_path
    )
    try:
        started = client.post(
            "/api/local-apps/generic_desktop/start",
            headers=_owner_headers(),
        )
        assert started.status_code == 200

        spoof = client.patch(
            "/api/local-apps/generic_desktop/binding",
            headers=_owner_headers(),
            json={"auto_start": True, "owner_user_id": 8},
        )
        assert spoof.status_code == 422

        updated = client.patch(
            "/api/local-apps/generic_desktop/binding",
            headers=_owner_headers(),
            json={"auto_start": True},
        )
        assert updated.status_code == 200
        assert updated.json()["auto_start"] is True
        assert updated.json()["is_owner"] is True

        other_binding = client.get(
            "/api/local-apps/generic_desktop/binding",
            headers=_other_headers(),
        )
        assert other_binding.status_code == 200
        other_body = other_binding.json()
        assert other_body["bound"] is True
        assert other_body["is_owner"] is False
        assert other_body["can_manage"] is False
        assert "owner_user_id" not in other_body
        assert "workspace_id" not in other_body

        conflict = client.post(
            "/api/local-apps/generic_desktop/start",
            headers=_other_headers(),
        )
        assert conflict.status_code == 409
        assert (
            conflict.json()["error"]["code"]
            == "local_app_owner_conflict"
        )

        denied = client.delete(
            "/api/local-apps/generic_desktop/binding",
            headers=_other_headers(),
        )
        assert denied.status_code == 409

        removed = client.delete(
            "/api/local-apps/generic_desktop/binding",
            headers=_owner_headers(),
        )
        assert removed.status_code == 200
        assert removed.json()["bound"] is False
        assert manager.in_process_runtime("generic_desktop") is None
        assert _connector.closed is False
    finally:
        asyncio.run(manager.close_all())
        client.close()
        asyncio.run(engine.dispose())
