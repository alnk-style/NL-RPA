from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.human_task_event import HumanTaskEvent


class HumanTask(Base):
    __tablename__ = "human_task"
    __table_args__ = (
        Index(
            "ix_human_task_execution_status",
            "execution_id",
            "status",
        ),
        Index(
            "ix_human_task_status_expires",
            "status",
            "expires_at",
        ),
        Index(
            "uq_human_task_pending_dedupe",
            "dedupe_key",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(
        ForeignKey("execution_log.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[int | None] = mapped_column(nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    verification_started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    events: Mapped[list["HumanTaskEvent"]] = relationship(
        back_populates="human_task",
        cascade="all, delete-orphan",
        order_by="HumanTaskEvent.id",
    )


__all__ = ["HumanTask"]
