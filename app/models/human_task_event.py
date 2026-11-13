from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.human_task import HumanTask


class HumanTaskEvent(Base):
    __tablename__ = "human_task_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    human_task_id: Mapped[int] = mapped_column(
        ForeignKey("human_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    human_task: Mapped["HumanTask"] = relationship(
        back_populates="events"
    )


__all__ = ["HumanTaskEvent"]
