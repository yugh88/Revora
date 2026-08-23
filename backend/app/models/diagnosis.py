"""Diagnosis and MLDiagnosisPrediction ORM models. BUILD_SPEC Sections 4 & 4a.

These two tables are the hybrid symbolic + ML architecture in storage form:

* ``Diagnosis``             — the rule engine's verdict. AUTHORITATIVE. The
  action actually taken always traces to this row.
* ``MLDiagnosisPrediction`` — the self-trained decision tree's independent
  verdict. Never overrides the rule engine. When it disagrees, or when its
  confidence is below threshold, the event is routed to /exceptions as
  "ML/rule disagreement — needs review".

They live in one module because they are two views of the same question, and
because ``agrees_with_rule_engine`` is only meaningful relative to ``Diagnosis``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TZDateTime, sa_enum, utcnow
from app.enums import RootCauseCode

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.risk_event import RiskEvent


class Diagnosis(Base):
    """Rule-engine root-cause verdict for an event. One row per event.

    ``event_id`` is the primary key: an event is diagnosed once. A broken
    Promise-to-Pay does not re-diagnose this event — it raises a NEW event with
    root cause ``broken_ptp`` (Section 4).
    """

    __tablename__ = "diagnoses"

    event_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("risk_events.id", ondelete="CASCADE"), primary_key=True
    )
    root_cause_code: Mapped[RootCauseCode] = mapped_column(
        sa_enum(RootCauseCode, "root_cause_code"), nullable=False, index=True
    )
    #: 0.0-1.0. Section 11 requires ~5% of synthetic events to be genuinely
    #: ambiguous; those must land in the low-confidence bucket and reach
    #: /exceptions rather than being force-classified.
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    #: Ordered list of human-readable evidence strings, e.g.
    #: ``["gateway_error_code=BAD_REQUEST_CARD_EXPIRED", "attempt_number=1"]``.
    #: This is what makes the diagnosis explainable in the drill-down UI.
    evidence: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    diagnosed_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    event: Mapped["RiskEvent"] = relationship(back_populates="diagnosis")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Diagnosis event_id={self.event_id!r} "
            f"root_cause={self.root_cause_code.value!r} confidence={self.confidence}>"
        )


class MLDiagnosisPrediction(Base):
    """Self-trained classifier's independent prediction. Section 4a.

    Not unique on ``event_id``: ``model_version`` is a real column, so a
    retrained model may be scored against historical events for comparison
    without destroying the earlier prediction. The pipeline reads the latest row
    per event.

    Section 4a is explicit that this layer is NON-BLOCKING: if the model file is
    missing or ``predict()`` raises, the pipeline logs it and proceeds on the
    rule-based diagnosis alone. A missing row here is a normal state, not an
    error.
    """

    __tablename__ = "ml_diagnosis_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    predicted_root_cause: Mapped[RootCauseCode] = mapped_column(
        sa_enum(RootCauseCode, "root_cause_code"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    #: False OR low confidence => routed to /exceptions for human review.
    #: /batch reports ``ml_agreement_rate`` as the measured mean of this column.
    agrees_with_rule_engine: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)

    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    predicted_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    event: Mapped["RiskEvent"] = relationship(back_populates="ml_predictions")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<MLDiagnosisPrediction event_id={self.event_id!r} "
            f"predicted={self.predicted_root_cause.value!r} agrees={self.agrees_with_rule_engine}>"
        )
