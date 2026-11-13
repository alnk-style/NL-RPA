"""Local-only lifecycle manager for built-in App MCP packages."""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
import secrets
import socket
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine.app_mcp.manifest import (
    AppManifest,
    ManifestError,
    load_manifest,
)
from app.engine.app_mcp.runtime import load_runtime
from app.models.mcp import MCPServer
from app.security.credentials import decrypt_value, encrypt_value
from runtime_paths import (
    app_mcp_logs_dir,
    builtin_apps_dir,
    generated_apps_dir,
    resource_root,
    stable_data_dir,
)

logger = logging.getLogger(__name__)

_APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_LOG_SECRET = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization)"
    r"\s*[:=]\s*[^\s,;]+"
)
_LOG_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_LOG_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_STARTUP_POLL_SECONDS = 0.15
_MAX_LOG_LINE = 500
_MAX_LOG_LINES = 500
_UNSUPPORTED_PLATFORM_MESSAGE = (
    "当前平台尚未验证；目前仅支持 Windows 本地模式。"
)


class LocalAppState(str, Enum):
    DISCOVERED = "discovered"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class LocalAppContext:
    user_id: int | None = None
    workspace_id: int | None = None
    public_base_url: str | None = None


@dataclass
class LocalAppInfo:
    app_id: str
    name: str
    description: str
    version: str
    platform: str
    tools_count: int
    state: LocalAppState
    url: str | None = None
    pid: int | None = None
    port: int | None = None
    started_at: str | None = None
    last_health_at: str | None = None
    last_error: str | None = None
    auto_start: bool = False
    server_id: int | None = None
    discovered_tool_count: int | None = None
    unsupported_reason: str | None = None
    host_mode: str = "subprocess"
    transport_kind: str = "process"
    generated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "platform": self.platform,
            "tools_count": self.tools_count,
            "state": self.state.value,
            "url": self.url,
            "pid": self.pid,
            "port": self.port,
            "started_at": self.started_at,
            "last_health_at": self.last_health_at,
            "last_error": self.last_error,
            "auto_start": self.auto_start,
            "server_id": self.server_id,
            "discovered_tool_count": self.discovered_tool_count,
            "unsupported_reason": self.unsupported_reason,
            "host_mode": self.host_mode,
            "transport_kind": self.transport_kind,
            "generated": self.generated,
        }


@dataclass(frozen=True)
class LocalAppRuntime:
    info: LocalAppInfo
    server_id: int | None = None
    discovered_tool_count: int | None = None

    @property
    def pid(self) -> int | None:
        return self.info.pid

    @property
    def port(self) -> int | None:
        return self.info.port

    @property
    def state(self) -> LocalAppState:
        return self.info.state


@dataclass(frozen=True)
class LocalAppScanError:
    app_id: str
    error: str

    def to_dict(self) -> dict[str, str]:
        return {"app_id": self.app_id, "error": self.error}


@dataclass
class LocalAppScanResult:
    apps: list[LocalAppInfo] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    errors: list[LocalAppScanError] = field(default_factory=list)


@dataclass(frozen=True)
class InProcessMCPTarget:
    app_id: str
    runtime: Any
    server_id: int
    user_id: int
    workspace_id: int | None


class LocalAppError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass
class _ManagedProcess:
    process: Any
    pid: int
    runtime_pid: int | None
    log_path: Path
    log_file: Any
    log_thread: threading.Thread | None = None


@dataclass
class _AppRecord:
    app_dir: Path
    manifest: AppManifest
    state: LocalAppState
    managed: _ManagedProcess | None = None
    port: int | None = None
    url: str | None = None
    started_at: str | None = None
    last_health_at: str | None = None
    last_error: str | None = None
    server_id: int | None = None
    discovered_tool_count: int | None = None
    context: LocalAppContext | None = None
    health_task: asyncio.Task[None] | None = None
    in_process_runtime: Any | None = None
    binding_auto_start: bool = False
    generated: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def app_id(self) -> str:
        return self.manifest.id

    def info(self) -> LocalAppInfo:
        pid = self.managed.pid if self.managed else None
        return LocalAppInfo(
            app_id=self.manifest.id,
            name=self.manifest.name,
            description=self.manifest.description,
            version=self.manifest.version,
            platform=self.manifest.platform,
            tools_count=len(self.manifest.tools),
            state=self.state,
            url=self.url,
            pid=pid,
            port=self.port,
            started_at=self.started_at,
            last_health_at=self.last_health_at,
            last_error=self.last_error,
            auto_start=self.binding_auto_start,
            server_id=self.server_id,
            discovered_tool_count=self.discovered_tool_count,
            unsupported_reason=(
                self.last_error
                if self.state == LocalAppState.UNSUPPORTED
                else None
            ),
            host_mode=self.manifest.local.host_mode,
            transport_kind=(
                "in_process"
                if self.manifest.local.host_mode == "in_process"
                else "process"
            ),
            generated=self.generated,
        )


HealthChecker = Callable[[str, float], Awaitable[bool]]
ProcessFactory = Callable[..., Any]


