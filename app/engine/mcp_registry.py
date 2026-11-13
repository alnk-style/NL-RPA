"""MCP Server 注册表：配置、工具发现、权限门、调用与审计。

注册表把数据库里的 Server 配置翻译成 :class:`MCPClient` 调用：

1. 解析 Server（按 id 或名字）并解密密钥引用；
2. 按工具清单里的风险级别做权限判断（read_only 默认允许，
   mutating 需要显式授权，destructive 需要授权 + 确认）；
3. 未授权直接拒绝并写审计，不发起任何网络调用；
4. 调用成功后把结果包装成不可信数据，写审计并返回。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import runtime_flags
from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.engine.connectors.audit import ToolCallAuditWriter, audit_secrets
from app.engine.connectors.permissions import (
    RISK_READ_ONLY,
    evaluate_permission,
    infer_risk_level,
    normalize_risk_level,
)
from app.engine.connectors.untrusted import wrap_untrusted_result
from app.engine.mcp_client import (
    MCPClientError,
    MCPClientPool,
    MCPServerConfig,
    MCPToolCall,
)
from app.models.mcp import MCPServer, ToolCallAudit
from app.protocols import FailureCategory
from app.security.credentials import decrypt_value, encrypt_value
from app.security.plugin_integrity import (
    canonical_plugin_spec,
    compute_fingerprint,
    compute_signature,
    compute_toolset_fingerprint,
    verify_plugin_config,
)

logger = logging.getLogger(__name__)

_RETRYABLE_CATEGORIES = frozenset(
    {
        FailureCategory.TIMEOUT,
        FailureCategory.UNKNOWN,
        FailureCategory.BUSINESS_ERROR,
    }
)


@dataclass
class MCPCallOutcome:
    """一次 MCP 工具调用的执行结果。"""

    success: bool
    server: str
    tool: str
    risk_level: str = "mutating"
    decision: str = "allowed"
    error: str | None = None
    error_category: FailureCategory = FailureCategory.UNKNOWN
    duration_ms: float = 0.0
    attempts: int = 1
    audit_id: int | None = None
    result: dict[str, Any] | None = None
    requires_confirmation: bool = False
    authorized_by: str | None = None

    def to_step_details(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "tool": self.tool,
            "risk_level": self.risk_level,
            "decision": self.decision,
            "authorized_by": self.authorized_by,
            "duration_ms": round(self.duration_ms, 3),
            "attempts": self.attempts,
            "audit_id": self.audit_id,
            "requires_confirmation": self.requires_confirmation,
        }


@dataclass
class MCPToolInfo:
    name: str
    risk_level: str
    authorized: bool = False
    confirmed: bool = False
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def merge_tool_manifest(
    existing: Any,
    discovered: list[dict[str, Any]],
    *,
    default_risk: str,
) -> list[dict[str, Any]]:
    """把新发现的工具与已有授权状态合并。

    已有工具保留 risk_level / authorized / confirmed；消失的工具保留
    记录并标记 ``available=false``，重新出现时无需再次授权。
    """

    prior_by_name: dict[str, dict[str, Any]] = {}
    if isinstance(existing, list):
        for item in existing:
            if isinstance(item, dict) and item.get("name"):
                prior_by_name[str(item["name"])] = item

    now = _utcnow().isoformat()
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in discovered:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        prior = prior_by_name.get(name, {})
        risk = prior.get("risk_level") or infer_risk_level(
            tool,
            default_risk,
        )
        schema = (
            tool.get("inputSchema")
            or tool.get("input_schema")
            or {}
        )
        merged.append(
            {
                "name": name,
                "description": str(tool.get("description") or ""),
                "input_schema": schema
                if isinstance(schema, dict)
                else {},
                "annotations": (
                    dict(tool.get("annotations"))
                    if isinstance(tool.get("annotations"), dict)
                    else {}
                ),
                "risk_level": normalize_risk_level(risk, default_risk),
                "authorized": bool(prior.get("authorized")),
                "confirmed": bool(prior.get("confirmed")),
                "authorized_at": prior.get("authorized_at"),
                "authorized_by": prior.get("authorized_by"),
                "available": True,
                "last_seen_at": now,
            }
        )

    for name, prior in prior_by_name.items():
        if name in seen:
            continue
        item = dict(prior)
        item.setdefault("risk_level", normalize_risk_level(None, default_risk))
        item["available"] = False
        merged.append(item)
    return merged


def find_tool_manifest(
    tools: Any,
    tool: str,
) -> dict[str, Any] | None:
    if not isinstance(tools, list):
        return None
    target = str(tool or "").strip()
    for item in tools:
        if isinstance(item, dict) and str(item.get("name") or "") == target:
            return item
    return None


class MCPRegistry:
    """MCP Server 注册表与调用入口。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        config: Settings | Any | None = None,
        client_pool: MCPClientPool | None = None,
        audit: ToolCallAuditWriter | None = None,
        prefer_sdk: bool = True,
    ) -> None:
        self.session_factory = session_factory or AsyncSessionLocal
        self.config = config
        self.audit = audit or ToolCallAuditWriter(self.session_factory)
        settings = self._settings()
        self.client_pool = client_pool or MCPClientPool(
            max_concurrency=getattr(
                settings,
                "MCP_MAX_CONCURRENCY",
                2,
            ),
            prefer_sdk=prefer_sdk,
        )

    # ---------------------------------------------------------------- 配置

    def _settings(self) -> Any:
        if self.config is not None:
            return self.config
        try:
            return get_settings()
        except Exception:  # noqa: BLE001
            return None

    @property
    def enabled(self) -> bool:
        return runtime_flags.resolve("mcp_client", self._settings())

    def default_timeout(self) -> float:
        settings = self._settings()
        try:
            timeout = float(
                getattr(settings, "MCP_CALL_TIMEOUT", 30.0) or 30.0
            )
        except (TypeError, ValueError):
            timeout = 30.0
        return timeout if timeout > 0 else 30.0

    def stdio_enabled(self) -> bool:
        return bool(getattr(self._settings(), "MCP_STDIO_ENABLED", False))

    def integrity_enabled(self) -> bool:
        return bool(
            getattr(self._settings(), "PLUGIN_INTEGRITY", False)
        )

    def require_signature(self) -> bool:
        return bool(
            getattr(self._settings(), "PLUGIN_REQUIRE_SIGNATURE", False)
        )

    def signing_secret(self) -> str | None:
        value = getattr(self._settings(), "PLUGIN_SIGNING_SECRET", "")
        text = str(value).strip() if value else ""
        return text or None

    # ------------------------------------------------------------ 数据库

    @asynccontextmanager
    async def _session_scope(
        self,
        session: AsyncSession | None,
    ) -> AsyncIterator[AsyncSession]:
        if session is not None:
            yield session
            return
        async with self.session_factory() as own_session:
            try:
                yield own_session
            except Exception:
                await own_session.rollback()
                raise
            # 调用审计、工具清单刷新等写入都在这个作用域里提交，
            # 避免调用方忘记 commit 导致审计丢失。
            await own_session.commit()

    async def list_servers(
        self,
        *,
        user_id: int | None = None,
        workspace_id: int | None = None,
        session: AsyncSession | None = None,
    ) -> list[MCPServer]:
        async with self._session_scope(session) as db:
            statement = select(MCPServer).order_by(MCPServer.id)
            if user_id is not None:
                statement = statement.where(MCPServer.user_id == user_id)
            if workspace_id is not None:
                statement = statement.where(
                    MCPServer.workspace_id == workspace_id
                )
            result = await db.execute(statement)
            return list(result.scalars().all())

    async def get_server(
        self,
        reference: Any,
        *,
        user_id: int | None = None,
        workspace_id: int | None = None,
        session: AsyncSession | None = None,
    ) -> MCPServer | None:
        text = str(reference or "").strip()
        if not text:
            return None
        async with self._session_scope(session) as db:
            server: MCPServer | None = None
            if text.isdigit():
                server = await db.get(MCPServer, int(text))
            if server is None:
                statement = select(MCPServer).where(MCPServer.name == text)
                if user_id is not None:
                    statement = statement.where(
                        MCPServer.user_id == user_id
                    )
                if workspace_id is not None:
                    statement = statement.where(
                        MCPServer.workspace_id == workspace_id
                    )
                result = await db.execute(statement)
                server = result.scalars().first()
            if (
                server is not None
                and user_id is not None
                and server.user_id != user_id
            ):
                return None
            if (
                server is not None
                and workspace_id is not None
                and server.workspace_id != workspace_id
            ):
                return None
            return server

    async def create_server(
        self,
        payload: dict[str, Any],
        *,
        user_id: int,
        workspace_id: int | None = None,
        session: AsyncSession | None = None,
    ) -> MCPServer:
        settings = self._settings()
        data = dict(payload or {})
        secret = data.pop("auth_token", None)
        headers = data.get("headers")
        encrypted_secret: bytes | None = None
        if secret and settings is not None:
            try:
                encrypted_secret = encrypt_value(settings, str(secret))
            except Exception as exc:  # noqa: BLE001
                raise MCPClientError(
                    f"无法加密访问令牌，请检查 CREDENTIAL_ENCRYPTION_KEY：{exc}"
                ) from exc
        server = MCPServer(
            user_id=user_id,
            workspace_id=workspace_id,
            name=str(data.get("name") or "").strip(),
            version=(
                str(data.get("version")).strip()
                if data.get("version")
                else None
            ),
            fingerprint=(
                str(data.get("fingerprint")).strip()
                if data.get("fingerprint")
                else None
            ),
            signature=(
                str(data.get("signature")).strip()
                if data.get("signature")
                else None
            ),
            description=data.get("description"),
            transport=str(data.get("transport") or "http").strip().lower(),
            url=(str(data.get("url")).strip() if data.get("url") else None),
            command=(
                str(data.get("command")).strip()
                if data.get("command")
                else None
            ),
            args=list(data.get("args") or []),
            env=dict(data.get("env") or {}),
            headers=dict(headers) if isinstance(headers, dict) else {},
            secret_encrypted=encrypted_secret,
            enabled=bool(data.get("enabled", True)),
            default_risk=normalize_risk_level(
                data.get("default_risk"),
                getattr(settings, "MCP_DEFAULT_RISK", "mutating"),
            ),
            timeout_seconds=data.get("timeout_seconds"),
            config=dict(data.get("config") or {}),
            tools=[],
        )
        if not server.name:
            raise ValueError("MCP Server 名称不能为空")
        async with self._session_scope(session) as db:
            db.add(server)
            await db.commit()
            await db.refresh(server)
        return server

    async def delete_server(
        self,
        reference: Any,
        *,
        user_id: int | None = None,
        workspace_id: int | None = None,
        session: AsyncSession | None = None,
    ) -> bool:
        async with self._session_scope(session) as db:
            server = await self.get_server(
                reference,
                user_id=user_id,
                workspace_id=workspace_id,
                session=db,
            )
            if server is None:
                return False
            await db.delete(server)
            await db.commit()
        self.client_pool.drop(reference)
        return True

    # ------------------------------------------------------------ 工具发现

    def _config_from_record(
        self,
        server: MCPServer,
        *,
        settings: Any | None = None,
        timeout: float | None = None,
    ) -> MCPServerConfig:
        resolved_settings = settings or self._settings()
        auth_token: str | None = None
        raw_secret = server.secret_encrypted
        if raw_secret and resolved_settings is not None:
            try:
                auth_token = decrypt_value(
                    resolved_settings,
                    bytes(raw_secret),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "无法解密 MCP Server %s 的密钥引用",
                    server.name,
                    exc_info=True,
                )
        config = MCPServerConfig(
            name=str(server.name),
            transport=str(server.transport or "http"),
            url=server.url,
            command=server.command,
            args=[str(item) for item in (server.args or [])],
            env={
                str(key): str(value)
                for key, value in (server.env or {}).items()
            },
            headers={
                str(key): str(value)
                for key, value in (server.headers or {}).items()
            },
            auth_token=auth_token,
            timeout=float(
                timeout
                or server.timeout_seconds
                or self.default_timeout()
            ),
            server_id=server.id,
            default_risk=str(server.default_risk or "mutating"),
            user_id=server.user_id,
        )
        config.validate()
        if config.transport == "stdio" and not self.stdio_enabled():
            raise MCPClientError(
                "stdio 传输未启用，请先设置 MCP_STDIO_ENABLED=true。",
                category=FailureCategory.UNKNOWN,
            )
        integrity = self._integrity_result(server, config)
        if not integrity.ok:
            raise MCPClientError(
                f"插件完整性校验失败：{integrity.reason}",
                category=FailureCategory.PERMISSION_DENIED,
            )
        return config

    def _integrity_result(self, server: MCPServer, config: MCPServerConfig):
        return verify_plugin_config(
            transport=config.transport,
            url=config.url,
            command=config.command,
            args=config.args,
            env=config.env,
            headers=config.headers,
            auth_token=config.auth_token,
            version=server.version,
            fingerprint=server.fingerprint,
            signature=server.signature,
            enforce=self.integrity_enabled(),
            require_signature=self.require_signature(),
            signing_secret=self.signing_secret(),
        )

    async def pin_fingerprint(
        self,
        reference: Any,
        *,
        version: str | None = None,
        sign: bool = False,
        user_id: int | None = None,
        workspace_id: int | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """计算并固定插件指纹（可选 HMAC 签名）。"""

        settings = self._settings()
        async with self._session_scope(session) as db:
            server = await self.get_server(
                reference,
                user_id=user_id,
                workspace_id=workspace_id,
                session=db,
            )
            if server is None:
                return {"success": False, "error": "MCP Server 不存在"}
            auth_token: str | None = None
            if server.secret_encrypted and settings is not None:
                try:
                    auth_token = decrypt_value(
                        settings,
                        bytes(server.secret_encrypted),
                    )
                except Exception:  # noqa: BLE001
                    auth_token = None
            fingerprint = compute_fingerprint(
                canonical_plugin_spec(
                    transport=server.transport,
                    url=server.url,
                    command=server.command,
                    args=list(server.args or []),
                    env=dict(server.env or {}),
                    headers=dict(server.headers or {}),
                    auth_token=auth_token,
                )
            )
            if version:
                server.version = str(version).strip()
            signature = None
            if sign:
                secret = self.signing_secret()
                if not secret:
                    return {
                        "success": False,
                        "error": "未配置 PLUGIN_SIGNING_SECRET",
                    }
                signature = compute_signature(fingerprint, secret)
            server.fingerprint = fingerprint
            server.signature = signature
            await db.commit()
            await db.refresh(server)
            return {
                "success": True,
                "server_id": server.id,
                "server": server.name,
                "version": server.version,
                "fingerprint": fingerprint,
                "signature": signature,
            }

    async def _discover_tools(
        self,
        server: MCPServer,
        *,
        settings: Any | None = None,
        timeout: float | None = None,
    ) -> tuple[list[dict[str, Any]], float, str]:
        resolved_settings = settings or self._settings()
        config = self._config_from_record(
            server,
            settings=resolved_settings,
            timeout=timeout,
        )
        client = self.client_pool.get(config)
        started_at = time.perf_counter()
        async with self.client_pool.semaphore:
            tools = await client.list_tools(timeout=config.timeout)
        return (
            tools,
            (time.perf_counter() - started_at) * 1000,
            client.transport_name,
        )

    async def test_server(
        self,
        reference: Any,
        *,
        user_id: int | None = None,
        workspace_id: int | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """健康检查 + 工具发现，并把工具清单写回 Server 记录。"""

        settings = self._settings()
        started_at = time.perf_counter()
        async with self._session_scope(session) as db:
            server = await self.get_server(
                reference,
                user_id=user_id,
                workspace_id=workspace_id,
                session=db,
            )
            if server is None:
                return {
                    "success": False,
                    "error": "MCP Server 不存在",
                    "tools": [],
                    "duration_ms": 0.0,
                }
            try:
                tools, duration_ms, transport = await self._discover_tools(
                    server,
                    settings=settings,
                )
            except MCPClientError as exc:
                server.last_error = str(exc)
                server.last_tested_at = _utcnow()
                await db.commit()
                return {
                    "success": False,
                    "error": str(exc),
                    "failure_category": exc.category.value,
                    "server_id": server.id,
                    "server": server.name,
                    "tools": [],
                    "duration_ms": (
                        time.perf_counter() - started_at
                    )
                    * 1000,
                }
            except Exception as exc:  # noqa: BLE001
                server.last_error = str(exc) or exc.__class__.__name__
                server.last_tested_at = _utcnow()
                await db.commit()
                return {
                    "success": False,
                    "error": server.last_error,
                    "server_id": server.id,
                    "server": server.name,
                    "tools": [],
                    "duration_ms": (
                        time.perf_counter() - started_at
                    )
                    * 1000,
                }

            server.tools = merge_tool_manifest(
                server.tools,
                tools,
                default_risk=str(
                    server.default_risk or "mutating"
                ),
            )
            self._apply_toolset_change(server)
            server.last_error = None
            server.last_tested_at = _utcnow()
            await db.commit()
            await db.refresh(server)
        return {
                "success": True,
                "server_id": server.id,
                "server": server.name,
                "transport": transport,
                "tool_count": len(server.tools or []),
                "tools": list(server.tools or []),
                "duration_ms": duration_ms,
            }

    @staticmethod
    def _apply_toolset_change(server: MCPServer) -> bool:
        """工具清单变化时重置授权，要求重新确认。"""

        fingerprint = compute_toolset_fingerprint(server.tools)
        previous = server.toolset_fingerprint
        if previous and previous != fingerprint:
            tools: list[dict[str, Any]] = []
            for item in server.tools or []:
                if not isinstance(item, dict):
                    continue
                record = dict(item)
                if record.get("authorized") or record.get("confirmed"):
                    record["authorized"] = False
                    record["confirmed"] = False
                    record["needs_reconfirmation"] = True
                tools.append(record)
            server.tools = tools
        server.toolset_fingerprint = fingerprint
        return bool(previous and previous != fingerprint)

    async def authorize_tool(
        self,
        reference: Any,
        tool: str,
        *,
        authorized: bool = True,
        confirmed: bool = False,
        risk_level: str | None = None,
        user_id: int | None = None,
        workspace_id: int | None = None,
        authorized_by: str | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        name = str(tool or "").strip()
        async with self._session_scope(session) as db:
            server = await self.get_server(
                reference,
                user_id=user_id,
                workspace_id=workspace_id,
                session=db,
            )
            if server is None:
                return {"success": False, "error": "MCP Server 不存在"}
            # 深拷贝一层：SQLAlchemy 的 JSON 列按值比较，原地改旧字典
            # 会被判定为“没有变化”，授权状态就落不了库。
            tools = [
                dict(item)
                for item in (server.tools or [])
                if isinstance(item, dict)
            ]
            record = find_tool_manifest(tools, name)
            if record is None:
                if not authorized:
                    return {
                        "success": False,
                        "error": f"工具 {name} 尚未发现，请先测试连接",
                    }
                record = {
                    "name": name,
                    "description": "",
                    "input_schema": {},
                    "annotations": {},
                    "risk_level": normalize_risk_level(
                        risk_level,
                        server.default_risk,
                    ),
                    "available": False,
                }
                tools.append(record)

            if risk_level:
                record["risk_level"] = normalize_risk_level(
                    risk_level,
                    server.default_risk,
                )
            record["authorized"] = bool(authorized)
            record["confirmed"] = bool(confirmed) if authorized else False
            record["authorized_at"] = (
                _utcnow().isoformat() if authorized else None
            )
            record["authorized_by"] = (
                (authorized_by or "user") if authorized else None
            )
            server.tools = tools
            await db.commit()
            await db.refresh(server)
            return {
                "success": True,
                "server_id": server.id,
                "tool": record,
            }

    # ---------------------------------------------------------------- 调用

    def _resolve_retry_policy(
        self,
        retry_policy: Any,
    ) -> tuple[int, float]:
        attempts = 1
        backoff = 0.0
        if isinstance(retry_policy, dict):
            try:
                attempts = max(
                    1,
                    min(5, int(retry_policy.get("max_attempts") or 1)),
                )
            except (TypeError, ValueError):
                attempts = 1
            try:
                backoff = max(
                    0.0,
                    min(30.0, float(retry_policy.get("backoff_seconds") or 0)),
                )
            except (TypeError, ValueError):
                backoff = 0.0
        return attempts, backoff

    async def call_tool(
        self,
        server_ref: Any,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
        timeout: float | None = None,
        retry_policy: Any = None,
        confirm: bool | None = None,
        session: AsyncSession | None = None,
    ) -> MCPCallOutcome:
        """执行一次 MCP 工具调用；权限检查与审计都在这里完成。"""

        request = dict(context or {})
        if confirm is None:
            confirm = bool(request.get("confirm"))
        started_at = time.perf_counter()
        settings = self._settings()
        secrets = audit_secrets(
            request.get("credentials"),
            {
                "auth_token": request.get("auth_token"),
            }
            if request.get("auth_token")
            else None,
        )
        workflow_id = _as_int(request.get("workflow_id"))
        execution_id = _as_int(request.get("execution_id"))
        user_id = _as_int(request.get("user_id"))
        workspace_id = _as_int(request.get("workspace_id"))
        tool_name = str(tool or "").strip()
        fallback_server = str(server_ref or "").strip()

        async def record_outcome(outcome: MCPCallOutcome, **kwargs: Any):
            return await self._record_outcome(
                outcome,
                workspace_id=workspace_id,
                **kwargs,
            )

        async with self._session_scope(session) as db:
            server = await self.get_server(
                server_ref,
                user_id=user_id,
                workspace_id=workspace_id,
                session=db,
            )
            if server is None:
                return await record_outcome(
                    MCPCallOutcome(
                        success=False,
                        server=fallback_server,
                        tool=tool_name,
                        decision="denied",
                        error=f"MCP Server 不存在：{fallback_server}",
                        error_category=FailureCategory.BUSINESS_ERROR,
                        duration_ms=(time.perf_counter() - started_at) * 1000,
                    ),
                    arguments=arguments,
                    secrets=secrets,
                    status="denied",
                    server_id=None,
                    workflow_id=workflow_id,
                    execution_id=execution_id,
                    user_id=user_id,
                    session=db,
                )

            if not bool(server.enabled):
                return await record_outcome(
                    MCPCallOutcome(
                        success=False,
                        server=str(server.name),
                        tool=tool_name,
                        decision="denied",
                        error=f"MCP Server {server.name} 已禁用",
                        error_category=FailureCategory.PERMISSION_DENIED,
                        duration_ms=(time.perf_counter() - started_at) * 1000,
                    ),
                    arguments=arguments,
                    secrets=secrets,
                    status="denied",
                    server_id=server.id,
                    workflow_id=workflow_id,
                    execution_id=execution_id,
                    user_id=user_id,
                    session=db,
                )

            record = find_tool_manifest(server.tools, tool_name)
            if record is None:
                # 清单里没有先做一次发现，未发现则直接拒绝。
                try:
                    discovered, _, _ = await self._discover_tools(
                        server,
                        settings=settings,
                        timeout=timeout,
                    )
                    server.tools = merge_tool_manifest(
                        server.tools,
                        discovered,
                        default_risk=str(server.default_risk),
                    )
                    await db.commit()
                    record = find_tool_manifest(
                        server.tools,
                        tool_name,
                    )
                except MCPClientError as exc:
                    return await record_outcome(
                        MCPCallOutcome(
                            success=False,
                            server=str(server.name),
                            tool=tool_name,
                            error=str(exc),
                            error_category=exc.category,
                            duration_ms=(
                                time.perf_counter() - started_at
                            )
                            * 1000,
                        ),
                        arguments=arguments,
                        secrets=secrets,
                        status=_status_for_category(exc.category),
                        server_id=server.id,
                        workflow_id=workflow_id,
                        execution_id=execution_id,
                        user_id=user_id,
                        session=db,
                    )

            if record is None:
                return await record_outcome(
                    MCPCallOutcome(
                        success=False,
                        server=str(server.name),
                        tool=tool_name,
                        error=f"工具清单里没有 {tool_name}",
                        error_category=FailureCategory.BUSINESS_ERROR,
                        duration_ms=(time.perf_counter() - started_at) * 1000,
                    ),
                    arguments=arguments,
                    secrets=secrets,
                    status="error",
                    server_id=server.id,
                    workflow_id=workflow_id,
                    execution_id=execution_id,
                    user_id=user_id,
                    session=db,
                )

            risk_level = normalize_risk_level(
                record.get("risk_level"),
                str(server.default_risk or "mutating"),
            )
            permission = evaluate_permission(
                risk_level,
                record,
                confirmed=bool(confirm),
                default=str(server.default_risk or "mutating"),
            )
            if not permission.allowed:
                return await record_outcome(
                    MCPCallOutcome(
                        success=False,
                        server=str(server.name),
                        tool=tool_name,
                        risk_level=risk_level,
                        decision="denied",
                        error=permission.reason,
                        error_category=FailureCategory.PERMISSION_DENIED,
                        duration_ms=(time.perf_counter() - started_at) * 1000,
                        requires_confirmation=(
                            permission.requires_confirmation
                        ),
                        authorized_by=permission.authorized_by,
                    ),
                    arguments=arguments,
                    secrets=secrets,
                    status="denied",
                    server_id=server.id,
                    workflow_id=workflow_id,
                    execution_id=execution_id,
                    user_id=user_id,
                    session=db,
                )

            try:
                config = self._config_from_record(
                    server,
                    settings=settings,
                    timeout=timeout,
                )
            except MCPClientError as exc:
                return await record_outcome(
                    MCPCallOutcome(
                        success=False,
                        server=str(server.name),
                        tool=tool_name,
                        risk_level=risk_level,
                        error=str(exc),
                        error_category=exc.category,
                        duration_ms=(time.perf_counter() - started_at) * 1000,
                    ),
                    arguments=arguments,
                    secrets=secrets,
                    status="error",
                    server_id=server.id,
                    workflow_id=workflow_id,
                    execution_id=execution_id,
                    user_id=user_id,
                    session=db,
                )

            if config.auth_token:
                secrets = audit_secrets(
                    secrets,
                    {"auth_token": config.auth_token},
                )

            client = self.client_pool.get(config)
            max_attempts, backoff = self._resolve_retry_policy(retry_policy)
            attempts = 0
            call = MCPToolCall(success=False)
            call_headers = _context_request_headers(request)
            while attempts < max_attempts:
                attempts += 1
                async with self.client_pool.semaphore:
                    kwargs: dict[str, Any] = {
                        "timeout": config.timeout,
                    }
                    if _accepts_keyword(client.call_tool, "headers"):
                        kwargs["headers"] = call_headers
                    call = await client.call_tool(
                        tool_name,
                        arguments,
                        **kwargs,
                    )
                if call.success:
                    break
                retryable = (
                    risk_level == RISK_READ_ONLY
                    and call.category in _RETRYABLE_CATEGORIES
                )
                if not retryable or attempts >= max_attempts:
                    break
                if backoff:
                    await asyncio.sleep(backoff)

            wrapped = wrap_untrusted_result(
                call.result,
                server=str(server.name),
                tool=tool_name,
            )
            outcome = MCPCallOutcome(
                success=call.success,
                server=str(server.name),
                tool=tool_name,
                risk_level=risk_level,
                decision=permission.decision,
                error=call.error,
                error_category=call.category,
                duration_ms=(time.perf_counter() - started_at) * 1000,
                attempts=attempts,
                result=wrapped,
                authorized_by=permission.authorized_by,
            )
            return await record_outcome(
                outcome,
                arguments=arguments,
                secrets=secrets,
                status=(
                    "success"
                    if call.success
                    else _status_for_category(call.category)
                ),
                result=wrapped,
                server_id=server.id,
                workflow_id=workflow_id,
                execution_id=execution_id,
                user_id=user_id,
                session=db,
            )

    async def _record_outcome(
        self,
        outcome: MCPCallOutcome,
        *,
        arguments: dict[str, Any] | None,
        secrets: list[str] | None,
        status: str,
        server_id: int | None,
        workflow_id: int | None,
        execution_id: int | None,
        user_id: int | None,
        workspace_id: int | None = None,
        result: Any = None,
        session: AsyncSession | None = None,
    ) -> MCPCallOutcome:
        audit_id = await self.audit.record(
            server_id=server_id,
            server_name=outcome.server,
            tool=outcome.tool,
            status=status,
            decision=outcome.decision,
            risk_level=outcome.risk_level,
            arguments=arguments,
            result=result,
            error=outcome.error,
            duration_ms=outcome.duration_ms,
            attempts=outcome.attempts,
            workflow_id=workflow_id,
            execution_id=execution_id,
            user_id=user_id,
            workspace_id=workspace_id,
            secrets=secrets,
            session=session,
        )
        outcome.audit_id = audit_id
        return outcome

    # ---------------------------------------------------------------- 审计

    async def list_audits(
        self,
        *,
        user_id: int | None = None,
        workspace_id: int | None = None,
        server_id: int | None = None,
        limit: int = 50,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        try:
            resolved_limit = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            resolved_limit = 50
        async with self._session_scope(session) as db:
            statement = (
                select(ToolCallAudit)
                .order_by(ToolCallAudit.id.desc())
                .limit(resolved_limit)
            )
            if user_id is not None:
                statement = statement.where(
                    ToolCallAudit.user_id == user_id
                )
            if workspace_id is not None:
                statement = statement.where(
                    ToolCallAudit.workspace_id == workspace_id
                )
            if server_id is not None:
                statement = statement.where(
                    ToolCallAudit.server_id == server_id
                )
            result = await db.execute(statement)
            return [
                _audit_to_payload(item)
                for item in result.scalars().all()
            ]

    async def aclose(self) -> None:
        await self.client_pool.aclose()


def _audit_to_payload(item: ToolCallAudit) -> dict[str, Any]:
    return {
        "id": item.id,
        "server_id": item.server_id,
        "server": item.server_name,
        "tool": item.tool,
        "risk_level": item.risk_level,
        "decision": item.decision,
        "status": item.status,
        "arguments": item.arguments_summary,
        "result": item.result_summary,
        "error": item.error,
        "duration_ms": item.duration_ms,
        "attempts": item.attempts,
        "workflow_id": item.workflow_id,
        "execution_id": item.execution_id,
        "user_id": item.user_id,
        "workspace_id": item.workspace_id,
        "created_at": (
            item.created_at.isoformat() if item.created_at else None
        ),
    }


def _status_for_category(category: FailureCategory) -> str:
    if category == FailureCategory.TIMEOUT:
        return "timeout"
    if category == FailureCategory.PERMISSION_DENIED:
        return "denied"
    if category == FailureCategory.AUTH_REQUIRED:
        return "denied"
    return "error"


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _context_request_headers(context: dict[str, Any]) -> dict[str, str]:
    mapping = {
        "workflow_id": "X-NL-RPA-Workflow-Id",
        "execution_id": "X-NL-RPA-Execution-Id",
        "request_id": "X-NL-RPA-Request-Id",
        "confirmed": "X-NL-RPA-Confirmed",
    }
    headers: dict[str, str] = {}
    for key, header in mapping.items():
        value = context.get(key)
        if value is None or value == "":
            continue
        if key == "confirmed":
            enabled = (
                str(value).strip().lower() in {"1", "true", "yes", "on"}
                if isinstance(value, str)
                else bool(value)
            )
            headers[header] = "true" if enabled else "false"
        else:
            headers[header] = str(value)
    return headers


def _accepts_keyword(callable_value: Any, name: str) -> bool:
    try:
        signature = inspect.signature(callable_value)
    except (TypeError, ValueError):
        return False
    return (
        name in signature.parameters
        or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
    )


__all__ = [
    "MCPCallOutcome",
    "MCPRegistry",
    "MCPToolInfo",
    "find_tool_manifest",
    "merge_tool_manifest",
]
