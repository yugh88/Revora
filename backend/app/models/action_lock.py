"""ActionLock ORM model — concurrency protection. BUILD_SPEC Section 4.

Any job must hold this lock before acting on an event. ``event_id`` is the
primary key, so the uniqueness that makes the lock a lock is enforced by the
database rather than by application logic: two concurrent workers attempting to
insert for the same event means exactly one wins on the PK constraint.

Expired locks are reclaimable, which is what makes a crashed job recoverable
instead of permanently wedging an event.

Acquire/release/reclaim behaviour lives in engine/locks.py (session 3); this
module only defines the row and the ``is_expired`` predicate it depends on.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TZDateTime, utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.risk_event import RiskEvent


class ActionLock(Base):
    """Exclusive, TTL-bounded claim on a single event."""

    __tablename__ = "action_locks"

    event_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("risk_events.id", ondelete="CASCADE"), primary_key=True
    )
    #: Worker identity — batch run id, scheduler job name, or request id.
    locked_by: Mapped[str] = mapped_column(String(80), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    #: TTL. Past this instant the lock is reclaimable by another worker.
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, index=True)

    event: Mapped["RiskEvent"] = relationship(back_populates="action_lock")

    def is_expired(self, now: datetime | None = None) -> bool:
        """True when the TTL has lapsed and the lock may be reclaimed."""
        return (now or utcnow()) >= self.expires_at

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<ActionLock event_id={self.event_id!r} by={self.locked_by!r} until={self.expires_at}>"
