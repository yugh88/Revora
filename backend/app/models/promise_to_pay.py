"""PromiseToPay ORM model. BUILD_SPEC Section 4.

The spec is emphatic that this must be "a real tracked entity, not just an
implied state". The daily APScheduler watcher (engine/promise_tracker.py) scans
rows where ``status == PENDING``: if ``promised_date`` has passed and the
underlying event is still unresolved, it marks the promise BROKEN and raises a
NEW RiskEvent with root cause ``broken_ptp``, one tone level above where the
previous conversation ended. That re-entry is what closes the loop.

/batch reports ``promises_made`` / ``promises_kept`` / ``promises_broken`` as
counts over this table (Section 10).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, Money, TZDateTime, sa_enum, utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.risk_event import RiskEvent

from app.enums import PromiseStatus


def _new_id() -> str:
    return f"ptp_{uuid.uuid4().hex[:20]}"


class PromiseToPay(Base):
    """A customer's commitment to pay by a date.

    Attached to an ``invoice_overdue`` or ``subscription_failed`` event. An
    event may hold several over its life (a renegotiated promise is a new row,
    leaving the broken one intact for the audit trail).
    """

    __tablename__ = "promises_to_pay"
    __table_args__ = (
        # The watcher's hot query: pending promises whose date has passed.
        Index("ix_promises_status_date", "status", "promised_date"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_new_id)
    event_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    promised_date: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    promised_amount: Mapped[Decimal] = mapped_column(Money, nullable=False)

    status: Mapped[PromiseStatus] = mapped_column(
        sa_enum(PromiseStatus, "promise_status"),
        nullable=False,
        default=PromiseStatus.PENDING,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    event: Mapped["RiskEvent"] = relationship(back_populates="promises")

    def is_lapsed(self, now: datetime | None = None) -> bool:
        """True when a still-pending promise is past its date.

        Lapsed is not the same as broken: the watcher must also confirm the
        underlying event is still unresolved before marking BROKEN, otherwise a
        customer who paid on the promised day would be punished for it.
        """
        if self.status != PromiseStatus.PENDING:
            return False
        return (now or utcnow()) > self.promised_date

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<PromiseToPay id={self.id!r} event_id={self.event_id!r} "
            f"status={self.status.value!r} due={self.promised_date}>"
        )
