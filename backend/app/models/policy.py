"""Policy ORM model — merchant-configurable thresholds. BUILD_SPEC Section 4.

Policies are versioned rather than mutated in place. ``PUT /policies`` inserts a
NEW row with an incremented ``policy_version`` for that (merchant, event_type)
pair; the previous row stays. Decisions record the ``policy_version`` they were
gated by, so editing a threshold today never rewrites the reasoning behind a
decision made yesterday — which is precisely what an audit trail has to
guarantee.

"Current policy" therefore means: the highest ``policy_version`` for that
(merchant_id, event_type).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, Money, TZDateTime, sa_enum, utcnow
from app.enums import EventType

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.merchant import Merchant


class Policy(Base):
    """One merchant's bounds for one event type, at one version."""

    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "event_type", "policy_version", name="uq_policies_merchant_type_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: Monotonically increasing per (merchant_id, event_type). Starts at 1.
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    merchant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[EventType] = mapped_column(
        sa_enum(EventType, "event_type"), nullable=False, index=True
    )

    #: Section 6 caps, e.g. checkout_abandoned stops after 1 email.
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    cooldown_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)

    #: Above this amount, escalate to a human instead of retrying silently.
    amount_threshold: Mapped[Decimal] = mapped_column(Money, nullable=False)

    #: Minimum score from the Recovery Probability Engine for an action to be
    #: worth taking at all (Section 6 formula).
    recovery_probability_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    #: Compliance frequency cap — Section 7 rule 3 checks script generation
    #: against this before rendering.
    contact_limit_per_channel: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    #: HARD ceiling on auto-escalation. Section 6: never past L2.
    escalation_ceiling: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    merchant: Mapped["Merchant"] = relationship(back_populates="policies")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Policy merchant={self.merchant_id!r} type={self.event_type.value!r} "
            f"v{self.policy_version} max_attempts={self.max_attempts} "
            f"ceiling=L{self.escalation_ceiling}>"
        )
