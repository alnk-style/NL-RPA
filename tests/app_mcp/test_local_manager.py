from __future__ import annotations

import asyncio
import io
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import models  # noqa: F401
from app.database import Base
from app.engine.app_mcp.local_manager import (
    LocalAppContext,
    LocalAppError,
    LocalAppManager,
    LocalAppState,
)
from app.engine.mcp_registry import MCPRegistry
from app.models.mcp import MCPServer
from app.models.user import User
from app.models.workspace import Workspace
from runtime_paths import DATA_DIR_ENV

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_SOURCE = PROJECT_ROOT / "mcp_apps" / "demo_app"


class FakeProcess:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.running = True
        self.terminated = False
        self.killed = False
        self.stdout = io.StringIO("server started\n")

    def poll(self) -> int | None:
        return None if self.running else 0

    def wait(self, timeout: float | None = None) -> int:
        self.running = False
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self.running = False

    def kill(self) -> None:
        self.killed = True
        self.running = False


class FakeProcessFactory:
    def __init__(self) -> None:
        self.processes: list[FakeProcess] = []
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> FakeProcess:
        process = FakeProcess(pid=4300 + len(self.processes))
        self.processes.append(process)
        self.calls.append({"args": args, "kwargs": kwargs})
        return process


def _app_root(tmp_path: Path) -> Path:
    root = tmp_path / "mcp_apps"
    root.mkdir()
    return root


def _copy_demo_app(
    apps_root: Path,
    *,
    app_id: str = "demo_app",
    default_port: int | None = None,
    startup_timeout: float | None = None,
) -> Path:
    target = apps_root / app_id
    shutil.copytree(DEMO_SOURCE, target)
    manifest_path = target / "manifest.yaml"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    payload["id"] = app_id
    payload["local"] = {
        "default_port": default_port,
        "auto_start": False,
        "startup_timeout_seconds": startup_timeout or 1,
        "health_interval_seconds": 60,
    }
    manifest_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return target


def _manager(
    tmp_path: Path,
    apps_root: Path,
    *,
    factory: FakeProcessFactory | None = None,
    health: Any = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    registry: MCPRegistry | None = None,
    generated_apps_root: Path | None = None,
) -> LocalAppManager:
    proc_factory = factory or FakeProcessFactory()

    async def healthy(url: str, timeout: float) -> bool:
        return True

    return LocalAppManager(
        project_root=PROJECT_ROOT,
        apps_root=apps_root,
        generated_apps_root=(
            generated_apps_root or tmp_path / "generated_apps"
        ),
        logs_root=tmp_path / "logs" / "app_mcp",
        script_path=PROJECT_ROOT / "scripts" / "run_app_mcp_server.py",
        process_factory=proc_factory,
        health_checker=health or healthy,
        platform_name="win32",
        session_factory=session_factory,
        registry=registry,
    )


def _context() -> LocalAppContext:
    return LocalAppContext(user_id=None, workspace_id=None)


def test_scan_accepts_valid_packages_and_reports_invalid_ones(
    tmp_path: Path,
) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    invalid = apps_root / "broken_app"
    invalid.mkdir()
    (invalid / "manifest.yaml").write_text(
        "schema_version: 1\nid: wrong_name\n",
        encoding="utf-8",
    )
    (apps_root / "__pycache__").mkdir()

    async def scenario() -> LocalAppManager:
        manager = _manager(tmp_path, apps_root)
        apps = await manager.scan()
        assert [item.app_id for item in apps] == ["demo_app"]
        assert apps[0].state == LocalAppState.DISCOVERED
        assert len(manager.scan_errors) == 1
        assert manager.scan_errors[0].app_id == "broken_app"
        return manager

    manager = asyncio.run(scenario())
    asyncio.run(manager.close_all())


def test_app_id_directory_mismatch_is_rejected(tmp_path: Path) -> None:
    apps_root = _app_root(tmp_path)
    target = apps_root / "folder_name"
    shutil.copytree(DEMO_SOURCE, target)

    async def scenario() -> None:
        manager = _manager(tmp_path, apps_root)
        apps = await manager.scan()
        assert apps == []
        assert manager.scan_errors
        assert "不一致" in manager.scan_errors[0].error
        await manager.close_all()

    asyncio.run(scenario())


