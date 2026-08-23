"""AuditLog ORM model — immutable, append-only. BUILD_SPEC Section 4.

Section 2's bar demands a "full audit trail: detection -> diagnosis -> decision
-> policy -> execution -> verification -> recovery/escalation". Section 4 calls
this table immutable and append-only, so that property is ENFORCED here rather
than merely intended: a ``before_flush`` listener rejects any UPDATE or DELETE
of an AuditLog row on any session in the process. Appending is the only legal
operation.

``event_id`` is nullable with ``ON DELETE SET NULL`` on purpose — batch-level
entries are not tied to a single event, and the log must outlive the records it
describes rather than being cascade-deleted along with them.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text, event
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.database import Base, TZDateTime, sa_enum, utcnow
from app.enums import AuditActor, AuditStage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.risk_event import RiskEvent


class ImmutableAuditLogError(RuntimeError):
    """Raised when code attempts to modify or delete an existing audit entry."""


class AuditLog(Base):
    """One append-only entry in the audit trail."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_event_stage", "event_id", "stage"),
        Index("ix_audit_logs_timestamp", "timestamp"),
    )

    #: Autoincrement integer doubles as the append order, so entries for the
    #: same event sort deterministically even at identical timestamps.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    timestamp: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    event_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("risk_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    actor: Mapped[AuditActor] = mapped_column(
        sa_enum(AuditActor, "audit_actor"), nullable=False, default=AuditActor.SYSTEM
    )
    stage: Mapped[AuditStage] = mapped_column(sa_enum(AuditStage, "audit_stage"), nullable=False)

    #: Short machine-readable verb, e.g. "state_transition",
    #: "invalid_transition_rejected", "policy_blocked", "retry_executed".
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)

    #: JSON so an entry can carry either a bare status string or a richer
    #: snapshot, without a schema change per stage.
    before_state: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    after_state: Mapped[Any | None] = mapped_column(JSON, nullable=True)

    #: Human-readable justification. For rendered text this is the template
    #: output; for anomalies it is the caught-violation description.
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    event: Mapped["RiskEvent | None"] = relationship(back_populates="audit_entries")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<AuditLog id={self.id} stage={self.stage.value!r} action={self.action!r} "
            f"event_id={self.event_id!r}>"
        )


@event.listens_for(Session, "before_flush")
def _enforce_audit_log_append_only(
    session: Session, flush_context: Any, instances: Any
) -> None:
    """Reject any attempt to mutate or delete an existing AuditLog row.

    Inserts pass through untouched. This runs before the flush reaches the
    database, so the offending transaction fails loudly at the point of the bug
    rather than quietly rewriting history.
    """
    for obj in session.dirty:
        if isinstance(obj, AuditLog) and session.is_modified(obj, include_collections=False):
            raise ImmutableAuditLogError(
                f"AuditLog id={obj.id} is append-only and cannot be modified. "
                "Append a new corrective entry instead."
            )
    for obj in session.deleted:
        if isinstance(obj, AuditLog):
            raise ImmutableAuditLogError(
                f"AuditLog id={obj.id} is append-only and cannot be deleted."
            )
