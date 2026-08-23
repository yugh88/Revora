"""RiskEvent ORM model — the hub of the whole pipeline. BUILD_SPEC Section 4.

Every other table hangs off this one. Status transitions are governed
exclusively by engine/state_machine.py (Section 8); nothing else may assign to
``RiskEvent.status`` directly.

Section 4 fixes this table's columns exactly, so the B2B marker lives inside
``raw_signal`` as ``{"channel": "b2b"}`` rather than as an extra column — the
``channel`` property below reads it back ergonomically, and ``is_b2b`` is what
the invoice_overdue workflow should branch on.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, Money, TZDateTime, sa_enum, utcnow
from app.enums import Channel, EventStatus, EventType, GatewayUsed

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.action_lock import ActionLock
    from app.models.audit_log import AuditLog
    from app.models.customer_profile import CustomerProfile
    from app.models.decision import Decision
    from app.models.diagnosis import Diagnosis, MLDiagnosisPrediction
    from app.models.merchant import Merchant
    from app.models.outcome import Outcome
    from app.models.payment_attempt import PaymentAttempt
    from app.models.promise_to_pay import PromiseToPay
    from app.models.stopping_rule_state import StoppingRuleState


def _new_id() -> str:
    return f"evt_{uuid.uuid4().hex[:20]}"


class RiskEvent(Base):
    """A unit of revenue at risk."""

    __tablename__ = "risk_events"
    __table_args__ = (
        # /events feed is filtered by status+type and ordered by detection time;
        # /audit and the structured logs are searched by correlation_id.
        Index("ix_risk_events_status_type", "status", "type"),
        Index("ix_risk_events_detected_at", "detected_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_new_id)

    type: Mapped[EventType] = mapped_column(sa_enum(EventType, "event_type"), nullable=False)
    merchant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("customer_profiles.customer_id"), nullable=False, index=True
    )

    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    #: Upstream identifier (Razorpay payment/subscription/invoice id, or a
    #: synthetic equivalent). Used by the pre-execution re-check in Section 9.
    source_ref: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    detected_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    #: Raw inbound signal, kept verbatim for auditability. Known keys:
    #: ``channel`` ("b2b" for B2B receivables), ``gateway_error_code``,
    #: ``due_date``, ``attempt_number``.
    raw_signal: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[EventStatus] = mapped_column(
        sa_enum(EventStatus, "event_status"),
        nullable=False,
        default=EventStatus.OPEN,
        index=True,
    )
    gateway_used: Mapped[GatewayUsed] = mapped_column(
        sa_enum(GatewayUsed, "gateway_used"), nullable=False, default=GatewayUsed.LOCAL_SIMULATION
    )
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # --- relationships ---------------------------------------------------- #
    merchant: Mapped["Merchant"] = relationship(back_populates="risk_events")
    customer: Mapped["CustomerProfile"] = relationship(back_populates="risk_events")

    diagnosis: Mapped["Diagnosis | None"] = relationship(
        back_populates="event", uselist=False, cascade="all, delete-orphan"
    )
    ml_predictions: Mapped[list["MLDiagnosisPrediction"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    decisions: Mapped[list["Decision"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="Decision.decided_at"
    )
    payment_attempts: Mapped[list["PaymentAttempt"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="PaymentAttempt.attempt_number",
    )
    action_lock: Mapped["ActionLock | None"] = relationship(
        back_populates="event", uselist=False, cascade="all, delete-orphan"
    )
    stopping_rule_state: Mapped["StoppingRuleState | None"] = relationship(
        back_populates="event", uselist=False, cascade="all, delete-orphan"
    )
    outcome: Mapped["Outcome | None"] = relationship(
        back_populates="event", uselist=False, cascade="all, delete-orphan"
    )
    promises: Mapped[list["PromiseToPay"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    #: NOTE: no cascade delete — the audit log is append-only and outlives
    #: everything else (see models/audit_log.py).
    audit_entries: Mapped[list["AuditLog"]] = relationship(
        back_populates="event", order_by="AuditLog.id"
    )

    # --- derived helpers --------------------------------------------------- #
    @property
    def channel(self) -> str | None:
        """Channel flag carried on the raw signal, if any (Section 4)."""
        if not isinstance(self.raw_signal, dict):
            return None
        value = self.raw_signal.get("channel")
        return value if isinstance(value, str) else None

    @property
    def is_b2b(self) -> bool:
        """True for a B2B receivable: invoice_overdue + ``channel=b2b``."""
        return self.type == EventType.INVOICE_OVERDUE and self.channel == Channel.B2B.value

    @property
    def gateway_error_code(self) -> str | None:
        """Razorpay-vocabulary error code from the raw signal, if present.

        Feature input for the ML classifier (Section 4a) and evidence for the
        rule-based diagnosis engine.
        """
        if not isinstance(self.raw_signal, dict):
            return None
        value = self.raw_signal.get("gateway_error_code")
        return value if isinstance(value, str) else None

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<RiskEvent id={self.id!r} type={self.type.value!r} "
            f"status={self.status.value!r} amount={self.amount}>"
        )
