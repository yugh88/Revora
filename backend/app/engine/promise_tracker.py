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
import re
from dataclasses import dataclass
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


# --------------------------------------------------------------------------- #
# Interpreting what a customer said
# --------------------------------------------------------------------------- #

#: Phrases that carry a commitment, in the registers Indian customers actually
#: use — English, Hinglish, and Hindi transliterated into Latin script.
_PROMISE_MARKERS = (
    "i will pay",
    "i'll pay",
    "ill pay",
    "will pay",
    "can pay",
    "shall pay",
    "payment kar dunga",
    "payment kar dungi",
    "kar dunga",
    "kar dungi",
    "pay kar dunga",
    "de dunga",
    "bhej dunga",
    "payment karunga",
    "tak payment",
    "promise to pay",
)

_PAID_MARKERS = (
    "already paid",
    "already done",
    "payment done",
    "kar diya",
    "ho gaya",
    "i have paid",
    "paid it",
)

_REFUSAL_MARKERS = ("cannot pay", "can't pay", "wont pay", "won't pay", "nahi kar", "not paying")

_RELATIVE_DAYS = {
    "today": 0,
    "aaj": 0,
    "tomorrow": 1,
    "kal": 1,
    "day after tomorrow": 2,
    "parso": 2,
    "next week": 7,
    "agle hafte": 7,
    "month end": 30,
    "mahine ke end": 30,
}

_WEEKDAYS = {
    "monday": 0, "somvar": 0, "tuesday": 1, "mangalvar": 1, "wednesday": 2,
    "budhvar": 2, "thursday": 3, "guruvar": 3, "friday": 4, "shukravar": 4,
    "saturday": 5, "shanivar": 5, "sunday": 6, "ravivar": 6,
}

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}


@dataclass(frozen=True)
class ResponseReading:
    """What a customer's reply appears to mean.

    ``confidence`` is deliberately coarse. This is pattern matching over a fixed
    phrase list, not language understanding, and dressing it up with a precise
    number would misrepresent how much it knows.
    """

    intent: str  # promise_to_pay | paid | refused | unclear
    promised_date: datetime | None
    confidence: float
    original_text: str


def interpret_response(
    text: str, *, now: datetime | None = None
) -> ResponseReading:
    """Read a simulated customer reply.

    Deterministic and offline. BUILD_SPEC forbids an LLM in the decision path,
    and this sits close enough to it that the same rule applies: a model that
    hallucinated a payment date would create a promise nobody made, and Revora
    would then pause recovery on the strength of it.

    A date is only ever returned when the text actually contains one. When
    someone says "I'll pay soon", the intent is a promise and the date is None —
    inventing "in three days" would be fabricating a commitment, and every
    downstream decision would inherit that fiction.
    """
    moment = now or utcnow()
    lowered = " ".join(text.lower().split())

    if any(marker in lowered for marker in _PAID_MARKERS):
        return ResponseReading("paid", None, 0.8, text)
    if any(marker in lowered for marker in _REFUSAL_MARKERS):
        return ResponseReading("refused", None, 0.8, text)
    if not any(marker in lowered for marker in _PROMISE_MARKERS):
        return ResponseReading("unclear", None, 0.0, text)

    promised = _extract_date(lowered, moment)
    # A commitment with no date is still a commitment, but it cannot be
    # tracked to a day — so it is reported honestly and handled as ambiguous.
    confidence = 0.85 if promised is not None else 0.4
    return ResponseReading("promise_to_pay", promised, confidence, text)


def _extract_date(lowered: str, now: datetime) -> datetime | None:
    """Find a date in the text, or return None. Never guesses."""
    for phrase, offset in _RELATIVE_DAYS.items():
        if phrase in lowered:
            return (now + timedelta(days=offset)).replace(
                hour=12, minute=0, second=0, microsecond=0
            )

    # A named weekday: "on Friday", "shukravar ko".
    for name, index in _WEEKDAYS.items():
        if name in lowered:
            ahead = (index - now.weekday()) % 7 or 7
            return (now + timedelta(days=ahead)).replace(
                hour=12, minute=0, second=0, microsecond=0
            )

    # "3 September", "3rd Sept", "September 3".
    #
    # Every candidate is tried rather than only the first. "pay by 3 September"
    # offers "by 3" before "3 september", and returning on the first
    # non-month word would throw away a date that is plainly there.
    # Two separate passes, not one alternation. A single pattern consumes
    # "by 3" in "pay by 3 September" and resumes past the digits, so the real
    # date is never seen. Scanning each ordering independently avoids that.
    candidates: list[tuple[int, str]] = [
        (int(m.group(1)), m.group(2))
        for m in re.finditer(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+([a-z]+)", lowered)
    ]
    candidates += [
        (int(m.group(2)), m.group(1))
        for m in re.finditer(r"([a-z]+)\s+(\d{1,2})\b", lowered)
    ]

    for day, month_word in candidates:
        month = _MONTHS.get(month_word)
        if not month or not 1 <= day <= 31:
            continue
        try:
            candidate = now.replace(
                year=now.year, month=month, day=day, hour=12, minute=0,
                second=0, microsecond=0,
            )
        except ValueError:
            continue
        # A date already past means next year — nobody promises backwards.
        if candidate < now:
            try:
                candidate = candidate.replace(year=now.year + 1)
            except ValueError:
                continue
        return candidate

    # "in 3 days", "3 din mein"
    match = re.search(r"(?:in|within)\s+(\d{1,2})\s+days?|(\d{1,2})\s+din", lowered)
    if match:
        days = int(match.group(1) or match.group(2))
        if 0 < days <= 90:
            return (now + timedelta(days=days)).replace(
                hour=12, minute=0, second=0, microsecond=0
            )

    return None


def has_open_promise(session: Session, event_id: str, *, now: datetime | None = None) -> bool:
    """Is this case waiting on a commitment the customer has not yet missed?

    Used by the recovery engine to hold off. Chasing someone who has told you
    when they will pay, before that date arrives, is exactly the behaviour a
    recovery agent has to avoid to stay welcome.
    """
    moment = now or utcnow()
    promise = session.execute(
        select(PromiseToPay).where(
            PromiseToPay.event_id == event_id,
            PromiseToPay.status == PromiseStatus.PENDING,
        )
    ).scalars().first()
    return promise is not None and promise.promised_date >= moment
