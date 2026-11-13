from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DesktopAgent(Base):
    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspace.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(
        String(50),
        default="windows",
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    environment: Mapped[str] = mapped_column(
        String(20),
        default="local",
        nullable=False,
        index=True,
    )
    human_access_url: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )
    human_access_instructions: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    connection_security: Mapped[str] = mapped_column(
        String(20),
        default="unknown",
        nullable=False,
    )
    allowed_apps: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    allowed_doc_dirs: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="offline",
        nullable=False,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    last_capabilities_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
