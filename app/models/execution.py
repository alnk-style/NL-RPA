from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.workflow import Workflow


class ExecutionLog(Base):
    __tablename__ = "execution_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflow.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspace.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_version.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    execution_policy_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    host_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    host_environment: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )
    host_bound: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    host_affinity_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    host_reason: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )
    browser_profile_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    human_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger_type: Mapped[str] = mapped_column(
        String(50),
        default="manual",
        nullable=False,
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="running",
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshots: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    artifacts: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    step_logs: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    # 阶段 5：执行级指标（人工介入次数、失败分类、修复统计等）。
    # JSON 字段保证可逆迁移，旧记录为 NULL 时按 step_logs 回退计算。
    metrics: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    workflow: Mapped["Workflow"] = relationship(back_populates="executions")
