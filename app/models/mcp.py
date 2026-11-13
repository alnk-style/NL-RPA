"""MCP Server 配置与工具调用审计的数据模型。

阶段 4 的配置优先落在 JSON 字段里：Server 记录保存传输方式、密钥引用
与工具清单（含风险级别和授权状态），工具调用审计单独一张表，删除
Server 后审计仍可保留。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MCPServer(Base):
    __tablename__ = "mcp_server"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_mcp_server_user_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspace.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 阶段 5：供应链安全（版本固定、sha256 指纹、可选 HMAC 签名）。
    version: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    fingerprint: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    signature: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    toolset_fingerprint: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport: Mapped[str] = mapped_column(
        String(20),
        default="http",
        nullable=False,
    )
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    command: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    args: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    env: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    headers: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    # 密钥用现有 Fernet 密钥加密存储，接口只返回是否已配置。
    secret_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    default_risk: Mapped[str] = mapped_column(
        String(20),
        default="mutating",
        nullable=False,
    )
    timeout_seconds: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    config: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    tools: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ToolCallAudit(Base):
    __tablename__ = "tool_call_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int | None] = mapped_column(
        nullable=True,
        index=True,
    )
    server_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool: Mapped[str] = mapped_column(String(255), nullable=False)
    risk_level: Mapped[str] = mapped_column(
        String(20),
        default="mutating",
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(
        String(20),
        default="allowed",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="success",
        nullable=False,
    )
    arguments_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    attempts: Mapped[int] = mapped_column(
        default=1,
        nullable=False,
    )
    workflow_id: Mapped[int | None] = mapped_column(
        nullable=True,
        index=True,
    )
    execution_id: Mapped[int | None] = mapped_column(
        nullable=True,
        index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        nullable=True,
        index=True,
    )
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspace.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


__all__ = ["MCPServer", "ToolCallAudit"]
