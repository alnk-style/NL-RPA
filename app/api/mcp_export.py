"""把平台工具导出为 MCP Server 的 HTTP 端点（POST /mcp）。"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.engine.mcp_server import MCPPlatformServer
from app.models.user import User

router = APIRouter(tags=["MCP Server"])


def require_mcp_server_export() -> None:
    if not get_settings().MCP_SERVER_EXPORT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MCP Server 导出未启用，请先设置 MCP_SERVER_EXPORT=true。",
        )


def _server(request: Request) -> MCPPlatformServer:
    server = getattr(request.app.state, "mcp_platform_server", None)
    if server is None:
        settings = get_settings()
        manager = getattr(request.app.state, "execution_manager", None)
        server = MCPPlatformServer(
            start_workflow=(
                getattr(manager, "start_workflow", None)
                if manager is not None
                else None
            ),
            allow_mutating=settings.MCP_SERVER_ALLOW_MUTATING,
        )
        request.app.state.mcp_platform_server = server
    return server


def _check_token(token: str | None) -> None:
    expected = get_settings().MCP_SERVER_TOKEN
    if not expected:
        return
    provided = str(token or "")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP Server token 无效",
        )


@router.post(
    "/mcp",
    dependencies=[
        Depends(require_mcp_server_export),
        Depends(get_current_user),
    ],
)
async def mcp_endpoint(
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    x_mcp_token: str | None = Header(default=None),
) -> Response:
    _check_token(x_mcp_token)
    server = _server(request)
    result = await server.handle(payload, user=current_user, db=db)
    if result is None:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return JSONResponse(result)


@router.get(
    "/mcp",
    dependencies=[Depends(require_mcp_server_export)],
)
async def mcp_endpoint_info() -> dict[str, Any]:
    settings = get_settings()
    return {
        "transport": "http",
        "protocol": "json-rpc-2.0",
        "allow_mutating": settings.MCP_SERVER_ALLOW_MUTATING,
        "auth": {
            "jwt": "Authorization: Bearer <token>",
            "server_token": bool(settings.MCP_SERVER_TOKEN),
        },
    }


__all__ = ["require_mcp_server_export", "router"]