def test_repeated_start_reuses_single_process(tmp_path: Path) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    factory = FakeProcessFactory()

    async def scenario() -> None:
        manager = _manager(tmp_path, apps_root, factory=factory)
        await manager.scan()
        first = await manager.start("demo_app", _context())
        second = await manager.start("demo_app", _context())
        assert first.pid == second.pid
        assert len(factory.processes) == 1
        assert first.state == LocalAppState.RUNNING
        await manager.close_all()

    asyncio.run(scenario())


def test_source_server_command_uses_python_script(
    tmp_path: Path,
) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    factory = FakeProcessFactory()

    async def scenario() -> None:
        manager = _manager(tmp_path, apps_root, factory=factory)
        await manager.scan()
        await manager.start("demo_app", _context())
        await manager.close_all()

    asyncio.run(scenario())

    command = factory.calls[0]["args"][0]
    assert command[:2] == [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_app_mcp_server.py"),
    ]
    assert "--internal-app-mcp-worker" not in command


def test_frozen_server_command_reuses_backend_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    factory = FakeProcessFactory()
    executable = tmp_path / "backend-server.exe"
    data_dir = tmp_path / "data"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setenv(DATA_DIR_ENV, str(data_dir))

    async def scenario() -> None:
        manager = _manager(tmp_path, apps_root, factory=factory)
        await manager.scan()
        await manager.start("demo_app", _context())
        await manager.close_all()

    asyncio.run(scenario())

    command = factory.calls[0]["args"][0]
    assert command[0] == str(executable)
    assert command[1] == "--internal-app-mcp-worker"
    assert "scripts\\run_app_mcp_server.py" not in "\n".join(command)
    assert "scripts/run_app_mcp_server.py" not in "\n".join(command)
    assert factory.calls[0]["kwargs"]["cwd"] == str(data_dir.resolve())


def test_missing_builtin_apps_reports_scan_error(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "mcp_apps"

    async def scenario() -> None:
        manager = _manager(tmp_path, missing_root)
        apps = await manager.scan()
        assert apps == []
        assert len(manager.scan_errors) == 1
        assert manager.scan_errors[0].app_id == "mcp_apps"
        assert manager.scan_errors[0].error
        await manager.close_all()

    asyncio.run(scenario())


def test_scan_includes_generated_apps_root(
    tmp_path: Path,
) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    shutil.copytree(
        PROJECT_ROOT / "mcp_apps" / "generic_desktop",
        generated_root / "generic_desktop",
    )

    async def scenario() -> None:
        manager = _manager(
            tmp_path,
            apps_root,
            generated_apps_root=generated_root,
        )
        apps = {app.app_id: app for app in await manager.scan()}
        assert set(apps) == {"demo_app", "generic_desktop"}
        await manager.close_all()

    asyncio.run(scenario())


def test_scan_reports_duplicate_app_id_across_roots(
    tmp_path: Path,
) -> None:
    apps_root = _app_root(tmp_path)
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    _copy_demo_app(apps_root)
    _copy_demo_app(generated_root)

    async def scenario() -> None:
        manager = _manager(
            tmp_path,
            apps_root,
            generated_apps_root=generated_root,
        )
        apps = await manager.scan()
        assert [app.app_id for app in apps] == ["demo_app"]
        assert manager.scan_errors
        assert manager.scan_errors[0].app_id == "demo_app"
        assert "duplicate" in manager.scan_errors[0].error.casefold()
        await manager.close_all()

    asyncio.run(scenario())


def test_port_conflict_uses_next_available_port(tmp_path: Path) -> None:
    reserved = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reserved.bind(("127.0.0.1", 0))
    reserved.listen(1)
    requested_port = reserved.getsockname()[1]
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root, default_port=requested_port)
    factory = FakeProcessFactory()

    async def scenario() -> int:
        manager = _manager(tmp_path, apps_root, factory=factory)
        await manager.scan()
        runtime = await manager.start("demo_app", _context())
        await manager.close_all()
        return runtime.port

    try:
        allocated = asyncio.run(scenario())
    finally:
        reserved.close()

    assert allocated != requested_port
    args = factory.calls[0]["args"][0]
    assert args[args.index("--port") + 1] == str(allocated)


def test_start_timeout_terminates_process(tmp_path: Path) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root, startup_timeout=0.05)
    factory = FakeProcessFactory()

    async def unhealthy(url: str, timeout: float) -> bool:
        return False

    async def scenario() -> None:
        manager = _manager(
            tmp_path,
            apps_root,
            factory=factory,
            health=unhealthy,
        )
        await manager.scan()
        with pytest.raises(LocalAppError) as captured:
            await manager.start("demo_app", _context())
        assert captured.value.code == "startup_timeout"
        assert factory.processes[0].terminated is True
        await manager.close_all()

    asyncio.run(scenario())


