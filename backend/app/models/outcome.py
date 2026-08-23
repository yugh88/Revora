"""Outcome ORM model — the recovery ledger. BUILD_SPEC Section 4.

Spec name: "Outcome / RecoveryLedger". The table is named ``recovery_ledger``
because that is what it functionally is, and because Section 2's bar hinges on
it: "measured money recovered across a batch (real numbers from ledger state,
not invented)". Every rupee /batch reports is a SUM over this table — nothing in
the API is permitted to compute recovered money any other way.

One row per event, so ``event_id`` is the primary key.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, Money, TZDateTime, sa_enum
from app.enums import Channel, OutcomeResolution

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.risk_event import RiskEvent


class Outcome(Base):
    """Ledger entry recording what was actually recovered for an event."""

    __tablename__ = "recovery_ledger"

    event_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("risk_events.id", ondelete="CASCADE"), primary_key=True
    )

    resolved: Mapped[OutcomeResolution] = mapped_column(
        sa_enum(OutcomeResolution, "outcome_resolution"),
        nullable=False,
        default=OutcomeResolution.PENDING,
        index=True,
    )
    #: Money actually collected. Zero while pending or lost; may be less than
    #: RiskEvent.amount for PARTIALLY_RECOVERED.
    amount_recovered: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0.00")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Which channel closed it. ``Channel.EXTERNAL`` is the Section 9
    #: "recovered_externally" case — the customer paid on their own before the
    #: engine acted, and the engine correctly declined to double-act.
    resolution_channel: Mapped[Channel | None] = mapped_column(
        sa_enum(Channel, "channel"), nullable=True
    )

    event: Mapped["RiskEvent"] = relationship(back_populates="outcome")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Outcome event_id={self.event_id!r} resolved={self.resolved.value!r} "
            f"recovered={self.amount_recovered}>"
        )
