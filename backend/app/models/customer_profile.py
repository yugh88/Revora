"""CustomerProfile ORM model. BUILD_SPEC Section 4.

Feeds ``decision_factors.customer_success_rate`` (Section 4 Decision), the
``customer_success_rate`` feature of the ML classifier (Section 4a), and the
``do_not_contact`` stopping rule. ``do_not_contact`` is snapshotted onto
StoppingRuleState at decision time so the audit trail records what the engine
believed *then*, not what the profile says now.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, Money, TZDateTime, sa_enum, utcnow
from app.enums import Channel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.risk_event import RiskEvent


class CustomerProfile(Base):
    """Per-customer behavioural profile.

    ``customer_id`` is the natural primary key: it is the merchant-supplied
    identifier that arrives on the inbound risk signal.
    """

    __tablename__ = "customer_profiles"

    customer_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    payment_success_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    payment_failure_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    lifetime_value: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0.00"))
    avg_payment_delay_days: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    preferred_channel: Mapped[Channel] = mapped_column(
        sa_enum(Channel, "channel"), nullable=False, default=Channel.EMAIL
    )
    do_not_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    risk_events: Mapped[list["RiskEvent"]] = relationship(back_populates="customer")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<CustomerProfile customer_id={self.customer_id!r} "
            f"success_rate={self.payment_success_rate} dnc={self.do_not_contact}>"
        )
