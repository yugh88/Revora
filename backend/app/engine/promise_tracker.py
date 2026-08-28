"""Promise-to-Pay lifecycle. BUILD_SPEC Section 4.

A customer who cannot pay today can say "I will pay by the 30th". That promise
is worth tracking: it is the difference between a customer who is engaged and
one who has gone quiet, and chasing the first as though they were the second is
how recovery turns into harassment.

WHAT IS STORED AND WHAT IS DERIVED
-----------------------------------
``PromiseToPay.status`` holds four persisted values — pending, kept, broken,
cancelled. Those are facts about what happened.

"Due soon" and "Overdue" are NOT stored. They are read off the promised date at
the moment someone looks, because they are statements about time rather than
events. A promise due tomorrow becomes due today without anything happening to
it, and a system that needed a scheduled job to notice that would be wrong for
however long the job was late.

The persisted ``broken`` transition still matters — it is what makes a broken
promise countable in batch metrics — so :func:`evaluate_overdue` writes it. That
runs from an explicit action, never from a read.

MONEY
-----
Fulfilment does NOT compute money. It calls the same ``upsert_outcome`` the
batch uses, so a rupee recovered through a promise is recorded by exactly the
code that records every other rupee. There is no second ledger and no separate
promise total: if this module tried to own an amount, the Overview and the
promise page would eventually disagree and one of them would be lying.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.enums import (
    AuditStage,
    Channel,
    EventStatus,
    OutcomeResolution,
    PromiseStatus,
)
from app.models import Outcome, PromiseToPay, RiskEvent

logger = logging.getLogger("revora.promises")

#: A promise inside this window reads as "Due soon". Two days is short enough to
#: be actionable and long enough that a merchant sees it before the date, not on it.
DUE_SOON_DAYS = 2

#: Merchant-facing states. Derived, never stored — see the module docstring.
PROMISED = "promised"
DUE_SOON = "due_soon"
FULFILLED = "fulfilled"
OVERDUE = "overdue"
CANCELLED = "cancelled"


class PromiseError(RuntimeError):
    """A promise cannot move the way the caller asked."""


def display_status(promise: PromiseToPay, *, now: datetime | None = None) -> str:
    """The state a merchant should see.

    A pending promise whose date has passed reads as Overdue immediately, even
    before anything has written ``broken``. Showing "Promised" for a date that
    is gone would be technically faithful to the row and useless to the person
    reading it.
    """
    moment = now or utcnow()

    if promise.status == PromiseStatus.KEPT:
        return FULFILLED
    if promise.status == PromiseStatus.CANCELLED:
        return CANCELLED
    if promise.status == PromiseStatus.BROKEN:
        return OVERDUE

    if promise.promised_date < moment:
        return OVERDUE
    if promise.promised_date <= moment + timedelta(days=DUE_SOON_DAYS):
        return DUE_SOON
    return PROMISED


def create_promise(
    session: Session,
    event: RiskEvent,
    *,
    promised_amount: Decimal,
    promised_date: datetime,
    now: datetime | None = None,
) -> PromiseToPay:
    """Record a customer's promise against the recovery case that prompted it.

    Bound to an event on purpose. A promise with no case behind it cannot be
    explained later — "why does this customer owe us?" has to have an answer.
    """
    moment = now or utcnow()

    if promised_amount <= 0:
        raise PromiseError("A promise must be for a positive amount.")
    if promised_amount > event.amount:
        raise PromiseError(
            "A promise cannot be for more than the amount at risk on the case."
        )
    if promised_date < moment:
        raise PromiseError("A promise date cannot be in the past.")

    # A closed case cannot be reopened by a promise.
    #
    # RECOVERED and UNRECOVERABLE are terminal in the state machine, and that
    # terminality is a guarantee other code relies on. Accepting a promise here
    # would leave fulfilment with two bad options: skip the transition and let
    # the ledger say "recovered" while the case says "unrecoverable", or force a
    # transition the state machine forbids. Refusing up front is the only answer
    # that keeps both consistent.
    if event.status in (EventStatus.RECOVERED, EventStatus.UNRECOVERABLE):
        raise PromiseError(
            "This case is already closed, so there is nothing left to promise."
        )

    existing = session.execute(
        select(PromiseToPay).where(
            PromiseToPay.event_id == event.id,
            PromiseToPay.status == PromiseStatus.PENDING,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise PromiseError(
            "This case already has an open promise. Cancel it before recording another."
        )

    promise = PromiseToPay(
        event_id=event.id,
        promised_amount=promised_amount,
        promised_date=promised_date,
        status=PromiseStatus.PENDING,
        created_at=moment,
    )
    session.add(promise)

    _audit(
        session,
        event,
        stage=AuditStage.EXECUTION,
        action="promise_to_pay_recorded",
        reasoning=(
            f"The customer said they would pay {promised_amount} by "
            f"{promised_date.date().isoformat()}. Recovery pauses until then."
        ),
    )
    session.commit()
    return promise


def fulfil_promise(
    session: Session,
    promise: PromiseToPay,
    *,
    paid_amount: Decimal | None = None,
    now: datetime | None = None,
) -> PromiseToPay:
    """Record that the promised payment actually arrived.

    Only called once a payment has been confirmed. This function does not decide
    that a payment succeeded — it records the consequence of one, which is why
    it cannot be used to invent a recovery.

    Idempotent: fulfilling an already-fulfilled promise is a no-op rather than a
    second recovery, because ``upsert_outcome`` overwrites the ledger row for
    the event instead of adding to it.
    """
    from app.routers.batch import upsert_outcome  # local: avoids a cycle

    moment = now or utcnow()

    if promise.status == PromiseStatus.KEPT:
        return promise  # already recorded; do not double-count
    if promise.status == PromiseStatus.CANCELLED:
        raise PromiseError("A cancelled promise cannot be fulfilled.")

    event = session.get(RiskEvent, promise.event_id)
    if event is None:  # pragma: no cover - referential integrity guards this
        raise PromiseError("The recovery case for this promise no longer exists.")

    amount = paid_amount if paid_amount is not None else promise.promised_amount
    if amount <= 0:
        raise PromiseError("A fulfilling payment must be for a positive amount.")

    promise.status = PromiseStatus.KEPT
    promise.resolved_at = moment

    # A payment smaller than the amount at risk settles the promise but NOT the
    # case: the remainder is still owed. Recording it as fully RECOVERED would
    # make that balance disappear from the ledger — the amounts would stop
    # summing to the amount at risk, and money would be lost from the books
    # rather than from the business.
    fully_settled = amount >= event.amount

    # The ONLY money write, and it is the batch's own. Channel EXTERNAL because
    # the customer paid of their own accord rather than through an action Revora
    # executed.
    upsert_outcome(
        session,
        event,
        resolved=(
            OutcomeResolution.RECOVERED
            if fully_settled
            else OutcomeResolution.PARTIALLY_RECOVERED
        ),
        amount_recovered=amount,
        channel=Channel.EXTERNAL,
        now=moment,
    )

    if event.status == EventStatus.UNRECOVERABLE:
        # Should be unreachable: create_promise refuses closed cases. Guarding
        # anyway, because writing a recovery against a case the engine has
        # written off would put the ledger and the state machine into open
        # disagreement.
        raise PromiseError(
            "This case has been closed as unrecoverable and cannot be settled by a promise."
        )

    # Only a full settlement closes the case. A partial payment keeps it open,
    # because there is still money to recover and marking it terminal would stop
    # Revora working something that is genuinely outstanding.
    if fully_settled and event.status != EventStatus.RECOVERED:
        from app.engine.state_machine import transition

        transition(
            session,
            event,
            EventStatus.RECOVERED,
            reasoning=(
                "The customer paid the amount they promised, so the case is "
                "settled."
            ),
            stage=AuditStage.RECOVERY,
            action="promise_fulfilled",
        )

    _audit(
        session,
        event,
        stage=AuditStage.RECOVERY,
        action="promise_fulfilled",
        reasoning=(
            f"The promised payment of {amount} arrived and was verified."
            if fully_settled
            else (
                f"The promised payment of {amount} arrived and was verified. "
                f"{event.amount - amount} of this case is still outstanding."
            )
        ),
    )
    session.commit()
    return promise


def cancel_promise(
    session: Session, promise: PromiseToPay, *, now: datetime | None = None
) -> PromiseToPay:
    """Withdraw a promise before its date.

    A cancelled promise is not a broken one. Nothing was owed on it, so it must
    never turn into a recovery.
    """
    moment = now or utcnow()

    if promise.status == PromiseStatus.KEPT:
        raise PromiseError("A fulfilled promise cannot be cancelled.")
    if promise.status == PromiseStatus.CANCELLED:
        return promise

    promise.status = PromiseStatus.CANCELLED
    promise.resolved_at = moment

    event = session.get(RiskEvent, promise.event_id)
    if event is not None:
        _audit(
            session,
            event,
            stage=AuditStage.VERIFICATION,
            action="promise_cancelled",
            reasoning="The promise was withdrawn before its date. No payment is expected.",
        )
    session.commit()
    return promise


def evaluate_overdue(session: Session, *, now: datetime | None = None) -> list[PromiseToPay]:
    """Persist ``broken`` for promises whose date has passed unpaid.

    Display already derives Overdue from the date, so this is not what makes a
    merchant see the right thing. It is what makes a broken promise COUNTABLE —
    batch metrics read the stored status — and what puts the fact in the audit
    trail at a definite moment.

    Runs from an explicit action, never from a read: a GET that quietly wrote
    rows would make the audit trail depend on who happened to open which page.
    """
    moment = now or utcnow()

    overdue = [
        promise
        for promise in session.execute(
            select(PromiseToPay).where(PromiseToPay.status == PromiseStatus.PENDING)
        ).scalars()
        if promise.promised_date < moment
    ]

    for promise in overdue:
        event = session.get(RiskEvent, promise.event_id)
        # A promise on an already-settled case is not broken — the money came in
        # by some other route, which is a good outcome, not a failure.
        settled = session.get(Outcome, promise.event_id)
        if settled is not None and settled.resolved == OutcomeResolution.RECOVERED:
            promise.status = PromiseStatus.KEPT
            promise.resolved_at = moment
            continue

        promise.status = PromiseStatus.BROKEN
        promise.resolved_at = moment
        if event is not None:
            _audit(
                session,
                event,
                stage=AuditStage.VERIFICATION,
                action="promise_broken",
                reasoning=(
                    f"The promised date of {promise.promised_date.date().isoformat()} "
                    "passed without the payment arriving."
                ),
            )

    if overdue:
        session.commit()
    return overdue


def _audit(session: Session, event: RiskEvent, **kwargs) -> None:
    """Write through the batch's own audit helper, so entries look identical."""
    from app.routers.batch import audit

    audit(session, event, **kwargs)
