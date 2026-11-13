"""把 MCP 注册表包装成统一 Connector。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.engine.connectors.base import Connector, connector_result

if TYPE_CHECKING:
    from app.engine.mcp_registry import MCPRegistry


class MCPConnector(Connector):
    name = "mcp"
    kind = "mcp"

    def __init__(self, registry: "MCPRegistry") -> None:
        self.registry = registry

    async def capabilities(self) -> dict[str, Any]:
        servers = await self.registry.list_servers()
        payload = []
        for server in servers:
            tools = [
                {
                    "name": item.get("name"),
                    "risk_level": item.get("risk_level"),
                    "authorized": bool(item.get("authorized")),
                    "confirmed": bool(item.get("confirmed")),
                    "available": item.get("available", True),
                }
                for item in (server.tools or [])
                if isinstance(item, dict)
            ]
            payload.append(
                {
                    "id": server.id,
                    "name": server.name,
                    "transport": server.transport,
                    "enabled": bool(server.enabled),
                    "tools": tools,
                }
            )
        return connector_result(
            True,
            data={
                "client_enabled": self.registry.enabled,
                "sdk_available": _sdk_available(),
                "servers": payload,
            },
        )

    async def observe(
        self,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = dict(context or {})
        reference = request.get("server") or request.get("server_id")
        servers = await self.registry.list_servers(
            user_id=request.get("user_id"),
        )
        if reference is not None:
            server = await self.registry.get_server(
                reference,
                user_id=request.get("user_id"),
            )
            if server is None:
                return connector_result(
                    False,
                    error=f"MCP Server 不存在：{reference}",
                )
            return connector_result(
                True,
                data={
                    "server": server.name,
                    "transport": server.transport,
                    "tools": list(server.tools or []),
                    "last_error": server.last_error,
                },
            )
        return connector_result(
            True,
            data={
                "servers": [
                    {
                        "id": server.id,
                        "name": server.name,
                        "transport": server.transport,
                        "tool_count": len(server.tools or []),
                        "last_error": server.last_error,
                    }
                    for server in servers
                ]
            },
        )

    async def act(
        self,
        action: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(action or {})
        action_type = str(
            payload.get("action") or payload.get("type") or "call_tool"
        ).strip()
        if action_type not in {"call_tool", "call", "tool"}:
            return connector_result(
                False,
                error=f"不支持的 MCP 动作：{action_type}",
            )
        params = payload.get("params")
        params = dict(params) if isinstance(params, dict) else {}
        server = (
            payload.get("server")
            or payload.get("server_id")
            or params.get("server")
            or params.get("server_id")
        )
        tool = (
            payload.get("tool")
            or params.get("tool")
            or params.get("name")
        )
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            arguments = params.get("arguments")
        outcome = await self.registry.call_tool(
            server,
            tool,
            arguments if isinstance(arguments, dict) else {},
            context={**dict(context or {}), **(params or {})},
            timeout=(
                params.get("timeout_seconds")
                if isinstance(params.get("timeout_seconds"), (int, float))
                else None
            ),
            retry_policy=params.get("retry_policy"),
            confirm=(
                payload.get("confirm")
                if payload.get("confirm") is not None
                else params.get("confirm")
            ),
        )
        return connector_result(
            outcome.success,
            data=outcome.result,
            error=outcome.error,
            **outcome.to_step_details(),
        )

    async def verify(
        self,
        assertion: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(assertion or {})
        server = payload.get("server") or payload.get("server_id")
        tool = str(payload.get("tool") or "").strip()
        if server is None or not tool:
            return connector_result(
                False,
                error="verify 需要 server 与 tool",
            )
        record = await self.registry.get_server(
            server,
            user_id=(context or {}).get("user_id"),
        )
        if record is None:
            return connector_result(False, error="MCP Server 不存在")
        from app.engine.mcp_registry import find_tool_manifest

        manifest = find_tool_manifest(record.tools, tool)
        if manifest is None:
            return connector_result(
                False,
                data={"available": False},
                error=f"工具 {tool} 不在清单中",
            )
        expected_authorized = payload.get("authorized")
        authorized = bool(manifest.get("authorized"))
        success = True
        if expected_authorized is not None:
            success = authorized == bool(expected_authorized)
        return connector_result(
            success,
            data={
                "available": manifest.get("available", True),
                "authorized": authorized,
                "risk_level": manifest.get("risk_level"),
            },
            error=None if success else "断言不成立",
        )

    async def auth_status(self) -> dict[str, Any]:
        servers = await self.registry.list_servers()
        statuses = []
        for server in servers:
            tools = [
                item
                for item in (server.tools or [])
                if isinstance(item, dict)
            ]
            statuses.append(
                {
                    "id": server.id,
                    "name": server.name,
                    "enabled": bool(server.enabled),
                    "authorized_tools": sum(
                        1 for item in tools if item.get("authorized")
                    ),
                    "destroyed_requires_confirmation": [
                        item.get("name")
                        for item in tools
                        if item.get("risk_level") == "destructive"
                        and not item.get("confirmed")
                    ],
                    "tool_count": len(tools),
                    "last_error": server.last_error,
                    "last_tested_at": (
                        server.last_tested_at.isoformat()
                        if server.last_tested_at
                        else None
                    ),
                }
            )
        return connector_result(True, data={"servers": statuses})

    async def aclose(self) -> None:
        await self.registry.aclose()


def _sdk_available() -> bool:
    from app.engine.mcp_client import sdk_available

    return sdk_available()


__all__ = ["MCPConnector"]
