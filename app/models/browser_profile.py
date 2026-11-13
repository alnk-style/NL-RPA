from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BrowserProfile(Base):
    __tablename__ = "browser_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_data_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    profile_directory: Mapped[str] = mapped_column(
        String(255),
        default="Default",
        nullable=False,
    )
    login_state: Mapped[str] = mapped_column(
        String(32),
        default="unknown",
        nullable=False,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


__all__ = ["BrowserProfile"]
