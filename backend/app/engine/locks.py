"""ActionLock acquisition, release and TTL reclaim. BUILD_SPEC Section 4.

    "Any job must acquire this lock before acting on an event. Expired locks are
     reclaimable (handles crashed jobs)."

Uniqueness is enforced by the database, not by this module: ``ActionLock.
event_id`` is the primary key, so two workers racing to insert for the same
event means exactly one wins on the constraint. The logic here is about what to
do with that answer, and about the TTL that stops a crashed worker from wedging
an event forever.

Reclaiming an expired lock is AUDITED. A lock that outlived its TTL means a job
died mid-flight, which is exactly the sort of thing that should be visible in
the audit trail rather than silently papered over.

Transactions
------------
Like the state machine, nothing here commits — the caller owns the unit of work.
One consequence to know: :func:`acquire` flushes so the uniqueness constraint is
tested immediately, but until the caller commits, a concurrent transaction will
not see the lock. On SQLite with the single-writer model used here that is
sufficient; a multi-writer database would want ``SELECT ... FOR UPDATE``, and
the note is here so that is a deliberate choice later rather than a surprise.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import utcnow
from app.enums import AuditActor, AuditStage
from app.models.action_lock import ActionLock
from app.models.audit_log import AuditLog
from app.models.risk_event import RiskEvent

#: How long a worker may hold an event before the lock becomes reclaimable.
#: Long enough for a slow gateway call, short enough that a crashed batch does
#: not block the next run.
DEFAULT_LOCK_TTL = timedelta(minutes=5)

ACTION_LOCK_ACQUIRED = "lock_acquired"
ACTION_LOCK_RECLAIMED = "lock_reclaimed"
ACTION_LOCK_RELEASED = "lock_released"


class LockUnavailable(RuntimeError):
    """Raised when another worker holds a live lock on the event."""

    def __init__(self, event_id: str, held_by: str, expires_at: datetime) -> None:
        self.event_id = event_id
        self.held_by = held_by
        self.expires_at = expires_at
        super().__init__(
            f"event {event_id} is locked by {held_by!r} until {expires_at.isoformat()}"
        )


@dataclass(frozen=True)
class LockAcquisition:
    """A held lock, and whether taking it required reclaiming a dead one."""

    lock: ActionLock
    reclaimed: bool
    previous_holder: str | None = None


def _audit(
    session: Session,
    *,
    event_id: str,
    correlation_id: str,
    action: str,
    reasoning: str,
    before: object = None,
    after: object = None,
) -> None:
    session.add(
        AuditLog(
            event_id=event_id,
            correlation_id=correlation_id,
            actor=AuditActor.SYSTEM,
            stage=AuditStage.EXECUTION,
            action=action,
            before_state=before,
            after_state=after,
            reasoning=reasoning,
        )
    )
    session.flush()


def acquire(
    session: Session,
    event: RiskEvent,
    locked_by: str,
    *,
    ttl: timedelta = DEFAULT_LOCK_TTL,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> LockAcquisition:
    """Take the lock on ``event``, reclaiming it if the previous holder's TTL lapsed.

    Raises:
        LockUnavailable: another worker holds a lock that has not yet expired.
    """
    moment = now or utcnow()
    expires_at = moment + ttl
    correlation = correlation_id or event.correlation_id

    existing = session.get(ActionLock, event.id)

    if existing is None:
        lock = ActionLock(
            event_id=event.id, locked_by=locked_by, locked_at=moment, expires_at=expires_at
        )
        session.add(lock)
        try:
            session.flush()
        except IntegrityError:
            # Lost the race between the get() and the insert.
            session.rollback()
            existing = session.get(ActionLock, event.id)
            if existing is None:  # pragma: no cover - defensive
                raise
            raise LockUnavailable(event.id, existing.locked_by, existing.expires_at) from None
        return LockAcquisition(lock=lock, reclaimed=False)

    if not existing.is_expired(moment):
        raise LockUnavailable(event.id, existing.locked_by, existing.expires_at)

    # --- expired: the previous holder crashed or stalled. Reclaim, audibly. ---
    previous_holder = existing.locked_by
    previous_expiry = existing.expires_at
    existing.locked_by = locked_by
    existing.locked_at = moment
    existing.expires_at = expires_at
    session.flush()

    _audit(
        session,
        event_id=event.id,
        correlation_id=correlation,
        action=ACTION_LOCK_RECLAIMED,
        before=previous_holder,
        after=locked_by,
        reasoning=(
            f"Reclaimed expired lock from {previous_holder!r} "
            f"(TTL lapsed at {previous_expiry.isoformat()}). "
            "Previous holder is presumed crashed or stalled."
        ),
    )
    return LockAcquisition(lock=existing, reclaimed=True, previous_holder=previous_holder)


def release(
    session: Session,
    event: RiskEvent,
    locked_by: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Release the lock if ``locked_by`` holds it.

    A worker whose lock was already reclaimed by someone else must NOT delete
    the new holder's lock, so ownership is checked before deleting.

    Returns:
        True if this worker's lock was released, False if it no longer held one.
    """
    existing = session.get(ActionLock, event.id)
    if existing is None:
        return False
    if existing.locked_by != locked_by:
        return False
    session.delete(existing)
    session.flush()
    return True


def is_locked(session: Session, event_id: str, *, now: datetime | None = None) -> bool:
    """True when a live (unexpired) lock exists for the event."""
    existing = session.get(ActionLock, event_id)
    if existing is None:
        return False
    return not existing.is_expired(now or utcnow())


@contextmanager
def event_lock(
    session: Session,
    event: RiskEvent,
    locked_by: str,
    *,
    ttl: timedelta = DEFAULT_LOCK_TTL,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> Iterator[LockAcquisition]:
    """Hold the lock for the duration of a block, releasing it even on error.

        with event_lock(session, event, "batch_run_7") as held:
            ...

    Releasing in a ``finally`` matters: an exception mid-action would otherwise
    leave the event locked until the TTL expired, delaying every retry.
    """
    acquisition = acquire(
        session, event, locked_by, ttl=ttl, now=now, correlation_id=correlation_id
    )
    try:
        yield acquisition
    finally:
        release(session, event, locked_by, now=now)
