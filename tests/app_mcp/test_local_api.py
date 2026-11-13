from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.api.local_apps import get_local_app_manager, router
from app.api.workspace_deps import get_workspace_context
from app.engine.app_mcp.local_manager import (
    LocalAppContext,
    LocalAppError,
    LocalAppInfo,
    LocalAppRuntime,
    LocalAppScanError,
    LocalAppScanResult,
    LocalAppState,
)
from app.models.user import User
from app.security.workspaces import WorkspaceContext


def _info(
    app_id: str = "demo_app",
    state: LocalAppState = LocalAppState.RUNNING,
) -> LocalAppInfo:
    return LocalAppInfo(
        app_id=app_id,
        name="Demo App",
        description="Demo",
        version="0.1.0",
        platform="cross_platform",
        tools_count=3,
        state=state,
        url="http://127.0.0.1:8767/mcp",
        pid=4321 if state == LocalAppState.RUNNING else None,
        port=8767 if state == LocalAppState.RUNNING else None,
        auto_start=False,
    )


class FakeLocalAppManager:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.error: LocalAppError | None = None
        self.info = _info()
        self.last_scan = LocalAppScanResult(apps=[self.info])

    def list_apps(self) -> list[LocalAppInfo]:
        return list(self.last_scan.apps)

    @property
    def scan_errors(self) -> list[LocalAppScanError]:
        return list(self.last_scan.errors)

    async def scan(self) -> list[LocalAppInfo]:
        self.calls.append(("scan",))
        return self.last_scan.apps

    async def start(
        self,
        app_id: str,
        context: LocalAppContext | None = None,
    ) -> LocalAppRuntime:
        self.calls.append(("start", app_id, context))
        if self.error is not None:
            raise self.error
        return LocalAppRuntime(
            info=self.info,
            server_id=9,
            discovered_tool_count=3,
        )

    async def stop(
        self,
        app_id: str,
        context: LocalAppContext | None = None,
    ) -> bool:
        self.calls.append(("stop", app_id, context))
        if self.error is not None:
            raise self.error
        return True

    async def restart(
        self,
        app_id: str,
        context: LocalAppContext | None = None,
    ) -> LocalAppRuntime:
        self.calls.append(("restart", app_id, context))
        if self.error is not None:
            raise self.error
        return LocalAppRuntime(
            info=self.info,
            server_id=9,
            discovered_tool_count=3,
        )

    async def status(
        self,
        app_id: str,
        context: LocalAppContext | None = None,
    ) -> LocalAppInfo:
        self.calls.append(("status", app_id, context))
        if self.error is not None:
            raise self.error
        return self.info

    async def logs(
        self,
        app_id: str,
        lines: int = 200,
        context: LocalAppContext | None = None,
    ) -> str:
        self.calls.append(("logs", app_id, lines, context))
        if self.error is not None:
            raise self.error
        return "line one\nline two"


@pytest.fixture()
def local_api() -> tuple[TestClient, FakeLocalAppManager]:
    manager = FakeLocalAppManager()
    user = User(
        id=7,
        username="tester",
        email="tester@example.com",
        hashed_password="hash",
    )
    workspace = SimpleNamespace(id=12)
    app = FastAPI()
    app.include_router(router)
    app.state.local_app_manager = manager

    async def override_user() -> User:
        return user

    async def override_workspace() -> WorkspaceContext:
        return WorkspaceContext(
            user=user,
            workspace=workspace,  # type: ignore[arg-type]
            isolation_enabled=True,
        )

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_workspace_context] = override_workspace
    app.dependency_overrides[get_local_app_manager] = lambda: manager
    return TestClient(app), manager


def test_local_api_requires_authentication() -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.local_app_manager = FakeLocalAppManager()
    response = TestClient(app).get("/api/local-apps")
    assert response.status_code == 401


def test_list_and_scan_return_runtime_and_scan_diagnostics(
    local_api,
) -> None:
    client, manager = local_api
    listed = client.get("/api/local-apps")
    assert listed.status_code == 200
    assert listed.json()["apps"][0]["app_id"] == "demo_app"

    manager.last_scan = LocalAppScanResult(
        apps=[manager.info],
        added=["demo_app"],
        removed=["old_app"],
        errors=[LocalAppScanError("broken_app", "Manifest invalid")],
    )
    scanned = client.post("/api/local-apps/scan")
    assert scanned.status_code == 200
    payload = scanned.json()
    assert payload["added"] == ["demo_app"]
    assert payload["removed"] == ["old_app"]
    assert payload["errors"][0]["app_id"] == "broken_app"


@pytest.mark.parametrize("operation", ["start", "stop", "restart"])
def test_lifecycle_uses_trusted_user_and_workspace_context(
    local_api,
    operation: str,
) -> None:
    client, manager = local_api
    response = client.post(f"/api/local-apps/demo_app/{operation}")
    assert response.status_code == 200

    call = next(item for item in manager.calls if item[0] == operation)
    context = call[-1]
    assert context.user_id == 7
    assert context.workspace_id == 12


@pytest.mark.parametrize("operation", ["start", "stop", "restart"])
def test_lifecycle_rejects_command_or_path_fields(
    local_api,
    operation: str,
) -> None:
    client, manager = local_api
    response = client.post(
        f"/api/local-apps/demo_app/{operation}",
        json={
            "command": "powershell",
            "args": ["-Command", "whoami"],
            "cwd": "C:\\",
            "host": "0.0.0.0",
        },
    )
    assert response.status_code == 422
    assert manager.calls == []


def test_logs_are_bounded_and_include_context(local_api) -> None:
    client, manager = local_api
    response = client.get(
        "/api/local-apps/demo_app/logs",
        params={"lines": 120},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["lines"] == 120
    assert "line one" in payload["logs"]
    call = next(item for item in manager.calls if item[0] == "logs")
    assert call[1] == "demo_app"
    assert call[2] == 120
    assert call[3].workspace_id == 12


def test_manager_errors_map_to_stable_api_codes(local_api) -> None:
    client, manager = local_api
    manager.error = LocalAppError(
        "unsupported_backend",
        "Desktop bridge is unavailable.",
    )
    response = client.post("/api/local-apps/generic_desktop/start")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "unsupported_backend"


def test_unknown_app_maps_to_not_found(local_api) -> None:
    client, manager = local_api
    manager.error = LocalAppError(
        "app_not_found",
        "Application not found.",
    )
    response = client.get("/api/local-apps/missing_app")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "app_not_found"


def test_adapter_manager_must_be_available() -> None:
    user = User(
        id=1,
        username="tester",
        email="tester@example.com",
        hashed_password="hash",
    )
    app = FastAPI()
    app.include_router(router)

    async def override_user() -> User:
        return user

    async def override_workspace() -> WorkspaceContext:
        return WorkspaceContext(
            user=user,
            workspace=None,
            isolation_enabled=False,
        )

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_workspace_context] = override_workspace
    response = TestClient(app).get("/api/local-apps")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "manager_unavailable"
