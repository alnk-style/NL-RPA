from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.execution import ExecutionLog
    from app.models.user import User
    from app.models.workflow_version import WorkflowVersion


class Workflow(Base):
    __tablename__ = "workflow"

    def __init__(self, **kwargs: Any) -> None:
        from app.schemas.execution_policy import default_execution_policy_dict

        kwargs.setdefault("executor_type", "web")
        kwargs.setdefault("required_capabilities", [])
        if "execution_mode" not in kwargs:
            kwargs["execution_mode"] = (
                "legacy_code"
                if str(kwargs.get("generated_code") or "").strip()
                else "structured"
            )
        kwargs.setdefault("execution_policy", default_execution_policy_dict())
        kwargs.setdefault("schedule_timezone", "Asia/Shanghai")
        kwargs.setdefault("draft_revision", 0)
        super().__init__(**kwargs)

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
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    natural_language_input: Mapped[str] = mapped_column(Text, nullable=False)
    structured_plan: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    generated_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    dag_definition: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    trigger_type: Mapped[str] = mapped_column(
        String(50),
        default="manual",
        nullable=False,
    )
    executor_type: Mapped[str] = mapped_column(
        String(20),
        default="web",
        nullable=False,
        index=True,
    )
    required_capabilities: Mapped[list[str] | None] = mapped_column(
        JSON,
        default=list,
        nullable=True,
    )
    execution_mode: Mapped[str] = mapped_column(
        String(20),
        default="structured",
        nullable=False,
        index=True,
    )
    execution_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )
    active_version_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )
    schedule_timezone: Mapped[str] = mapped_column(
        String(64),
        default="Asia/Shanghai",
        nullable=False,
    )
    draft_revision: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default="draft",
        nullable=False,
    )
    notification_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
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

    user: Mapped["User"] = relationship(back_populates="workflows")
    executions: Mapped[list["ExecutionLog"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
    )
    versions: Mapped[list["WorkflowVersion"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowVersion.version",
    )
