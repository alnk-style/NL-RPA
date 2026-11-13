"""工具调用审计写入。

每次工具调用（含被拒绝、超时、失败）都会落一条 ``tool_call_audit``：
server、tool、风险级别、授权决策、脱敏后的参数与结果摘要、状态、耗时、
错误、workflow/execution/user。数据库不可用时退化为结构化日志，保证
审计事件不会静默丢失。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import AsyncSessionLocal
from app.models.mcp import ToolCallAudit
from app.security.redaction import redact_payload, secret_values

logger = logging.getLogger(__name__)


class ToolCallAuditWriter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.session_factory = session_factory or AsyncSessionLocal

    async def record(
        self,
        *,
        server_name: str,
        tool: str,
        status: str,
        decision: str = "allowed",
        risk_level: str = "mutating",
        server_id: int | None = None,
        arguments: dict[str, Any] | None = None,
        result: Any = None,
        error: str | None = None,
        duration_ms: float | None = None,
        attempts: int = 1,
        workflow_id: int | None = None,
        execution_id: int | None = None,
        user_id: int | None = None,
        workspace_id: int | None = None,
        secrets: list[str] | None = None,
        session: AsyncSession | None = None,
    ) -> int | None:
        redacted_arguments = redact_payload(arguments, secrets)
        redacted_result = (
            redact_payload(result, secrets) if result is not None else None
        )
        redacted_error = (
            redact_payload(str(error), secrets) if error else None
        )

        entry = ToolCallAudit(
            server_id=server_id,
            server_name=str(server_name),
            tool=str(tool),
            risk_level=str(risk_level or "mutating"),
            decision=str(decision or "allowed"),
            status=str(status or "success"),
            arguments_summary=redacted_arguments,
            result_summary=(
                redacted_result
                if isinstance(redacted_result, (dict, list))
                else {"value": redacted_result}
                if redacted_result is not None
                else None
            ),
            error=(
                str(redacted_error) if redacted_error is not None else None
            ),
            duration_ms=duration_ms,
            attempts=int(attempts or 1),
            workflow_id=workflow_id,
            execution_id=execution_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        try:
            if session is not None:
                session.add(entry)
                await session.flush()
                return entry.id
            async with self.session_factory() as own_session:
                own_session.add(entry)
                await own_session.commit()
                await own_session.refresh(entry)
                return entry.id
        except Exception:  # noqa: BLE001
            logger.warning(
                "工具调用审计写入失败 server=%s tool=%s status=%s "
                "decision=%s error=%s",
                server_name,
                tool,
                status,
                decision,
                error,
                exc_info=True,
            )
            return None


def audit_secrets(*candidates: Any) -> list[str]:
    """把凭据字典合并成一份脱敏用的秘密列表。"""

    secrets: list[str] = []
    for candidate in candidates:
        for value in secret_values(candidate):
            if value and value not in secrets:
                secrets.append(value)
    return secrets


__all__ = ["ToolCallAuditWriter", "audit_secrets"]
