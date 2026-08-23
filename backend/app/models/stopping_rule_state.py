"""StoppingRuleState ORM model. BUILD_SPEC Section 4.

Section 2's bar requires "stopping rules that actually stop things" and
"compliant escalation with a hard ceiling". This row is where both are made
observable: it is the per-event counter set the policy engine reads, the
compliance frequency-cap check reads (Section 7 rule 3), and the /events
drill-down renders as "stopping-rule state".

One row per event, so ``event_id`` is the primary key.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TZDateTime, utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.risk_event import RiskEvent


class StoppingRuleState(Base):
    """Per-event bounds on how far the engine may go."""

    __tablename__ = "stopping_rule_states"

    event_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("risk_events.id", ondelete="CASCADE"), primary_key=True
    )

    attempts_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Resolved from the merchant's Policy for this event type at decision time.
    max_attempts_for_type: Mapped[int] = mapped_column(Integer, nullable=False)

    #: While set and in the future, no further contact may be made.
    cooldown_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True, index=True)

    #: Snapshot of CustomerProfile.do_not_contact as it was when this event was
    #: decided. Snapshotted deliberately: the audit trail must show what the
    #: engine believed at the time, not what the profile says today.
    do_not_contact_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: 0 = none, 1 = L1, 2 = L2. Section 6: L2 is a hard ceiling for
    #: invoice_overdue — the engine never auto-escalates past it.
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Set when the engine has permanently stopped acting. Reasons used by the
    #: /batch ``stopping_rule_triggers`` breakdown (Section 10): "cooldown",
    #: "do_not_contact", "max_attempts", "hard_decline", "escalation_ceiling",
    #: "policy_block".
    hard_stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    event: Mapped["RiskEvent"] = relationship(back_populates="stopping_rule_state")

    def is_in_cooldown(self, now: datetime | None = None) -> bool:
        """True when a cooldown window is currently active."""
        if self.cooldown_until is None:
            return False
        return (now or utcnow()) < self.cooldown_until

    def attempts_remaining(self) -> int:
        """Attempts still permitted for this event, never negative."""
        return max(0, self.max_attempts_for_type - self.attempts_used)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<StoppingRuleState event_id={self.event_id!r} "
            f"attempts={self.attempts_used}/{self.max_attempts_for_type} "
            f"L{self.escalation_level} stop={self.hard_stop_reason!r}>"
        )
