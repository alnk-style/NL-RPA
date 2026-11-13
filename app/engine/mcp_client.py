"""MCP 客户端：官方 SDK 优先，内置 HTTP 传输兜底。

只支持平台当前的两种传输：
- ``http``：Streamable HTTP（官方 SDK 的 ``streamablehttp_client``，
  未安装 SDK 时退化为等价的 JSON-RPC/HTTP 实现）；
- ``stdio``：本地子进程（需要显式打开 ``MCP_STDIO_ENABLED``）。

每次调用建立一次会话（streamable HTTP 天然支持无状态），避免跨
asyncio 任务复用 anyio task group；连接池负责复用 HTTP 客户端并按
``MCP_MAX_CONCURRENCY`` 限流。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.protocols import FailureCategory

logger = logging.getLogger(__name__)

CLIENT_NAME = "nl-rpa-platform"
CLIENT_VERSION = "0.4.0"
# 与官方 SDK 支持的最新协议版本保持一致，内置传输也用同一版本。
PROTOCOL_VERSION = "2025-03-26"

try:  # pragma: no cover - 依赖安装情况由环境决定
    from mcp import ClientSession as _SDKClientSession

    _SDK_IMPORT_ERROR: str | None = None
except Exception as exc:  # noqa: BLE001
    _SDKClientSession = None
    _SDK_IMPORT_ERROR = str(exc) or exc.__class__.__name__


def sdk_available() -> bool:
    return _SDKClientSession is not None


class MCPClientError(RuntimeError):
    """带确定性失败分类的 MCP 客户端错误。"""

    def __init__(
        self,
        message: str,
        *,
        category: FailureCategory = FailureCategory.UNKNOWN,
    ) -> None:
        super().__init__(message)
        self.category = category


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "http"
    url: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    auth_token: str | None = None
    timeout: float = 30.0
    server_id: int | None = None
    default_risk: str = "mutating"
    user_id: int | None = None

    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any] | None,
        *,
        server_id: int | None = None,
        timeout: float | None = None,
        default_risk: str | None = None,
        auth_token: str | None = None,
    ) -> MCPServerConfig:
        payload = dict(data) if isinstance(data, dict) else {}
        raw_args = payload.get("args")
        raw_env = payload.get("env")
        raw_headers = payload.get("headers")
        resolved_timeout = timeout
        if resolved_timeout is None:
            candidate = payload.get("timeout_seconds")
            if isinstance(candidate, (int, float)) and candidate > 0:
                resolved_timeout = float(candidate)
        return cls(
            name=str(payload.get("name") or "").strip(),
            transport=str(payload.get("transport") or "http").strip().lower(),
            url=(str(payload.get("url")).strip() if payload.get("url") else None),
            command=(
                str(payload.get("command")).strip()
                if payload.get("command")
                else None
            ),
            args=[str(item) for item in raw_args]
            if isinstance(raw_args, list)
            else [],
            env={
                str(key): str(value)
                for key, value in raw_env.items()
            }
            if isinstance(raw_env, dict)
            else {},
            headers={
                str(key): str(value)
                for key, value in raw_headers.items()
            }
            if isinstance(raw_headers, dict)
            else {},
            auth_token=auth_token,
            timeout=float(resolved_timeout or 30.0),
            server_id=server_id
            if server_id is not None
            else _coerce_int(payload.get("server_id") or payload.get("id")),
            default_risk=str(
                default_risk
                or payload.get("default_risk")
                or "mutating"
            ),
            user_id=_coerce_int(payload.get("user_id")),
        )

    def validate(self) -> None:
        if not self.name:
            raise MCPClientError(
                "MCP Server 缺少 name",
                category=FailureCategory.UNKNOWN,
            )
        if self.transport not in {"http", "stdio"}:
            raise MCPClientError(
                f"不支持的 MCP 传输方式：{self.transport}",
                category=FailureCategory.UNKNOWN,
            )
        if self.transport == "http" and not self.url:
            raise MCPClientError(
                "HTTP 传输需要配置 url",
                category=FailureCategory.UNKNOWN,
            )
        if self.transport == "stdio" and not self.command:
            raise MCPClientError(
                "stdio 传输需要配置 command",
                category=FailureCategory.UNKNOWN,
            )

    def request_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self.headers,
        }
        if self.auth_token:
            headers.setdefault(
                "Authorization",
                f"Bearer {self.auth_token}",
            )
        return headers


@dataclass
class MCPToolCall:
    success: bool
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    category: FailureCategory = FailureCategory.UNKNOWN
    duration_ms: float = 0.0
    attempts: int = 1


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def map_mcp_error(exc: BaseException) -> MCPClientError:
    """把传输/协议错误映射成稳定的失败分类。"""

    if isinstance(exc, MCPClientError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return MCPClientError(
            "MCP 调用超时",
            category=FailureCategory.TIMEOUT,
        )
    if isinstance(exc, httpx.TimeoutException):
        return MCPClientError(
            f"MCP HTTP 请求超时：{exc}",
            category=FailureCategory.TIMEOUT,
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        category = FailureCategory.UNKNOWN
        if status == 401:
            category = FailureCategory.AUTH_REQUIRED
        elif status == 403:
            category = FailureCategory.PERMISSION_DENIED
        elif status == 404:
            category = FailureCategory.BUSINESS_ERROR
        elif status >= 500:
            category = FailureCategory.BUSINESS_ERROR
        return MCPClientError(
            f"MCP 服务返回 HTTP {status}",
            category=category,
        )
    if isinstance(exc, httpx.TransportError):
        return MCPClientError(
            f"无法连接 MCP 服务：{exc}",
            category=FailureCategory.UNKNOWN,
        )
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    if "timeout" in lowered or "timed out" in lowered:
        category = FailureCategory.TIMEOUT
    elif "401" in lowered or "unauthorized" in lowered:
        category = FailureCategory.AUTH_REQUIRED
    elif "403" in lowered or "forbidden" in lowered:
        category = FailureCategory.PERMISSION_DENIED
    elif "connect" in lowered or "refused" in lowered:
        category = FailureCategory.BUSINESS_ERROR
    else:
        category = FailureCategory.UNKNOWN
    return MCPClientError(text, category=category)


def _dump_model(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            data = dump(mode="json")
        except TypeError:
            data = dump()
        if isinstance(data, dict):
            return data
    return {}


def _normalize_call_result(result: Any) -> dict[str, Any]:
    """把 SDK 的 CallToolResult 归一化成平台内部结构。"""

    payload = _dump_model(result)
    content = payload.get("content")
    if not isinstance(content, list):
        content = []
    normalized_content = [_dump_model(item) or item for item in content]
    text_parts = [
        str(item.get("text"))
        for item in normalized_content
        if isinstance(item, dict) and item.get("text") is not None
    ]
    is_error = bool(
        payload.get("isError")
        or payload.get("is_error")
        or payload.get("isError") is True
    )
    return {
        "content": normalized_content,
        "text": "\n".join(text_parts),
        "structured": payload.get("structuredContent"),
        "is_error": is_error,
        "raw": payload,
    }


class _HTTPTransport:
    """内置 Streamable HTTP 传输（JSON-RPC 2.0 + SSE 兜底解析）。"""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._owns_client = client is None
        self._session_id: str | None = None
        self._request_id = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout),
            )
            self._owns_client = True
        return self._client

    async def _post(
        self,
        payload: dict[str, Any],
        *,
        timeout: float,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        client = await self._get_client()
        request_headers = self.config.request_headers()
        if headers:
            request_headers.update(
                {
                    str(key): str(value)
                    for key, value in headers.items()
                }
            )
        if self._session_id:
            request_headers["Mcp-Session-Id"] = self._session_id
        response = await client.post(
            self.config.url or "",
            json=payload,
            headers=request_headers,
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"MCP 服务返回 HTTP {response.status_code}",
                request=response.request,
                response=response,
            )
        session_id = response.headers.get("Mcp-Session-Id") or response.headers.get(
            "mcp-session-id"
        )
        if session_id:
            self._session_id = session_id
        return self._parse_body(response)

    @staticmethod
    def _parse_body(response: httpx.Response) -> dict[str, Any] | None:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return _parse_sse_payload(response.text)
        text = response.text.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MCPClientError(
                f"MCP 返回了非 JSON 内容：{text[:200]}",
                category=FailureCategory.BUSINESS_ERROR,
            ) from exc
        return data if isinstance(data, dict) else None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_id = self._next_id()
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params:
            payload["params"] = params
        response = await self._post(
            payload,
            timeout=timeout,
            headers=headers,
        )
        if not isinstance(response, dict):
            raise MCPClientError(
                f"MCP 方法 {method} 没有返回结果",
                category=FailureCategory.BUSINESS_ERROR,
            )
        error = response.get("error")
        if isinstance(error, dict):
            raise MCPClientError(
                str(error.get("message") or error),
                category=_category_from_jsonrpc_error(error),
            )
        return response.get("result")

    async def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float,
    ) -> None:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            payload["params"] = params
        await self._post(payload, timeout=timeout)

    async def initialize(self, *, timeout: float) -> dict[str, Any]:
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": CLIENT_NAME,
                    "version": CLIENT_VERSION,
                },
            },
            timeout=timeout,
        )
        try:
            await self.notify("notifications/initialized", timeout=timeout)
        except Exception:  # noqa: BLE001
            logger.debug("MCP initialized 通知发送失败", exc_info=True)
        return result if isinstance(result, dict) else {}

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None


def _category_from_jsonrpc_error(error: dict[str, Any]) -> FailureCategory:
    code = error.get("code")
    try:
        code_int = int(code)
    except (TypeError, ValueError):
        code_int = 0
    if code_int == -32601:
        return FailureCategory.BUSINESS_ERROR
    if code_int == -32602:
        return FailureCategory.BUSINESS_ERROR
    message = str(error.get("message") or "").lower()
    if "permission" in message or "forbidden" in message or "denied" in message:
        return FailureCategory.PERMISSION_DENIED
    if "auth" in message or "unauthorized" in message:
        return FailureCategory.AUTH_REQUIRED
    return FailureCategory.BUSINESS_ERROR


def _parse_sse_payload(text: str) -> dict[str, Any] | None:
    """从 SSE 响应里取第一条 JSON-RPC 消息。"""

    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
            continue
        if not line.strip() and data_lines:
            payload = "\n".join(data_lines)
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                data_lines = []
                continue
            if isinstance(parsed, dict):
                return parsed
            data_lines = []
    if data_lines:
        try:
            parsed = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


class MCPClient:
    """一个 MCP Server 的客户端句柄。"""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        timeout: float | None = None,
        prefer_sdk: bool = True,
    ) -> None:
        self.config = config
        self.timeout = float(timeout or config.timeout or 30.0)
        self.prefer_sdk = prefer_sdk
        self._http: _HTTPTransport | None = None

    @property
    def transport_name(self) -> str:
        if (
            self.prefer_sdk
            and self.config.transport in {"http", "stdio"}
            and sdk_available()
        ):
            return f"sdk:{self.config.transport}"
        return f"builtin:{self.config.transport}"

    async def list_tools(self, *, timeout: float | None = None) -> list[dict[str, Any]]:
        resolved = float(timeout or self.timeout)

        async def operation() -> list[dict[str, Any]]:
            if self.prefer_sdk and sdk_available():
                result = await self._sdk_request("tools/list", {}, timeout=resolved)
            else:
                transport = await self._builtin()
                await transport.initialize(timeout=resolved)
                result = await transport.request(
                    "tools/list",
                    {},
                    timeout=resolved,
                )
            payload = _dump_model(result)
            tools = payload.get("tools")
            if not isinstance(tools, list):
                return []
            return [_dump_model(tool) for tool in tools]

        return await self._guard(operation(), resolved)

    async def call_tool(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> MCPToolCall:
        resolved = float(timeout or self.timeout)
        started_at = time.perf_counter()
        payload = dict(arguments) if isinstance(arguments, dict) else {}

        async def operation() -> dict[str, Any]:
            if self.prefer_sdk and sdk_available():
                result = await self._sdk_request(
                    "tools/call",
                    {"name": tool, "arguments": payload},
                    timeout=resolved,
                    request_headers=headers,
                )
            else:
                transport = await self._builtin()
                await transport.initialize(timeout=resolved)
                result = await transport.request(
                    "tools/call",
                    {"name": tool, "arguments": payload},
                    timeout=resolved,
                    headers=headers,
                )
            return _normalize_call_result(result)

        try:
            result = await self._guard(operation(), resolved)
            return MCPToolCall(
                success=not bool(result.get("is_error")),
                result=result,
                error=(
                    result.get("text") or "MCP 工具返回了错误"
                    if result.get("is_error")
                    else None
                ),
                category=(
                    FailureCategory.BUSINESS_ERROR
                    if result.get("is_error")
                    else FailureCategory.UNKNOWN
                ),
                duration_ms=(time.perf_counter() - started_at) * 1000,
            )
        except MCPClientError as exc:
            return MCPToolCall(
                success=False,
                error=str(exc),
                category=exc.category,
                duration_ms=(time.perf_counter() - started_at) * 1000,
            )

    async def ping(self, *, timeout: float | None = None) -> bool:
        resolved = float(timeout or min(self.timeout, 10.0))
        try:
            await self._guard(self._ping_operation(resolved), resolved)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _ping_operation(self, timeout: float) -> None:
        if self.prefer_sdk and sdk_available():
            await self._sdk_request("ping", {}, timeout=timeout)
            return
        transport = await self._builtin()
        await transport.initialize(timeout=timeout)
        await transport.request("ping", {}, timeout=timeout)

    async def _builtin(self) -> _HTTPTransport:
        if self.config.transport == "stdio":
            raise MCPClientError(
                "内置传输不支持 stdio，请安装官方 mcp SDK；"
                "stdio 需要显式开启 MCP_STDIO_ENABLED。",
                category=FailureCategory.UNKNOWN,
            )
        if self._http is None:
            self._http = _HTTPTransport(self.config)
        return self._http

    async def _guard(self, awaitable: Any, timeout: float) -> Any:
        try:
            return await asyncio.wait_for(awaitable, timeout=max(timeout, 0.01))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            raise map_mcp_error(exc) from exc

    async def _sdk_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        request_headers: dict[str, str] | None = None,
    ) -> Any:
        """用官方 SDK 建立一次会话并执行单个请求。"""

        if self.config.transport == "stdio":
            return await self._sdk_stdio_request(method, params, timeout)
        return await self._sdk_http_request(
            method,
            params,
            timeout,
            request_headers=request_headers,
        )

    async def _sdk_http_request(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float,
        *,
        request_headers: dict[str, str] | None = None,
    ) -> Any:
        from mcp.client.streamable_http import streamablehttp_client

        headers = self.config.request_headers()
        if request_headers:
            headers.update(
                {
                    str(key): str(value)
                    for key, value in request_headers.items()
                }
            )
        async with streamablehttp_client(
            self.config.url or "",
            headers=headers,
            timeout=timeout,
        ) as (read_stream, write_stream, _):
            async with _SDKClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await self._sdk_call(session, method, params, timeout)

    async def _sdk_stdio_request(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float,
    ) -> Any:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        server = StdioServerParameters(
            command=self.config.command or "",
            args=list(self.config.args),
            env=dict(self.config.env) or None,
        )
        async with stdio_client(server) as (read_stream, write_stream):
            async with _SDKClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await self._sdk_call(session, method, params, timeout)

    @staticmethod
    async def _sdk_call(
        session: Any,
        method: str,
        params: dict[str, Any],
        timeout: float,
    ) -> Any:
        if method == "tools/list":
            result = await session.list_tools()
        elif method == "tools/call":
            result = await session.call_tool(
                params.get("name") or "",
                arguments=params.get("arguments") or {},
            )
        elif method == "ping":
            result = await session.send_ping()
        else:  # pragma: no cover - 内部只使用以上方法
            raise MCPClientError(
                f"不支持的 MCP 方法：{method}",
                category=FailureCategory.BUSINESS_ERROR,
            )
        return result

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None


class MCPClientPool:
    """按 Server 复用客户端，并按全局并发上限限流。"""

    def __init__(
        self,
        *,
        max_concurrency: int = 2,
        prefer_sdk: bool = True,
    ) -> None:
        try:
            limit = max(1, int(max_concurrency))
        except (TypeError, ValueError):
            limit = 1
        self._semaphore = asyncio.Semaphore(limit)
        self._clients: dict[str, MCPClient] = {}
        self._prefer_sdk = prefer_sdk

    def get(self, config: MCPServerConfig) -> MCPClient:
        config.validate()
        key = str(config.server_id or config.name)
        client = self._clients.get(key)
        if client is None or client.config != config:
            client = MCPClient(config, prefer_sdk=self._prefer_sdk)
            self._clients[key] = client
        return client

    def drop(self, key: Any) -> None:
        text = str(key)
        for client_key in list(self._clients):
            if client_key == text or self._clients[client_key].config.name == text:
                self._clients.pop(client_key, None)

    @property
    def semaphore(self) -> asyncio.Semaphore:
        return self._semaphore

    async def aclose(self) -> None:
        for client in self._clients.values():
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                continue
        self._clients.clear()


__all__ = [
    "CLIENT_NAME",
    "CLIENT_VERSION",
    "MCPClient",
    "MCPClientError",
    "MCPClientPool",
    "MCPServerConfig",
    "MCPToolCall",
    "PROTOCOL_VERSION",
    "map_mcp_error",
    "sdk_available",
]
