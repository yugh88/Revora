"""Idempotency keys and duplicate-execution prevention. BUILD_SPEC Section 9.

    "every execution request carries a key (hash of event+attempt); check
     `PaymentAttempt` for existing row before executing — return existing result
     if found, never re-execute."

Two layers, deliberately:

1. :func:`find_existing_attempt` is the application-level check Section 9
   describes — look before you leap.
2. The UNIQUE constraint on ``PaymentAttempt.idempotency_key`` (Session 1) is
   the backstop. If two workers pass the check simultaneously, the database
   refuses the second insert. :func:`record_attempt` catches that IntegrityError
   and returns the winner's row, because losing that race is not an error — it
   means the work was already done.

Relying on the check alone would be a time-of-check-to-time-of-use bug; relying
on the constraint alone would turn a normal duplicate into an exception the
caller has to handle. Both together mean a duplicate request is simply a no-op
that returns the original result.

Key composition
---------------
``sha256(event_id | attempt_number | action_code)``, hex. Section 9 specifies
"hash of event+attempt"; the action code is included because Section 6 permits
different actions at the same attempt number for the same event, and two
genuinely different actions must not collide onto one key. The hash is stable
across processes and restarts — no salt, no randomness, no clock — so a replayed
batch computes the identical key and correctly finds the existing row.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import utcnow
from app.enums import GatewayUsed, PaymentAttemptStatus
from app.models.payment_attempt import PaymentAttempt

#: Separator that cannot appear in an event id, attempt number or action code,
#: so distinct inputs cannot produce the same joined string.
_SEPARATOR = "|"


def build_key(event_id: str, attempt_number: int, action_code: str) -> str:
    """Deterministic idempotency key for one (event, attempt, action).

    Args:
        event_id: The RiskEvent id.
        attempt_number: 1-based attempt counter.
        action_code: The action about to be executed.

    Returns:
        64-character hex digest.
    """
    if attempt_number < 1:
        raise ValueError(f"attempt_number must be >= 1, got {attempt_number}")
    payload = _SEPARATOR.join([event_id, str(attempt_number), action_code])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def find_existing_attempt(session: Session, idempotency_key: str) -> PaymentAttempt | None:
    """The attempt already recorded under this key, if any.

    Section 9's pre-execution check: a non-None result means the work has been
    done and its outcome must be returned rather than repeated.
    """
    return session.execute(
        select(PaymentAttempt).where(PaymentAttempt.idempotency_key == idempotency_key)
    ).scalar_one_or_none()


def next_attempt_number(session: Session, event_id: str) -> int:
    """The attempt number a new execution for this event would take.

    Derived from the recorded attempts rather than a counter, so it stays
    correct even if a caller skips a number or rows are inserted concurrently.
    """
    highest = session.execute(
        select(func.max(PaymentAttempt.attempt_number)).where(
            PaymentAttempt.event_id == event_id
        )
    ).scalar_one()
    return 1 if highest is None else int(highest) + 1


def has_executed(session: Session, event_id: str, attempt_number: int, action_code: str) -> bool:
    """Whether this exact (event, attempt, action) has already been executed."""
    key = build_key(event_id, attempt_number, action_code)
    return find_existing_attempt(session, key) is not None


def record_attempt(
    session: Session,
    *,
    event_id: str,
    attempt_number: int,
    action_code: str,
    gateway_used: GatewayUsed = GatewayUsed.LOCAL_SIMULATION,
    status: PaymentAttemptStatus = PaymentAttemptStatus.PENDING,
    failure_reason: str | None = None,
    provider_ref: str | None = None,
    now: datetime | None = None,
) -> tuple[PaymentAttempt, bool]:
    """Record an execution attempt, or return the existing one for this key.

    Returns:
        ``(attempt, created)``. ``created`` is False when the key had already
        been used — the caller must then return the existing result rather than
        executing again.
    """
    key = build_key(event_id, attempt_number, action_code)

    existing = find_existing_attempt(session, key)
    if existing is not None:
        return existing, False

    attempt = PaymentAttempt(
        event_id=event_id,
        attempt_number=attempt_number,
        status=status,
        failure_reason=failure_reason,
        idempotency_key=key,
        provider_ref=provider_ref,
        gateway_used=gateway_used,
        initiated_at=now or utcnow(),
    )
    session.add(attempt)
    try:
        session.flush()
    except IntegrityError:
        # Another worker inserted the same key between the check and the flush.
        # Losing that race means the work is already done, which is the correct
        # outcome, not a failure.
        session.rollback()
        winner = find_existing_attempt(session, key)
        if winner is None:  # pragma: no cover - defensive
            raise
        return winner, False

    return attempt, True
