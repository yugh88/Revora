"""Decision ORM model — structured reasoning, source of truth. BUILD_SPEC Section 4.

This is the row a judge should be able to point at and ask "why did the system
do that?", and get a complete answer without reading code:

  decision_factors     the facts considered
  recovery_probability the score, and which engine produced it
  policy_result        which named rule was checked, with threshold vs actual
  action_code          what was chosen
  reasoning_text       the rendered sentence (template engine, never an LLM)

An event has MANY decisions — Section 6 defines attempt 1 / attempt 2 / escalation
interventions, and each is its own decision with its own policy evaluation.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TZDateTime, sa_enum, utcnow
from app.enums import ProbabilitySource

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.risk_event import RiskEvent


class Decision(Base):
    """One bounded, explainable intervention choice."""

    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_event_decided", "event_id", "decided_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: JSON: root_cause, confidence, amount, customer_success_rate,
    #: attempt_number, channel_preference, days_overdue, ...
    #: This is also the input the template engine renders ``reasoning_text`` and
    #: the Hinglish scripts from (Section 7) — slots are filled from here, never
    #: from hand-typed strings.
    decision_factors: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    recovery_probability: Mapped[float] = mapped_column(Float, nullable=False)
    probability_source: Mapped[ProbabilitySource] = mapped_column(
        sa_enum(ProbabilitySource, "probability_source"),
        nullable=False,
        default=ProbabilitySource.DETERMINISTIC,
    )

    #: Structured, NOT a boolean. Shape (Section 4):
    #: {"status": "allowed"|"blocked", "rule_triggered": str|None,
    #:  "threshold_checked": str|None, "actual_value": Any, "threshold_value": Any}
    #: The /exceptions page renders this verbatim as "why we didn't act".
    policy_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    #: Which Policy row version gated this decision — so a later policy edit
    #: never rewrites the history of decisions already made.
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: e.g. "update_card_email", "sms_reminder", "in_app_nudge",
    #: "formal_notice", "human_handoff", "no_action".
    action_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    #: Rendered by engine/template_engine.py from ``decision_factors``.
    reasoning_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    decided_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    event: Mapped["RiskEvent"] = relationship(back_populates="decisions")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Decision event_id={self.event_id!r} action={self.action_code!r} "
            f"p={self.recovery_probability:.3f}>"
        )