class LocalAppManager:
    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        apps_root: str | Path | None = None,
        generated_apps_root: str | Path | None = None,
        logs_root: str | Path | None = None,
        script_path: str | Path | None = None,
        process_factory: ProcessFactory | None = None,
        health_checker: HealthChecker | None = None,
        platform_name: str | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        registry: Any | None = None,
        connectors: Mapping[str, Any] | None = None,
        settings: Any | None = None,
        workspace_isolation: bool | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root)
            if project_root is not None
            else resource_root()
        ).resolve()
        self.apps_root = (
            Path(apps_root)
            if apps_root is not None
            else builtin_apps_dir()
        ).resolve()
        self.generated_apps_root = (
            Path(generated_apps_root)
            if generated_apps_root is not None
            else generated_apps_dir(create=False)
        ).resolve()
        self.apps_roots = tuple(
            dict.fromkeys((self.apps_root, self.generated_apps_root))
        )
        self.logs_root = (
            Path(logs_root)
            if logs_root is not None
            else app_mcp_logs_dir()
        ).resolve()
        self.script_path = (
            Path(script_path)
            if script_path is not None
            else self.project_root / "scripts" / "run_app_mcp_server.py"
        ).resolve()
        self.session_factory = session_factory
        self.registry = registry
        self.connectors = dict(connectors or {})
        self.settings = settings
        self.workspace_isolation = bool(workspace_isolation)
        if workspace_isolation is None:
            self.workspace_isolation = bool(
                getattr(settings, "WORKSPACE_ISOLATION", False)
            )
        self.platform_name = platform_name or sys.platform
        self._process_factory = process_factory or subprocess.Popen
        self._health_checker = health_checker or _default_health_check
        self._records: dict[str, _AppRecord] = {}
        self._previous_ids: set[str] = set()
        self._last_scan = LocalAppScanResult()
        self._closed = False

    @property
    def scan_errors(self) -> list[LocalAppScanError]:
        return list(self._last_scan.errors)

    @property
    def last_scan(self) -> LocalAppScanResult:
        return self._last_scan

    def list_apps(self) -> list[LocalAppInfo]:
        return [
            self._records[app_id].info()
            for app_id in sorted(self._records)
        ]

    async def scan(self) -> list[LocalAppInfo]:
        self._ensure_open()
        discovered: dict[str, _AppRecord] = {}
        errors: list[LocalAppScanError] = []
        if not self.apps_root.is_dir():
            errors.append(
                LocalAppScanError(
                    app_id="mcp_apps",
                    error=(
                        "内置应用资源目录缺失，请检查桌面安装包。"
                    ),
                )
            )
            self._last_scan = LocalAppScanResult(errors=errors)
            return []

        for directory in sorted(self.apps_root.iterdir()):
            if not directory.is_dir() or directory.name.startswith((".", "_")):
                continue
            app_id = directory.name
            if not _APP_ID_RE.fullmatch(app_id):
                errors.append(
                    LocalAppScanError(
                        app_id=app_id,
                        error="应用 ID 不合法。",
                    )
                )
                continue
            try:
                manifest = load_manifest(directory / "manifest.yaml")
                await self._validate_adapter(directory, manifest)
            except (ManifestError, ValueError, OSError) as exc:
                errors.append(
                    LocalAppScanError(
                        app_id=app_id,
                        error=_safe_error(exc),
                    )
                )
                continue
            except Exception as exc:
                logger.warning(
                    "local app package validation failed: %s",
                    app_id,
                    exc_info=True,
                )
                errors.append(
                    LocalAppScanError(
                        app_id=app_id,
                        error=_safe_error(exc),
                    )
                )
                continue
            existing = self._records.get(app_id)
            state = (
                existing.state
                if existing is not None
                else LocalAppState.DISCOVERED
            )
            if (
                manifest.local.unsupported_reason
                or not self._platform_supported(manifest.platform)
            ):
                state = LocalAppState.UNSUPPORTED
                if (
                    existing is not None
                    and (
                        existing.managed is not None
                        or existing.in_process_runtime is not None
                    )
                ):
                    await self.stop(app_id)
            record = existing or _AppRecord(
                app_dir=directory,
                manifest=manifest,
                state=state,
            )
            record.app_dir = directory
            record.manifest = manifest
            record.state = state
            record.generated = False
            if state == LocalAppState.UNSUPPORTED:
                record.last_error = (
                    manifest.local.unsupported_reason
                    or _UNSUPPORTED_PLATFORM_MESSAGE
                )
            elif existing is None:
                record.last_error = None
            discovered[app_id] = record

        for app_id, record in (
            await self._scan_generated_root(errors)
        ).items():
            if app_id in discovered:
                errors.append(
                    LocalAppScanError(
                        app_id=app_id,
                        error=(
                            "duplicate app ID across application roots"
                        ),
                    )
                )
                continue
            discovered[app_id] = record

        removed = sorted(set(self._records) - set(discovered))
        for app_id in removed:
            record = self._records[app_id]
            if (
                record.managed is not None
                or record.in_process_runtime is not None
            ):
                await self.stop(app_id)
        self._records = discovered
        await self._refresh_binding_states()
        added = sorted(set(discovered) - self._previous_ids)
        self._previous_ids = set(discovered)
        apps = self.list_apps()
        self._last_scan = LocalAppScanResult(
            apps=apps,
            added=added,
            removed=removed,
            errors=errors,
        )
        return apps

    async def _scan_generated_root(
        self,
        errors: list[LocalAppScanError],
    ) -> dict[str, _AppRecord]:
        discovered: dict[str, _AppRecord] = {}
        if not self.generated_apps_root.is_dir():
            return discovered
        for directory in sorted(self.generated_apps_root.iterdir()):
            if (
                not directory.is_dir()
                or directory.name.startswith((".", "_"))
            ):
                continue
            app_id = directory.name
            if not _APP_ID_RE.fullmatch(app_id):
                errors.append(
                    LocalAppScanError(
                        app_id=app_id,
                        error="Application ID is invalid.",
                    )
                )
                continue
            try:
                manifest = load_manifest(directory / "manifest.yaml")
                await self._validate_adapter(directory, manifest)
            except (ManifestError, ValueError, OSError) as exc:
                errors.append(
                    LocalAppScanError(
                        app_id=app_id,
                        error=_safe_error(exc),
                    )
                )
                continue
            except Exception as exc:
                logger.warning(
                    "generated local app validation failed: %s",
                    app_id,
                    exc_info=True,
                )
                errors.append(
                    LocalAppScanError(
                        app_id=app_id,
                        error=_safe_error(exc),
                    )
                )
                continue
            existing = self._records.get(app_id)
            state = (
                existing.state
                if existing is not None
                else LocalAppState.DISCOVERED
            )
            if (
                manifest.local.unsupported_reason
                or not self._platform_supported(manifest.platform)
            ):
                state = LocalAppState.UNSUPPORTED
            record = existing or _AppRecord(
                app_dir=directory,
                manifest=manifest,
                state=state,
            )
            record.app_dir = directory
            record.manifest = manifest
            record.state = state
            record.generated = True
            if state == LocalAppState.UNSUPPORTED:
                record.last_error = (
                    manifest.local.unsupported_reason
                    or _UNSUPPORTED_PLATFORM_MESSAGE
                )
            elif existing is None:
                record.last_error = None
            discovered[app_id] = record
        return discovered

    async def status(
        self,
        app_id: str,
        context: LocalAppContext | None = None,
    ) -> LocalAppInfo:
        self._ensure_open()
        record = self._record(app_id)
        async with record.lock:
            self._reap_exited(record)
            return record.info()

    async def start(
        self,
        app_id: str,
        context: LocalAppContext | None = None,
    ) -> LocalAppRuntime:
        self._ensure_open()
        record = self._record(app_id)
        if record.state == LocalAppState.UNSUPPORTED:
            raise LocalAppError(
                "unsupported_platform",
                record.last_error
                or "The application backend is unavailable.",
            )
        async with record.lock:
            await self._assert_binding_control(record, context)
            if (
                context is not None
                and context.user_id is not None
                and self.session_factory is not None
            ):
                await self._ensure_binding(record, context)
            if (
                record.state == LocalAppState.RUNNING
                and (
                    record.managed is not None
                    or record.in_process_runtime is not None
                )
            ):
                if context is not None and context.user_id is not None:
                    await self._discover_server(record, context)
                return LocalAppRuntime(
                    info=record.info(),
                    server_id=record.server_id,
                    discovered_tool_count=record.discovered_tool_count,
                )
            if record.state == LocalAppState.STARTING:
                return LocalAppRuntime(record.info())

            await self._cleanup_previous_process(record)
            record.state = LocalAppState.STARTING
            record.last_error = None
            record.context = context
            if record.manifest.local.host_mode == "in_process":
                return await self._start_in_process(record, context)
            return await self._start_subprocess(record, context)

    async def _start_subprocess(
        self,
        record: _AppRecord,
        context: LocalAppContext | None,
    ) -> LocalAppRuntime:
            port = self._find_available_port(
                record.manifest.local.default_port
            )
            record.port = port
            record.url = f"http://127.0.0.1:{port}/mcp"
            if context is not None and context.user_id is not None:
                await self._update_binding_url(record, context)
            export_root = self.logs_root / "exports" / record.app_id
            export_root.mkdir(parents=True, exist_ok=True)
            self.logs_root.mkdir(parents=True, exist_ok=True)
            log_path = self.logs_root / f"{record.app_id}.log"
            app_args = [
                "--app",
                record.app_id,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--export-root",
                str(export_root),
            ]
            command = self._server_command(app_args)
            try:
                managed = self._spawn_process(
                    record,
                    command,
                    log_path,
                )
            except OSError as exc:
                record.state = LocalAppState.ERROR
                record.last_error = _safe_error(exc)
                raise LocalAppError(
                    "process_start_failed",
                    "应用进程启动失败。",
                ) from exc

            record.managed = managed
            record.started_at = _utcnow_iso()
            try:
                await self._wait_until_ready(record)
                managed.runtime_pid = await self._resolve_runtime_pid(
                    record,
                    managed,
                )
                record.state = LocalAppState.RUNNING
                record.last_health_at = _utcnow_iso()
                record.last_error = None
                if context is not None and context.user_id is not None:
                    await self._discover_server(record, context)
                self._write_log(
                    managed,
                    f"app ready pid={managed.pid} port={port}",
                )
                interval = record.manifest.local.health_interval_seconds
                if interval > 0:
                    record.health_task = asyncio.create_task(
                        self._health_loop(record, interval),
                        name=f"local-app-health-{record.app_id}",
                    )
                return LocalAppRuntime(
                    info=record.info(),
                    server_id=record.server_id,
                    discovered_tool_count=record.discovered_tool_count,
                )
            except LocalAppError as exc:
                record.state = LocalAppState.ERROR
                record.last_error = exc.safe_message
                await self._terminate_after_failure(record)
                raise
            except Exception as exc:
                record.state = LocalAppState.ERROR
                record.last_error = "应用启动后同步失败。"
                logger.exception(
                    "local app start failed: %s",
                    record.app_id,
                )
                await self._terminate_after_failure(record)
                raise LocalAppError(
                    "server_sync_failed",
                    "应用已启动，但 MCP Server 同步失败。",
                ) from exc

    async def _start_in_process(
        self,
        record: _AppRecord,
        context: LocalAppContext | None,
    ) -> LocalAppRuntime:
        if context is None or context.user_id is None:
            raise LocalAppError(
                "local_app_binding_required",
                "同进程应用必须由已认证用户启动。",
            )
        runtime: Any | None = None
        try:
            runtime = load_runtime(
                record.app_dir,
                export_root=self.logs_root / "exports" / record.app_id,
                connectors=self.connectors,
                timeout_seconds=record.manifest.local.startup_timeout_seconds,
                max_concurrency=1,
            )
            record.in_process_runtime = runtime
            record.managed = None
            record.port = None
            record.started_at = _utcnow_iso()
            record.last_health_at = record.started_at
            record.state = LocalAppState.RUNNING
            record.last_error = None
            await self._discover_server(record, context)
            return LocalAppRuntime(
                info=record.info(),
                server_id=record.server_id,
                discovered_tool_count=record.discovered_tool_count,
            )
        except LocalAppError:
            await self._close_in_process_runtime(record)
            record.state = LocalAppState.ERROR
            raise
        except Exception as exc:
            await self._close_in_process_runtime(record)
            record.state = LocalAppState.ERROR
            record.last_error = _safe_error(exc)
            raise LocalAppError(
                "in_process_start_failed",
                "同进程应用启动失败。",
            ) from exc

    async def stop(
        self,
        app_id: str,
        context: LocalAppContext | None = None,
    ) -> bool:
        self._ensure_open()
        record = self._record(app_id)
        async with record.lock:
            await self._assert_binding_control(record, context)
            if record.state in {
                LocalAppState.DISCOVERED,
                LocalAppState.STOPPED,
            } and (
                record.managed is None
                and record.in_process_runtime is None
            ):
                return False
            if (
                record.managed is None
                and record.in_process_runtime is None
            ):
                record.state = LocalAppState.STOPPED
                return False
            record.state = LocalAppState.STOPPING
            try:
                await self._terminate_record(record)
            except LocalAppError as exc:
                record.state = LocalAppState.ERROR
                record.last_error = exc.safe_message
                raise
            record.state = LocalAppState.STOPPED
            record.last_error = None
            record.last_health_at = None
            await self._mark_server_stopped(record)
            return True

    async def restart(
        self,
        app_id: str,
        context: LocalAppContext | None = None,
    ) -> LocalAppRuntime:
        await self.stop(app_id, context)
        return await self.start(app_id, context)

    async def logs(
        self,
        app_id: str,
        lines: int = 200,
        context: LocalAppContext | None = None,
    ) -> str:
        self._ensure_open()
        record = self._record(app_id)
        await self._assert_binding_control(record, context)
        try:
            count = max(1, min(_MAX_LOG_LINES, int(lines)))
        except (TypeError, ValueError):
            count = 200
        if record.manifest.local.host_mode == "in_process":
            return ""
        path = self.logs_root / f"{app_id}.log"
        if not path.is_file():
            return ""
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            selected = deque(handle, maxlen=count)
        return "\n".join(
            _sanitize_log_line(line.rstrip("\r\n"))
            for line in selected
        )

    async def auto_start(self) -> None:
        if self.session_factory is None:
            return
        async with self.session_factory() as db:
            servers = list(
                (
                    await db.execute(select(MCPServer))
                ).scalars().all()
            )
        for server in servers:
            config = dict(server.config or {})
            app_id = str(config.get("local_app_id") or "").strip()
            if (
                config.get("managed_by") != "local_app_manager"
                or config.get("auto_start") is not True
                or not app_id
            ):
                continue
            record = self._records.get(app_id)
            if record is None or record.state == LocalAppState.UNSUPPORTED:
                continue
            owner_user_id = _as_int(config.get("owner_user_id"))
            workspace_id = _as_int(config.get("workspace_id"))
            if owner_user_id is None:
                continue
            if int(server.user_id) != owner_user_id:
                continue
            if self.workspace_isolation and workspace_id is None:
                continue
            if (
                self.workspace_isolation
                and server.workspace_id != workspace_id
            ):
                continue
            base_url = _base_url_from_url(server.url)
            context = LocalAppContext(
                user_id=owner_user_id,
                workspace_id=workspace_id,
                public_base_url=base_url,
            )
            try:
                await self.start(app_id, context)
            except LocalAppError as exc:
                logger.warning(
                    "local app auto-start skipped: %s: %s",
                    app_id,
                    exc.safe_message,
                )

    async def binding_info(
        self,
        app_id: str,
        context: LocalAppContext,
    ) -> dict[str, Any]:
        self._ensure_open()
        record = self._record(app_id)
        server = await self._bound_server(record)
        return self._binding_payload(record, server, context)

    async def update_binding(
        self,
        app_id: str,
        *,
        auto_start: bool,
        context: LocalAppContext,
    ) -> dict[str, Any]:
        self._ensure_open()
        record = self._record(app_id)
        async with record.lock:
            server, config = await self._require_owned_binding(
                record,
                context,
            )
            config["auto_start"] = bool(auto_start)
            server.config = config
            async with self.session_factory() as db:  # type: ignore[union-attr]
                merged = await db.get(MCPServer, server.id)
                if merged is None:
                    raise LocalAppError(
                        "local_app_unbound",
                        "本地应用尚未绑定。",
                    )
                merged.config = config
                await db.commit()
            record.binding_auto_start = bool(auto_start)
            return self._binding_payload(record, server, context)

    async def remove_binding(
        self,
        app_id: str,
        context: LocalAppContext,
    ) -> dict[str, Any]:
        self._ensure_open()
        record = self._record(app_id)
        async with record.lock:
            server, config = await self._require_owned_binding(
                record,
                context,
            )
            if (
                record.managed is not None
                or record.in_process_runtime is not None
            ):
                record.state = LocalAppState.STOPPING
                await self._terminate_record(record)
                record.state = LocalAppState.STOPPED
            config["auto_start"] = False
            config["owner_user_id"] = None
            config["workspace_id"] = None
            record.binding_auto_start = False
            record.server_id = server.id
            record.context = None
            async with self.session_factory() as db:  # type: ignore[union-attr]
                merged = await db.get(MCPServer, server.id)
                if merged is None:
                    raise LocalAppError(
                        "local_app_unbound",
                        "本地应用尚未绑定。",
                    )
                merged.enabled = False
                merged.config = config
                merged.secret_encrypted = None
                merged.tools = []
                merged.last_error = "本地应用绑定已解除。"
                await db.commit()
            server.config = config
            server.secret_encrypted = None
            server.tools = []
            return self._binding_payload(record, server, context)

    async def authenticate_in_process(
        self,
        app_id: str,
        token: str,
    ) -> InProcessMCPTarget:
        self._ensure_open()
        record = self._record(app_id)
        if record.manifest.local.host_mode != "in_process":
            raise LocalAppError(
                "not_in_process_app",
                "应用不是同进程 MCP 服务。",
            )
        if (
            record.state != LocalAppState.RUNNING
            or record.in_process_runtime is None
        ):
            raise LocalAppError(
                "app_not_running",
                "同进程应用尚未启动。",
            )
        if (
            record.server_id is None
            or self.session_factory is None
            or self.settings is None
        ):
            raise LocalAppError(
                "local_app_unauthenticated",
                "同进程应用缺少本地访问凭据。",
            )
        async with self.session_factory() as db:
            server = await db.get(MCPServer, record.server_id)
        if (
            server is None
            or server.secret_encrypted is None
            or not server.enabled
        ):
            raise LocalAppError(
                "local_app_unauthenticated",
                "同进程应用缺少本地访问凭据。",
            )
        try:
            expected = decrypt_value(
                self.settings,
                server.secret_encrypted,
            )
        except Exception as exc:
            raise LocalAppError(
                "local_app_unauthenticated",
                "同进程应用访问凭据无效。",
            ) from exc
        if not hmac.compare_digest(expected, str(token or "")):
            raise LocalAppError(
                "invalid_local_app_token",
                "本地应用访问令牌无效。",
            )
        return InProcessMCPTarget(
            app_id=record.app_id,
            runtime=record.in_process_runtime,
            server_id=server.id,
            user_id=int(server.user_id),
            workspace_id=server.workspace_id,
        )

    def in_process_runtime(self, app_id: str) -> Any | None:
        text = str(app_id or "").strip()
        record = self._records.get(text)
        if record is None or record.state != LocalAppState.RUNNING:
            return None
        return record.in_process_runtime

    async def close_all(self) -> None:
        if self._closed:
            return
        for app_id in list(self._records):
            try:
                await self.stop(app_id)
            except Exception:
                logger.debug(
                    "failed to stop local app %s",
                    app_id,
                    exc_info=True,
                )
        self._closed = True

    async def _validate_adapter(
        self,
        directory: Path,
        manifest: AppManifest,
    ) -> None:
        runtime = load_runtime(
            directory,
            export_root=self.logs_root / "validation" / manifest.id,
            connectors=self.connectors,
            timeout_seconds=manifest.local.startup_timeout_seconds,
        )
        await runtime.close()

    def _record(self, app_id: str) -> _AppRecord:
        text = str(app_id or "").strip()
        if not _APP_ID_RE.fullmatch(text):
            raise LocalAppError("invalid_app_id", "应用 ID 不合法。")
        record = self._records.get(text)
        if record is None:
            raise LocalAppError("app_not_found", "应用不存在或尚未扫描。")
        return record

    async def _bound_server(
        self,
        record: _AppRecord,
    ) -> MCPServer | None:
        if self.session_factory is None:
            return None
        async with self.session_factory() as db:
            if record.server_id is not None:
                server = await db.get(MCPServer, record.server_id)
                if (
                    server is not None
                    and str(
                        (server.config or {}).get("local_app_id") or ""
                    )
                    == record.app_id
                ):
                    return server
            rows = list(
                (
                    await db.execute(
                        select(MCPServer).where(
                            MCPServer.name == record.manifest.name
                        )
                    )
                ).scalars().all()
            )
        matches = [
            server
            for server in rows
            if str(
                (server.config or {}).get("local_app_id") or ""
            )
            == record.app_id
        ]
        return matches[0] if len(matches) == 1 else None

    async def _refresh_binding_states(self) -> None:
        if self.session_factory is None:
            for record in self._records.values():
                record.binding_auto_start = False
            return
        async with self.session_factory() as db:
            servers = list(
                (
                    await db.execute(select(MCPServer))
                ).scalars().all()
            )
        for record in self._records.values():
            matches = [
                server
                for server in servers
                if str(
                    (server.config or {}).get("local_app_id") or ""
                )
                == record.app_id
            ]
            if len(matches) == 1:
                server = matches[0]
                record.server_id = server.id
                record.binding_auto_start = (
                    _as_int((server.config or {}).get("owner_user_id"))
                    is not None
                    and (server.config or {}).get("auto_start") is True
                )
            else:
                record.server_id = None
                record.binding_auto_start = False

    async def _binding_lock_target(
        self,
        db: AsyncSession,
        record: _AppRecord,
    ) -> MCPServer | None:
        if record.server_id is not None:
            server = await db.get(MCPServer, record.server_id)
            if (
                server is not None
                and str(
                    (server.config or {}).get("local_app_id") or ""
                )
                == record.app_id
            ):
                return server
        rows = list(
            (
                await db.execute(
                    select(MCPServer).where(
                        MCPServer.name == record.manifest.name
                    )
                )
            ).scalars().all()
        )
        matches = [
            server
            for server in rows
            if str(
                (server.config or {}).get("local_app_id") or ""
            )
            == record.app_id
        ]
        return matches[0] if len(matches) == 1 else None

    async def _require_owned_binding(
        self,
        record: _AppRecord,
        context: LocalAppContext | None,
    ) -> tuple[MCPServer, dict[str, Any]]:
        if (
            context is None
            or context.user_id is None
            or self.session_factory is None
        ):
            raise LocalAppError(
                "local_app_owner_required",
                "需要已认证的应用所有者。",
            )
        async with self.session_factory() as db:
            server = await self._binding_lock_target(db, record)
        if server is None:
            raise LocalAppError(
                "local_app_unbound",
                "本地应用尚未绑定。",
            )
        config = dict(server.config or {})
        owner_user_id = _as_int(config.get("owner_user_id"))
        if owner_user_id is None:
            raise LocalAppError(
                "local_app_unbound",
                "本地应用绑定信息不完整。",
            )
        if owner_user_id != context.user_id:
            raise LocalAppError(
                "local_app_owner_conflict",
                "本地应用已绑定给其他用户。",
            )
        if self.workspace_isolation:
            bound_workspace = _as_int(config.get("workspace_id"))
            if (
                bound_workspace is None
                or context.workspace_id is None
                or bound_workspace != context.workspace_id
            ):
                raise LocalAppError(
                    "local_app_owner_conflict",
                    "本地应用已绑定给其他工作区。",
                )
        return server, config

    async def _assert_binding_control(
        self,
        record: _AppRecord,
        context: LocalAppContext | None,
    ) -> None:
        if context is None or context.user_id is None:
            return
        server = await self._bound_server(record)
        if server is None:
            self._assert_control(record, context)
            return
        config = dict(server.config or {})
        owner_user_id = _as_int(config.get("owner_user_id"))
        if owner_user_id is None:
            return
        if owner_user_id != context.user_id:
            raise LocalAppError(
                "local_app_owner_conflict",
                "本地应用已绑定给其他用户。",
            )
        if self.workspace_isolation:
            bound_workspace = _as_int(config.get("workspace_id"))
            if (
                context.workspace_id is None
                or bound_workspace != context.workspace_id
            ):
                raise LocalAppError(
                    "local_app_owner_conflict",
                    "本地应用已绑定给其他工作区。",
                )

    def _binding_payload(
        self,
        record: _AppRecord,
        server: MCPServer | None,
        context: LocalAppContext,
    ) -> dict[str, Any]:
        config = dict(server.config or {}) if server is not None else {}
        owner_user_id = _as_int(config.get("owner_user_id"))
        bound = (
            server is not None
            and config.get("managed_by") == "local_app_manager"
            and config.get("local_app_id") == record.app_id
            and owner_user_id is not None
            and int(server.user_id) == owner_user_id
            and (
                not self.workspace_isolation
                or server.workspace_id
                == _as_int(config.get("workspace_id"))
            )
        )
        is_owner = bool(
            bound
            and context.user_id is not None
            and context.user_id == owner_user_id
            and (
                not self.workspace_isolation
                or context.workspace_id
                == _as_int(config.get("workspace_id"))
            )
        )
        payload: dict[str, Any] = {
            "app_id": record.app_id,
            "bound": bound,
            "is_owner": is_owner,
            "can_manage": is_owner,
            "auto_start": bool(bound and config.get("auto_start") is True),
            "workspace_bound": bool(
                bound and _as_int(config.get("workspace_id")) is not None
            ),
            "host_mode": record.manifest.local.host_mode,
        }
        if is_owner:
            payload["owner_user_id"] = owner_user_id
            payload["workspace_id"] = _as_int(config.get("workspace_id"))
        return payload

    async def _ensure_binding(
        self,
        record: _AppRecord,
        context: LocalAppContext,
    ) -> MCPServer:
        if context.user_id is None or self.session_factory is None:
            raise LocalAppError(
                "local_app_binding_required",
                "本地应用需要已认证用户。",
            )
        if self.workspace_isolation and context.workspace_id is None:
            raise LocalAppError(
                "workspace_required",
                "开启工作区隔离时必须绑定当前工作区。",
            )
        if record.manifest.local.host_mode == "in_process":
            record.url = _in_process_url(
                context.public_base_url,
                record.app_id,
            )
        async with self.session_factory() as db:
            rows = list(
                (
                    await db.execute(
                        select(MCPServer).where(
                            MCPServer.name == record.manifest.name
                        )
                    )
                ).scalars().all()
            )
            local_rows = [
                server
                for server in rows
                if (server.config or {}).get("managed_by")
                == "local_app_manager"
                or (server.config or {}).get("local_app_id")
            ]
            app_rows = [
                server
                for server in local_rows
                if (server.config or {}).get("local_app_id")
                == record.app_id
            ]
            if len(app_rows) > 1:
                raise LocalAppError(
                    "local_app_binding_corrupt",
                    "检测到重复的本地应用绑定。",
                )
            if app_rows:
                server = app_rows[0]
            elif rows:
                raise LocalAppError(
                    "server_conflict",
                    "同名 MCP Server 已被其他应用使用。",
                )
            else:
                server = MCPServer(
                    user_id=context.user_id,
                    workspace_id=context.workspace_id,
                    name=record.manifest.name,
                    description=record.manifest.description,
                    transport="http",
                    url=record.url,
                    enabled=True,
                    default_risk="mutating",
                    config={},
                    tools=[],
                )
                db.add(server)
                await db.flush()

            config = dict(server.config or {})
            owner_user_id = _as_int(config.get("owner_user_id"))
            adopting_unbound = owner_user_id is None
            if owner_user_id is None:
                server.tools = []
                server.secret_encrypted = None
            elif owner_user_id != context.user_id:
                raise LocalAppError(
                    "local_app_owner_conflict",
                    "本地应用已绑定给其他用户。",
                )
            if self.workspace_isolation:
                bound_workspace = _as_int(config.get("workspace_id"))
                if (
                    owner_user_id is not None
                    and bound_workspace != context.workspace_id
                ):
                    raise LocalAppError(
                        "local_app_owner_conflict",
                        "本地应用已绑定给其他工作区。",
                    )
            server.user_id = context.user_id
            server.workspace_id = context.workspace_id
            server.enabled = True
            server.transport = "http"
            if record.url:
                server.url = record.url
            config.update(
                {
                    "managed_by": "local_app_manager",
                    "local_managed": True,
                    "local_app_id": record.app_id,
                    "host_mode": record.manifest.local.host_mode,
                    "owner_user_id": context.user_id,
                    "workspace_id": context.workspace_id,
                    "auto_start": (
                        False
                        if adopting_unbound
                        else bool(config.get("auto_start") is True)
                    ),
                    "manifest_version": record.manifest.version,
                }
            )
            if record.manifest.local.host_mode == "in_process":
                await self._ensure_in_process_secret(server, config)
                if not record.url:
                    record.url = _base_url_from_url(server.url)
                    if record.url:
                        record.url = _in_process_url(
                            record.url,
                            record.app_id,
                        )
                        server.url = record.url
            server.config = config
            await db.commit()
            await db.refresh(server)
        record.server_id = server.id
        record.context = context
        record.binding_auto_start = bool(config.get("auto_start"))
        return server

    async def _ensure_in_process_secret(
        self,
        server: MCPServer,
        config: dict[str, Any],
    ) -> None:
        settings = self._credential_settings()
        token = ""
        if server.secret_encrypted is not None:
            try:
                token = decrypt_value(settings, server.secret_encrypted)
            except (InvalidToken, TypeError, ValueError):
                token = ""
        if not token:
            token = secrets.token_urlsafe(32)
            try:
                server.secret_encrypted = encrypt_value(settings, token)
            except (TypeError, ValueError) as exc:
                raise LocalAppError(
                    "local_app_credential_error",
                    "无法建立本地应用访问凭据，请检查加密密钥配置。",
                ) from exc
        config["local_token_required"] = True

    def _credential_settings(self) -> Any:
        if self.settings is None:
            from app.config import get_settings

            self.settings = get_settings()
        return self.settings

    async def _update_binding_url(
        self,
        record: _AppRecord,
        context: LocalAppContext,
    ) -> None:
        if record.server_id is None or self.session_factory is None:
            return
        async with self.session_factory() as db:
            server = await db.get(MCPServer, record.server_id)
            if server is None:
                return
            server.url = record.url
            server.enabled = True
            server.transport = "http"
            await db.commit()

    async def _discover_server(
        self,
        record: _AppRecord,
        context: LocalAppContext,
    ) -> None:
        if (
            self.registry is None
            or record.server_id is None
            or context.user_id is None
            or self.session_factory is None
        ):
            return
        async with self.session_factory() as db:
            server = await db.get(MCPServer, record.server_id)
            if server is None:
                raise LocalAppError(
                    "server_sync_failed",
                    "本地 MCP Server 记录不存在。",
                )
            server.url = record.url
            server.enabled = True
            server.transport = "http"
            await db.commit()
            found = await self.registry.test_server(
                server.id,
                user_id=context.user_id,
                workspace_id=context.workspace_id,
                session=db,
            )
        if not found.get("success"):
            raise LocalAppError(
                "tool_discovery_failed",
                "应用已启动，但工具发现失败。",
            )
        record.discovered_tool_count = int(
            found.get("tool_count") or 0
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise LocalAppError("manager_closed", "本地应用管理器已关闭。")

    @staticmethod
    def _assert_control(
        record: _AppRecord,
        context: LocalAppContext | None,
    ) -> None:
        if (
            context is None
            or context.user_id is None
            or record.context is None
            or record.context.user_id is None
        ):
            return
        if record.context.user_id != context.user_id:
            raise LocalAppError(
                "app_forbidden",
                "This local application is controlled by another user.",
            )

    def _platform_supported(self, platform: str) -> bool:
        del platform
        return self.platform_name.startswith("win")

    def _spawn_process(
        self,
        record: _AppRecord,
        command: list[str],
        log_path: Path,
    ) -> _ManagedProcess:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8", buffering=1)
        working_directory = (
            stable_data_dir()
            if getattr(sys, "frozen", False)
            else self.project_root
        )
        kwargs: dict[str, Any] = {
            "cwd": str(working_directory),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "shell": False,
        }
        if self.platform_name.startswith("win"):
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            process = self._process_factory(
                command,
                **kwargs,
            )
        except Exception:
            log_file.close()
            raise
        managed = _ManagedProcess(
            process=process,
            pid=int(process.pid),
            runtime_pid=None,
            log_path=log_path,
            log_file=log_file,
        )
        managed.log_thread = threading.Thread(
            target=_pump_logs,
            args=(getattr(process, "stdout", None), log_file),
            name=f"app-mcp-log-{record.app_id}",
            daemon=True,
        )
        managed.log_thread.start()
        self._write_log(
            managed,
            f"starting {record.app_id} on port {record.port}",
        )
        return managed

    def _server_command(self, app_args: list[str]) -> list[str]:
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "--internal-app-mcp-worker",
                *app_args,
            ]
        return [
            sys.executable,
            str(self.script_path),
            *app_args,
        ]

    async def _wait_until_ready(self, record: _AppRecord) -> None:
        timeout = record.manifest.local.startup_timeout_seconds
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            managed = record.managed
            if managed is None:
                raise LocalAppError("process_exited", "应用进程未启动。")
            if managed.process.poll() is not None:
                self._write_log(managed, "process exited before health check")
                raise LocalAppError(
                    "process_exited",
                    "应用进程在启动完成前退出。",
                )
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise LocalAppError(
                    "startup_timeout",
                    "应用启动超时。",
                )
            try:
                healthy = await self._health_checker(
                    record.url or "",
                    timeout=min(1.5, remaining),
                )
            except Exception:  # noqa: BLE001
                healthy = False
            if healthy:
                return
            await asyncio.sleep(
                min(_STARTUP_POLL_SECONDS, max(0.01, remaining))
            )

    async def _resolve_runtime_pid(
        self,
        record: _AppRecord,
        managed: _ManagedProcess,
    ) -> int | None:
        if not isinstance(managed.process, subprocess.Popen):
            return managed.pid
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                response = await client.get(record.url or "")
                payload = response.json()
            runtime = payload.get("runtime")
            server = payload.get("server")
            pid = (
                int(runtime.get("pid"))
                if isinstance(runtime, dict)
                and runtime.get("pid") is not None
                else None
            )
            app_id = (
                str(server.get("name") or "")
                if isinstance(server, dict)
                else ""
            )
            if (
                response.status_code == 200
                and app_id == record.app_id
                and pid is not None
                and pid > 0
            ):
                return pid
        except (httpx.HTTPError, OSError, TypeError, ValueError):
            pass
        if (
            self.platform_name.startswith("win")
            and record.port is not None
        ):
            return await asyncio.to_thread(
                _windows_port_pid,
                record.port,
            )
        return managed.pid

    async def _health_loop(
        self,
        record: _AppRecord,
        interval: float,
    ) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                if record.state != LocalAppState.RUNNING:
                    return
                if (
                    record.managed is None
                    or record.managed.process.poll() is not None
                ):
                    record.state = LocalAppState.ERROR
                    record.last_error = "本地应用进程已退出。"
                    return
                try:
                    healthy = await self._health_checker(
                        record.url or "",
                        timeout=min(2.0, interval),
                    )
                except Exception:  # noqa: BLE001
                    healthy = False
                if healthy:
                    record.last_health_at = _utcnow_iso()
                    if record.state == LocalAppState.ERROR:
                        record.state = LocalAppState.RUNNING
                        record.last_error = None
                else:
                    record.state = LocalAppState.ERROR
                    record.last_error = "本地应用健康检查失败。"
        except asyncio.CancelledError:
            return

    def _reap_exited(self, record: _AppRecord) -> None:
        if (
            record.managed is not None
            and record.state == LocalAppState.RUNNING
            and record.managed.process.poll() is not None
        ):
            record.state = LocalAppState.ERROR
            record.last_error = "本地应用进程已退出。"
            self._write_log(record.managed, "process exited")
            if record.health_task is not None:
                record.health_task.cancel()
                record.health_task = None

    async def _cleanup_previous_process(self, record: _AppRecord) -> None:
        if record.managed is not None:
            await self._terminate_record(record)
        if record.in_process_runtime is not None:
            await self._close_in_process_runtime(record)
        record.state = LocalAppState.DISCOVERED

    async def _terminate_after_failure(self, record: _AppRecord) -> None:
        try:
            await self._terminate_record(record)
        except LocalAppError as exc:
            logger.error(
                "local app cleanup failed: %s: %s",
                record.app_id,
                exc.safe_message,
            )

    async def _terminate_record(self, record: _AppRecord) -> None:
        if record.health_task is not None:
            record.health_task.cancel()
            record.health_task = None
        if record.in_process_runtime is not None:
            await self._close_in_process_runtime(record)
            return
        managed = record.managed
        if managed is None:
            return
        process = managed.process
        if int(process.pid) != managed.pid:
            record.managed = None
            return
        self._write_log(managed, "stopping managed process")
        try:
            if (
                self.platform_name.startswith("win")
                and isinstance(process, subprocess.Popen)
            ):
                if (
                    managed.runtime_pid is None
                    and record.port is not None
                ):
                    managed.runtime_pid = await asyncio.to_thread(
                        _windows_port_pid,
                        record.port,
                    )
                targets = [
                    pid
                    for pid in (managed.runtime_pid, managed.pid)
                    if pid is not None and pid > 0
                ]
                for pid in dict.fromkeys(targets):
                    await asyncio.to_thread(
                        subprocess.run,
                        [
                            "taskkill",
                            "/PID",
                            str(pid),
                            "/T",
                            "/F",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                try:
                    await asyncio.to_thread(process.wait, timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            else:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=3)
        except ProcessLookupError:
            pass
        except Exception:
            logger.debug(
                "terminate local app failed: %s",
                record.app_id,
                exc_info=True,
            )
            try:
                process.kill()
            except Exception:
                logger.debug(
                    "force kill local app failed: %s",
                    record.app_id,
                    exc_info=True,
                )
        finally:
            _close_managed(managed)
            record.managed = None
        if (
            self.platform_name.startswith("win")
            and record.port is not None
            and not await self._wait_for_port_closed(record.port)
        ):
            raise LocalAppError(
                "termination_failed",
                "应用进程已结束，但监听端口仍未释放。",
            )

    async def _close_in_process_runtime(
        self,
        record: _AppRecord,
    ) -> None:
        runtime = record.in_process_runtime
        record.in_process_runtime = None
        if runtime is None:
            return
        try:
            await runtime.close()
        except Exception:
            logger.debug(
                "close in-process app runtime failed: %s",
                record.app_id,
                exc_info=True,
            )

    def _find_available_port(self, preferred: int | None) -> int:
        start = preferred or 8767
        for offset in range(200):
            port = start + offset
            if port > 65535:
                port = 1024 + ((port - 1024) % (65535 - 1024))
            if self._port_available(port):
                return port
        raise LocalAppError("port_exhausted", "安全端口范围内没有可用端口。")

    @staticmethod
    def _port_available(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                return False
        return True

    async def _wait_for_port_closed(
        self,
        port: int,
        timeout: float = 3.0,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if not await asyncio.to_thread(_port_accepts_connections, port):
                return True
            await asyncio.sleep(0.05)
        return False

    async def _mark_server_stopped(self, record: _AppRecord) -> None:
        if self.session_factory is None or record.server_id is None:
            return
        async with self.session_factory() as db:
            server = await db.get(MCPServer, record.server_id)
            if server is None:
                return
            server.enabled = False
            server.last_error = None
            await db.commit()

    @staticmethod
    def _write_log(managed: _ManagedProcess, message: str) -> None:
        try:
            managed.log_file.write(_sanitize_log_line(message) + "\n")
            managed.log_file.flush()
        except OSError:
            return


def _pump_logs(stream: Any, log_file: Any) -> None:
    if stream is None:
        return
    try:
        for line in stream:
            log_file.write(_sanitize_log_line(line.rstrip("\r\n")) + "\n")
            log_file.flush()
    except (OSError, ValueError):
        return
    finally:
        try:
            stream.close()
        except Exception:
            logger.debug("close app log stream failed", exc_info=True)


def _close_managed(managed: _ManagedProcess) -> None:
    thread = managed.log_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=1)
    try:
        managed.log_file.close()
    except Exception:
        logger.debug("close app log file failed", exc_info=True)


def _sanitize_log_line(value: Any) -> str:
    text = _LOG_CONTROL.sub("", str(value or ""))
    text = _LOG_BEARER.sub("Bearer ***", text)
    text = _LOG_SECRET.sub(
        lambda match: f"{match.group(1)}=***",
        text,
    )
    return text[:_MAX_LOG_LINE]


def _safe_error(value: BaseException | str) -> str:
    text = _sanitize_log_line(value)
    return text or "未知错误。"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _base_url_from_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(str(value))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _in_process_url(base_url: str | None, app_id: str) -> str:
    parsed = urlsplit(str(base_url or ""))
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or host not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise LocalAppError(
            "backend_base_url_required",
            "同进程应用需要后端实际的本机访问地址。",
        )
    root = urlunsplit(
        (parsed.scheme, parsed.netloc, "", "", "")
    ).rstrip("/")
    return f"{root}/api/local-apps/{app_id}/mcp"


def _windows_port_pid(port: int) -> int | None:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    needle = f":{int(port)}"
    for line in result.stdout.splitlines():
        columns = line.split()
        if (
            len(columns) >= 5
            and columns[0].upper() == "TCP"
            and columns[1].endswith(needle)
            and columns[3].upper() == "LISTENING"
        ):
            try:
                return int(columns[4])
            except ValueError:
                continue
    return None


def _port_accepts_connections(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


async def _default_health_check(url: str, timeout: float) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
    except (httpx.HTTPError, OSError):
        return False
    return response.status_code == 200


__all__ = [
    "InProcessMCPTarget",
    "LocalAppContext",
    "LocalAppError",
    "LocalAppInfo",
    "LocalAppManager",
    "LocalAppRuntime",
    "LocalAppScanError",
    "LocalAppScanResult",
    "LocalAppState",
]
