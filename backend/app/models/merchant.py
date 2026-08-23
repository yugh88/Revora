"""Merchant ORM model. BUILD_SPEC Section 4.

Both RiskEvent.merchant_id and Policy.merchant_id FK here — policies and events
belong to a merchant, never to a global namespace.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TZDateTime, utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.policy import Policy
    from app.models.risk_event import RiskEvent


def _new_id() -> str:
    return f"mer_{uuid.uuid4().hex[:16]}"


class Merchant(Base):
    """A merchant using Revora."""

    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    risk_events: Mapped[list["RiskEvent"]] = relationship(
        back_populates="merchant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    policies: Mapped[list["Policy"]] = relationship(
        back_populates="merchant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Merchant id={self.id!r} name={self.name!r}>"
