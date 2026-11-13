"""把平台有限工具暴露为 MCP Server（白名单 + 权限校验）。

只暴露白名单里的只读工具；``platform_run_workflow`` 属于 mutating，
必须同时满足 ``MCP_SERVER_ALLOW_MUTATING=true`` 与调用参数
``confirm=true`` 才会执行。没有模型参与这里的权限判断。
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import AsyncSessionLocal
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow

logger = logging.getLogger(__name__)

SERVER_NAME = "nl-rpa-platform"
SERVER_VERSION = "0.4.0"
PROTOCOL_VERSION = "2025-03-26"


class MCPPlatformServer:
    """处理 MCP JSON-RPC 请求，工具都限定在当前登录用户范围内。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        start_workflow: Callable[[int], Awaitable[Any]] | None = None,
        allow_mutating: bool = False,
    ) -> None:
        self.session_factory = session_factory or AsyncSessionLocal
        self.start_workflow = start_workflow
        self.allow_mutating = bool(allow_mutating)

    # ------------------------------------------------------------ JSON-RPC

    async def handle(
        self,
        payload: dict[str, Any],
        *,
        user: User,
        db: AsyncSession | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return self._error(None, -32600, "Invalid Request")
        method = str(payload.get("method") or "")
        request_id = payload.get("id")

        if method.startswith("notifications/"):
            return None
        if not method:
            return self._error(request_id, -32600, "Invalid Request")

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tool_definitions()}
            elif method == "tools/call":
                params = payload.get("params")
                params = params if isinstance(params, dict) else {}
                result = await self._call_tool(
                    str(params.get("name") or ""),
                    params.get("arguments"),
                    user=user,
                    db=db,
                )
            else:
                return self._error(
                    request_id,
                    -32601,
                    f"Method not found: {method}",
                )
        except PermissionError as exc:
            return self._error(request_id, -32003, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP Server 工具调用失败：%s", method)
            return self._error(
                request_id,
                -32603,
                str(exc) or exc.__class__.__name__,
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
        *,
        user: User,
        db: AsyncSession | None,
    ) -> dict[str, Any]:
        args = arguments if isinstance(arguments, dict) else {}
        definition = _definition(name)
        if definition is None:
            raise PermissionError(f"工具不在白名单中：{name}")
        if definition["risk_level"] == "mutating":
            if not self.allow_mutating:
                raise PermissionError(
                    "平台未开启 mutating 工具导出"
                    "（MCP_SERVER_ALLOW_MUTATING=false）"
                )
            if args.get("confirm") is not True:
                raise PermissionError(
                    "mutating 工具需要 confirm=true 才能执行"
                )

        if name == "platform_health":
            data = {"status": "ok", "tools": len(tool_definitions())}
        elif name == "platform_list_workflows":
            data = await self._list_workflows(user=user, db=db)
        elif name == "platform_get_execution":
            data = await self._get_execution(
                args,
                user=user,
                db=db,
            )
        elif name == "platform_run_workflow":
            data = await self._run_workflow(args, user=user, db=db)
        else:  # pragma: no cover - 白名单已覆盖
            raise PermissionError(f"工具未实现：{name}")

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(data, ensure_ascii=False),
                }
            ],
            "structuredContent": data,
            "isError": False,
        }

    async def _list_workflows(
        self,
        *,
        user: User,
        db: AsyncSession | None,
    ) -> dict[str, Any]:
        async with self._session_scope(db) as session:
            result = await session.execute(
                select(Workflow)
                .where(Workflow.user_id == user.id)
                .order_by(Workflow.id)
            )
            return {
                "workflows": [
                    {
                        "id": workflow.id,
                        "name": workflow.name,
                        "status": workflow.status,
                        "executor_type": workflow.executor_type,
                    }
                    for workflow in result.scalars().all()
                ]
            }

    async def _get_execution(
        self,
        args: dict[str, Any],
        *,
        user: User,
        db: AsyncSession | None,
    ) -> dict[str, Any]:
        try:
            execution_id = int(args.get("execution_id"))
        except (TypeError, ValueError) as exc:
            raise PermissionError("execution_id 必须是整数") from exc
        async with self._session_scope(db) as session:
            execution = await session.get(ExecutionLog, execution_id)
            if execution is None:
                raise PermissionError("执行记录不存在")
            workflow = await session.get(
                Workflow,
                execution.workflow_id,
            )
            if workflow is None or workflow.user_id != user.id:
                raise PermissionError("执行记录不属于当前用户")
            return {
                "id": execution.id,
                "workflow_id": execution.workflow_id,
                "status": execution.status,
                "error": execution.error_message,
                "step_count": len(execution.step_logs or []),
                "started_at": (
                    execution.started_at.isoformat()
                    if execution.started_at
                    else None
                ),
                "finished_at": (
                    execution.finished_at.isoformat()
                    if execution.finished_at
                    else None
                ),
            }

    async def _run_workflow(
        self,
        args: dict[str, Any],
        *,
        user: User,
        db: AsyncSession | None,
    ) -> dict[str, Any]:
        if self.start_workflow is None:
            raise PermissionError("平台未配置执行管理器")
        try:
            workflow_id = int(args.get("workflow_id"))
        except (TypeError, ValueError) as exc:
            raise PermissionError("workflow_id 必须是整数") from exc
        async with self._session_scope(db) as session:
            workflow = await session.get(Workflow, workflow_id)
            if workflow is None or workflow.user_id != user.id:
                raise PermissionError("流程不存在或不属于当前用户")
        execution = await self.start_workflow(workflow_id)
        return {
            "execution_id": getattr(execution, "id", None),
            "workflow_id": workflow_id,
            "status": getattr(execution, "status", "running"),
        }

    @asynccontextmanager
    async def _session_scope(
        self,
        db: AsyncSession | None,
    ) -> AsyncIterator[AsyncSession]:
        if db is not None:
            yield db
            return
        async with self.session_factory() as session:
            yield session

    @staticmethod
    def _error(
        request_id: Any,
        code: int,
        message: str,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": definition["name"],
            "description": definition["description"],
            "inputSchema": definition["input_schema"],
            "annotations": {
                "readOnlyHint": definition["risk_level"] == "read_only",
                "destructiveHint": False,
            },
        }
        for definition in PLATFORM_TOOLS
    ]


def _definition(name: str) -> dict[str, Any] | None:
    for definition in PLATFORM_TOOLS:
        if definition["name"] == name:
            return definition
    return None


PLATFORM_TOOLS: list[dict[str, Any]] = [
    {
        "name": "platform_health",
        "description": "平台健康状态与可用工具数量",
        "risk_level": "read_only",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "platform_list_workflows",
        "description": "列出当前用户的流程",
        "risk_level": "read_only",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "platform_get_execution",
        "description": "读取当前用户的一次执行状态",
        "risk_level": "read_only",
        "input_schema": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "integer"},
            },
            "required": ["execution_id"],
        },
    },
    {
        "name": "platform_run_workflow",
        "description": "触发一次流程执行（需要确认）",
        "risk_level": "mutating",
        "input_schema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "integer"},
                "confirm": {"type": "boolean"},
            },
            "required": ["workflow_id", "confirm"],
        },
    },
]


__all__ = [
    "MCPPlatformServer",
    "PLATFORM_TOOLS",
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "tool_definitions",
]
