"""CommunicationLog — one recovery message, and what became of it.

Revora decides a customer should be contacted; this records the message it would
send, on which channel, and what happened next.

NOTHING HERE IS EVER SENT
--------------------------
There is no email, SMS or voice provider wired into Revora, so no row in this
table can honestly claim a customer heard from anyone. ``is_simulated`` is
therefore not a mode flag that might one day be False by accident — it is a
standing statement about what this table means. The status enum has no "sent"
and no "delivered" value at all, which is the strongest way to guarantee the UI
cannot show one.

The same discipline applies to responses. ``customer_response`` is populated
only when someone explicitly simulates one in the demo. It is never inferred
from a message having been prepared, because a customer who has not replied has
simply not replied, and inventing engagement is how a recovery tool starts
lying to the business it is meant to serve.

WHERE THE MESSAGE COMES FROM
-----------------------------
The body is produced by the existing template engine, which runs the full
Section 7 compliance gate first. A message that fails any rule is stored with
status BLOCKED and an EMPTY body: a refused message that still carried its text
could be copied and sent anyway, which would defeat the gate entirely.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TZDateTime, sa_enum, utcnow
from app.enums import Channel, CommunicationStatus, CustomerResponse


def _new_id() -> str:
    return f"comm_{uuid.uuid4().hex[:16]}"


class CommunicationLog(Base):
    """A recovery message prepared for one event."""

    __tablename__ = "communication_log"
    __table_args__ = (
        Index("ix_communication_event", "event_id"),
        Index("ix_communication_channel", "channel"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_new_id)

    #: The recovery case that prompted the contact. A message with no case
    #: behind it could not be explained to the customer or to an auditor.
    event_id: Mapped[str] = mapped_column(
        ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False
    )

    channel: Mapped[Channel] = mapped_column(sa_enum(Channel, "channel"), nullable=False)

    status: Mapped[CommunicationStatus] = mapped_column(
        sa_enum(CommunicationStatus, "communication_status"),
        nullable=False,
        default=CommunicationStatus.PREPARED,
    )

    #: The exact text. Empty when compliance blocked it — deliberately, so a
    #: refused message cannot be copied out of the record and sent anyway.
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: Why this customer is being contacted, as the root cause the engine
    #: recorded. Translated for display; stored as the engine's own value so it
    #: stays traceable.
    reason: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")

    #: Why the agent chose this channel, in merchant language. Stored so the
    #: explanation a merchant read at the time survives, rather than being
    #: recomputed later from state that may have moved on.
    channel_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: Present when compliance refused, explaining which rule and why.
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Always true. Revora has no provider integration, so every record in this
    #: table is a representation of a message, never evidence of one.
    is_simulated: Mapped[bool] = mapped_column(nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    #: When the demo represented sending it. Never a delivery timestamp.
    simulated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Only ever set by an explicit simulated response.
    customer_response: Mapped[CustomerResponse | None] = mapped_column(
        sa_enum(CustomerResponse, "customer_response"), nullable=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Set when the simulated response was a commitment to pay, linking the
    #: conversation to the promise it produced.
    promise_id: Mapped[str | None] = mapped_column(
        ForeignKey("promises_to_pay.id", ondelete="SET NULL"), nullable=True
    )

    event: Mapped["RiskEvent"] = relationship()  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CommunicationLog {self.id} {self.channel} {self.status}>"
