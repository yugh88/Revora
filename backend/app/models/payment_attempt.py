"""PaymentAttempt ORM model. BUILD_SPEC Sections 4 and 9.

The UNIQUE constraint on ``idempotency_key`` is the enforcement point for
Section 9's idempotency requirement: even if the application-level check is
skipped or races, the database refuses the second insert. Callers treat an
IntegrityError on this column as "already executed — return the existing row",
never as a failure.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TZDateTime, sa_enum, utcnow
from app.enums import GatewayUsed, PaymentAttemptStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.risk_event import RiskEvent


def _new_id() -> str:
    return f"att_{uuid.uuid4().hex[:20]}"


class PaymentAttempt(Base):
    """A single execution attempt against a gateway."""

    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payment_attempts_idempotency_key"),
        UniqueConstraint("event_id", "attempt_number", name="uq_payment_attempts_event_attempt"),
        Index("ix_payment_attempts_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_new_id)
    event_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: 1-based. Compared against Policy.max_attempts by the stopping rules.
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    status: Mapped[PaymentAttemptStatus] = mapped_column(
        sa_enum(PaymentAttemptStatus, "payment_attempt_status"),
        nullable=False,
        default=PaymentAttemptStatus.PENDING,
    )
    #: Gateway-vocabulary failure reason, kept raw for the diagnosis engine.
    failure_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    #: Hash of (event_id, attempt_number, action) — see engine/idempotency.py.
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Gateway-side identifier (Razorpay payment id, or simulated equivalent).
    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)

    gateway_used: Mapped[GatewayUsed] = mapped_column(
        sa_enum(GatewayUsed, "gateway_used"), nullable=False, default=GatewayUsed.LOCAL_SIMULATION
    )

    initiated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    event: Mapped["RiskEvent"] = relationship(back_populates="payment_attempts")

    @property
    def is_resolved(self) -> bool:
        """True once the gateway has returned a final state."""
        return self.status != PaymentAttemptStatus.PENDING

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<PaymentAttempt event_id={self.event_id!r} n={self.attempt_number} "
            f"status={self.status.value!r}>"
        )
