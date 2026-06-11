from datetime import datetime, timezone
import enum

from sqlalchemy import DateTime, Enum as SAEnum, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ProxyStatus(str, enum.Enum):
    creating = "creating"
    running = "running"
    stopped = "stopped"
    error = "error"


class Base(DeclarativeBase):
    pass


class ProxyInstance(Base):
    __tablename__ = "instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    secret: Mapped[str] = mapped_column(String, nullable=False)
    port: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    container_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[ProxyStatus] = mapped_column(
        SAEnum(ProxyStatus),
        default=ProxyStatus.creating,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
