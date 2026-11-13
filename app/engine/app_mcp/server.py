"""Generate a standard MCP server from one App Runtime."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.engine.app_mcp.contracts import (
    ActionResult,
    ToolCallContext,
    sanitize_message,
)
from app.engine.app_mcp.manifest import ToolSpec
from app.engine.app_mcp.runtime import AppRuntime

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-03-26"


class AppMCPServer:
    """MCP JSON-RPC handler backed by one manifest and adapter."""

    def __init__(
        self,
        runtime: AppRuntime,
        *,
        protocol_version: str = PROTOCOL_VERSION,
    ) -> None:
        self.runtime = runtime
        self.protocol_version = protocol_version

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [_tool_definition(tool) for tool in self.runtime.manifest.tools]

    async def handle(
        self,
        payload: dict[str, Any],
        context: ToolCallContext,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return _error(None, -32600, "Invalid Request")
        method = str(payload.get("method") or "")
        request_id = payload.get("id")
        if method.startswith("notifications/"):
            return None
        if not method:
            return _error(request_id, -32600, "Invalid Request")

        try:
            if method == "initialize":
                result: Any = {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": self.runtime.manifest.id,
                        "version": self.runtime.manifest.version,
                    },
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tool_definitions()}
            elif method == "tools/call":
                params = payload.get("params")
                params = params if isinstance(params, dict) else {}
                result = await self._call_tool(
                    str(params.get("name") or ""),
                    params.get("arguments"),
                    context,
                )
            else:
                return _error(
                    request_id,
                    -32601,
                    f"Method not found: {method}",
                )
        except Exception:
            logger.exception(
                "App MCP request failed: app=%s method=%s",
                self.runtime.manifest.id,
                method,
            )
            return _error(
                request_id,
                -32603,
                "Internal MCP server error",
            )

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }

    async def _call_tool(
        self,
        name: str,
        arguments: Any,
        context: ToolCallContext,
    ) -> dict[str, Any]:
        args = dict(arguments) if isinstance(arguments, dict) else {}
        result = await self.runtime.execute_tool(name, args, context)
        return _call_result(self.runtime, result, context)


def create_http_app(
    server: AppMCPServer,
    *,
    default_context: ToolCallContext | None = None,
) -> FastAPI:
    """Create a local Streamable HTTP endpoint at ``POST /mcp``."""

    app = FastAPI(
        title=f"{server.runtime.manifest.name} App MCP",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.post("/mcp")
    async def mcp_endpoint(
        payload: dict[str, Any],
        request: Request,
    ) -> Response:
        context = _request_context(request, default_context)
        result = await server.handle(payload, context)
        if result is None:
            return Response(status_code=202)
        return JSONResponse(result)

    @app.delete("/mcp")
    async def close_session() -> Response:
        return Response(status_code=200)

    @app.get("/mcp")
    async def mcp_info() -> dict[str, Any]:
        return {
            "transport": "streamable-http",
            "protocol_version": server.protocol_version,
            "runtime": {"pid": os.getpid()},
            "server": {
                "name": server.runtime.manifest.id,
                "version": server.runtime.manifest.version,
            },
        }

    return app


def _tool_definition(tool: ToolSpec) -> dict[str, Any]:
    read_only = tool.risk_level == "read_only"
    destructive = tool.risk_level == "destructive"
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": dict(tool.input_schema),
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": read_only,
            "openWorldHint": False,
        },
        "_meta": {
            "risk_level": tool.risk_level,
            "output_description": tool.output_description,
        },
    }


def _call_result(
    runtime: AppRuntime,
    result: ActionResult,
    context: ToolCallContext,
) -> dict[str, Any]:
    envelope = result.to_envelope(
        app_id=runtime.manifest.id,
        app_version=runtime.manifest.version,
        context=context,
    )
    text = (
        sanitize_message(result.message)
        if not result.success and result.message
        else json.dumps(envelope, ensure_ascii=False, sort_keys=True)
    )
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": envelope,
        "isError": not result.success,
    }


def _request_context(
    request: Request,
    default: ToolCallContext | None,
) -> ToolCallContext:
    base = default or ToolCallContext()
    return ToolCallContext(
        request_id=(
            request.headers.get("x-nl-rpa-request-id") or base.request_id or uuid4().hex
        ),
        user_id=_header_int(
            request.headers.get("x-nl-rpa-user-id"),
            base.user_id,
        ),
        workspace_id=_header_int(
            request.headers.get("x-nl-rpa-workspace-id"),
            base.workspace_id,
        ),
        workflow_id=_header_int(
            request.headers.get("x-nl-rpa-workflow-id"),
            base.workflow_id,
        ),
        execution_id=_header_int(
            request.headers.get("x-nl-rpa-execution-id"),
            base.execution_id,
        ),
        confirmed=_header_bool(
            request.headers.get("x-nl-rpa-confirmed"),
            base.confirmed,
        ),
    )


def _header_int(value: str | None, fallback: int | None) -> int | None:
    if value is None or not str(value).strip():
        return fallback
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _header_bool(value: str | None, fallback: bool) -> bool:
    if value is None:
        return fallback
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": int(code),
            "message": sanitize_message(message),
        },
    }


__all__ = [
    "PROTOCOL_VERSION",
    "AppMCPServer",
    "create_http_app",
]