def test_stop_only_terminates_managed_process(tmp_path: Path) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    factory = FakeProcessFactory()
    unrelated = FakeProcess(pid=9999)

    async def scenario() -> None:
        manager = _manager(tmp_path, apps_root, factory=factory)
        await manager.scan()
        await manager.start("demo_app", _context())
        stopped = await manager.stop("demo_app")
        assert stopped is True
        assert factory.processes[0].terminated is True
        assert unrelated.terminated is False
        assert (await manager.status("demo_app")).state == LocalAppState.STOPPED
        await manager.close_all()

    asyncio.run(scenario())


def test_logs_are_limited_and_redacted(tmp_path: Path) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    factory = FakeProcessFactory()

    async def scenario() -> str:
        manager = _manager(tmp_path, apps_root, factory=factory)
        await manager.scan()
        await manager.start("demo_app", _context())
        await manager.stop("demo_app")
        log_path = tmp_path / "logs" / "app_mcp" / "demo_app.log"
        log_path.write_text(
            "\n".join(
                [
                    "one",
                    "two token=secret-value",
                    "x" * 2000,
                ]
            ),
            encoding="utf-8",
        )
        return await manager.logs("demo_app", lines=2)

    logs = asyncio.run(scenario())
    lines = logs.splitlines()
    assert len(lines) <= 2
    assert all(len(line) <= 500 for line in lines)
    assert "secret-value" not in logs


def test_invalid_app_id_is_rejected(tmp_path: Path) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)

    async def scenario() -> None:
        manager = _manager(tmp_path, apps_root)
        await manager.scan()
        with pytest.raises(LocalAppError) as captured:
            await manager.start("../demo_app", _context())
        assert captured.value.code == "invalid_app_id"
        await manager.close_all()

    asyncio.run(scenario())


def test_close_all_stops_every_managed_process(tmp_path: Path) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    factory = FakeProcessFactory()

    async def scenario() -> None:
        manager = _manager(tmp_path, apps_root, factory=factory)
        await manager.scan()
        await manager.start("demo_app", _context())
        await manager.close_all()
        assert factory.processes[0].terminated is True

    asyncio.run(scenario())


class FakeRegistryPool:
    def __init__(self) -> None:
        self.semaphore = asyncio.Semaphore(2)
        self.transport_name = "fake:http"
        self.tools = [
            {
                "name": "demo_status",
                "description": "status",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "demo_execute",
                "description": "execute",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                    },
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        ]

    def get(self, _config: Any) -> FakeRegistryPool:
        return self

    def drop(self, _key: Any) -> None:
        return None

    async def list_tools(self, *, timeout: float | None = None) -> list[dict[str, Any]]:
        return [dict(tool) for tool in self.tools]

    async def aclose(self) -> None:
        return None


def _registry_settings() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        MCP_CLIENT=True,
        MCP_CALL_TIMEOUT=5.0,
        MCP_MAX_CONCURRENCY=2,
        MCP_DEFAULT_RISK="mutating",
        MCP_STDIO_ENABLED=False,
        CREDENTIAL_ENCRYPTION_KEY=(
            "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
        ),
    )


async def _registry_harness(
    tmp_path: Path,
) -> tuple[
    async_sessionmaker[AsyncSession],
    MCPRegistry,
    FakeRegistryPool,
    Any,
]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'local.db').as_posix()}",
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        session.add(
            User(
                id=7,
                username="local-user",
                email="local@example.com",
                hashed_password="hash",
            )
        )
        await session.commit()
        workspace = Workspace(
            id=12,
            owner_id=7,
            name="Local",
            slug="default",
            is_default=True,
        )
        session.add(workspace)
        await session.commit()
    pool = FakeRegistryPool()
    registry = MCPRegistry(
        session_factory=session_factory,
        config=_registry_settings(),
        client_pool=pool,  # type: ignore[arg-type]
    )
    return session_factory, registry, pool, engine


