"""Planner-facing catalog built from authorized MCP registry records."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.app_mcp.contracts import sanitize_message
from app.engine.connectors.permissions import normalize_risk_level
from app.engine.mcp_registry import MCPRegistry

_SENSITIVE_KEYS = {
    "auth_token",
    "authorization",
    "headers",
    "env",
    "password",
    "secret",
}


class MCPToolCatalog:
    """Read-only, authorization-filtered catalog for planner input."""

    def __init__(
        self,
        registry: MCPRegistry | None,
        *,
        max_tools: int = 50,
        max_description_length: int = 500,
    ) -> None:
        self.registry = registry
        self.max_tools = max(1, min(200, int(max_tools)))
        self.max_description_length = max(
            40,
            min(4000, int(max_description_length)),
        )

    async def list_tools(
        self,
        *,
        user_id: int,
        workspace_id: int | None = None,
        q: str | None = None,
        limit: int | None = None,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        if self.registry is None or not self.registry.enabled:
            return []
        try:
            resolved_limit = int(limit if limit is not None else self.max_tools)
        except (TypeError, ValueError):
            resolved_limit = self.max_tools
        resolved_limit = max(0, min(resolved_limit, self.max_tools))
        if resolved_limit == 0:
            return []

        servers = await self.registry.list_servers(
            user_id=int(user_id),
            workspace_id=workspace_id,
            session=session,
        )
        query = str(q or "").strip().casefold()
        tools: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for server in servers:
            if not bool(server.enabled):
                continue
            app_name = _app_name(server)
            for raw in server.tools or []:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name") or "").strip()
                if not name or raw.get("available", True) is False:
                    continue
                risk = normalize_risk_level(
                    raw.get("risk_level"),
                    str(server.default_risk or "mutating"),
                )
                authorized = bool(raw.get("authorized"))
                confirmed = bool(raw.get("confirmed"))
                if risk == "read_only":
                    authorized = True
                elif risk == "mutating":
                    if not authorized:
                        continue
                elif risk == "destructive" and (not authorized or not confirmed):
                    continue

                description = _clean_text(
                    raw.get("description"),
                    self.max_description_length,
                )
                searchable = (
                    f"{server.name} {app_name} {name} {description}"
                ).casefold()
                if query and query not in searchable:
                    continue
                key = (int(server.id), name)
                if key in seen:
                    continue
                seen.add(key)
                tools.append(
                    {
                        "server_id": int(server.id),
                        "server": str(server.name),
                        "app_name": app_name,
                        "tool": name,
                        "description": description,
                        "input_schema": _safe_schema(
                            raw.get("input_schema") or raw.get("inputSchema")
                        ),
                        "risk_level": risk,
                        "authorized": authorized,
                        "confirmed": confirmed,
                        "metadata_trust": "untrusted",
                        "metadata_kind": "external_tool_metadata",
                    }
                )
                if len(tools) >= resolved_limit:
                    return tools
        return tools


def _app_name(server: Any) -> str:
    config = server.config if isinstance(server.config, dict) else {}
    value = config.get("app_name") or config.get("display_name") or server.name
    return _clean_text(value, 200)


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return sanitize_message(text, max_length=limit)


def _safe_schema(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if depth >= 12:
        return {}
    safe = _safe_json(value, depth=depth)
    if not isinstance(safe, dict):
        return {}
    try:
        json.dumps(safe, ensure_ascii=False)
    except (TypeError, ValueError):
        return {}
    return safe


def _safe_json(value: Any, *, depth: int) -> Any:
    if depth >= 12:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if not value:
            return ""
        return sanitize_message(value, max_length=1000)
    if isinstance(value, Mapping):
        payload: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if name.casefold() in _SENSITIVE_KEYS:
                continue
            payload[name] = _safe_json(item, depth=depth + 1)
        return payload
    if isinstance(value, (list, tuple, set)):
        return [_safe_json(item, depth=depth + 1) for item in value]
    return None


__all__ = ["MCPToolCatalog"]