def test_start_creates_server_and_stop_preserves_authorization(
    tmp_path: Path,
) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    factory = FakeProcessFactory()

    async def scenario() -> None:
        session_factory, registry, _pool, engine = await _registry_harness(
            tmp_path
        )
        manager = _manager(
            tmp_path,
            apps_root,
            factory=factory,
            session_factory=session_factory,
            registry=registry,
        )
        context = LocalAppContext(user_id=7, workspace_id=12)
        try:
            await manager.scan()
            runtime = await manager.start("demo_app", context)
            assert runtime.server_id is not None
            async with session_factory() as session:
                server = (
                    await session.execute(select(MCPServer))
                ).scalar_one()
                assert server.url == runtime.info.url
                assert server.config["local_app_id"] == "demo_app"

            authorized = await registry.authorize_tool(
                runtime.server_id,
                "demo_execute",
                authorized=True,
                confirmed=True,
                user_id=7,
                workspace_id=12,
            )
            assert authorized["success"] is True

            await manager.stop("demo_app", context)
            async with session_factory() as session:
                stopped = (
                    await session.execute(select(MCPServer))
                ).scalar_one()
                tool = next(
                    item
                    for item in stopped.tools
                    if item["name"] == "demo_execute"
                )
                assert stopped.enabled is False
                assert tool["authorized"] is True
                assert tool["confirmed"] is True
        finally:
            await manager.close_all()
            await registry.aclose()
            await engine.dispose()

    asyncio.run(scenario())


def test_toolset_fingerprint_change_requires_reauthorization(
    tmp_path: Path,
) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    factory = FakeProcessFactory()

    async def scenario() -> None:
        session_factory, registry, pool, engine = await _registry_harness(
            tmp_path
        )
        manager = _manager(
            tmp_path,
            apps_root,
            factory=factory,
            session_factory=session_factory,
            registry=registry,
        )
        context = LocalAppContext(user_id=7, workspace_id=12)
        try:
            await manager.scan()
            runtime = await manager.start("demo_app", context)
            assert runtime.server_id is not None
            await registry.authorize_tool(
                runtime.server_id,
                "demo_execute",
                authorized=True,
                confirmed=True,
                user_id=7,
                workspace_id=12,
            )

            pool.tools[1]["inputSchema"]["properties"]["note"] = {
                "type": "string"
            }
            await manager.restart("demo_app", context)
            async with session_factory() as session:
                server = (
                    await session.execute(select(MCPServer))
                ).scalar_one()
                tool = next(
                    item
                    for item in server.tools
                    if item["name"] == "demo_execute"
                )
                assert tool["authorized"] is False
                assert tool["confirmed"] is False
                assert tool["needs_reconfirmation"] is True
        finally:
            await manager.close_all()
            await registry.aclose()
            await engine.dispose()

    asyncio.run(scenario())


def test_running_process_cannot_be_controlled_by_another_user(
    tmp_path: Path,
) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    factory = FakeProcessFactory()

    async def scenario() -> None:
        manager = _manager(tmp_path, apps_root, factory=factory)
        try:
            await manager.scan()
            await manager.start(
                "demo_app",
                LocalAppContext(user_id=7, workspace_id=12),
            )
            visible = await manager.status(
                "demo_app",
                LocalAppContext(user_id=8, workspace_id=13),
            )
            assert visible.state == LocalAppState.RUNNING
            operations = (
                lambda context: manager.stop("demo_app", context),
                lambda context: manager.logs(
                    "demo_app",
                    context=context,
                ),
                lambda context: manager.restart("demo_app", context),
            )
            for operation in operations:
                with pytest.raises(LocalAppError) as captured:
                    await operation(
                        LocalAppContext(user_id=8, workspace_id=13),
                    )
                assert captured.value.code == "app_forbidden"
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_same_name_server_in_another_workspace_is_rejected(
    tmp_path: Path,
) -> None:
    apps_root = _app_root(tmp_path)
    _copy_demo_app(apps_root)
    factory = FakeProcessFactory()

    async def scenario() -> None:
        session_factory, registry, _pool, engine = await _registry_harness(
            tmp_path
        )
        async with session_factory() as session:
            session.add(
                Workspace(
                    id=13,
                    owner_id=7,
                    name="Other",
                    slug="other",
                )
            )
            session.add(
                MCPServer(
                    user_id=7,
                    workspace_id=13,
                    name="Demo App",
                    transport="http",
                    url="http://127.0.0.1:9999/mcp",
                )
            )
            await session.commit()
        manager = _manager(
            tmp_path,
            apps_root,
            factory=factory,
            session_factory=session_factory,
            registry=registry,
        )
        try:
            await manager.scan()
            with pytest.raises(LocalAppError) as captured:
                await manager.start(
                    "demo_app",
                    LocalAppContext(user_id=7, workspace_id=12),
                )
            assert captured.value.code == "server_conflict"
            assert factory.processes == []
        finally:
            await manager.close_all()
            await registry.aclose()
            await engine.dispose()

    asyncio.run(scenario())
