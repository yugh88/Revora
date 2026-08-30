"""Promise-to-Pay endpoints. BUILD_SPEC Sections 4 and 10.

Thin over ``engine/promise_tracker.py``: this module shapes requests and
responses and does not decide anything. In particular it never computes money —
fulfilment records recovery through the batch's own ``upsert_outcome``, so a
rupee recovered via a promise is written by exactly the code that writes every
other rupee.

Everything here is safe. Recording a promise contacts nobody, and "fulfil" does
not move money: it records that a payment already confirmed in the simulation
arrived. There is no path from this router to a real charge.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.engine import promise_tracker
from app.engine.promise_tracker import PromiseError, display_status
from app.enums import OutcomeResolution
from app.models import Outcome, PromiseToPay, RiskEvent
from app.schemas.promise_to_pay import (
    PromiseCreate,
    PromiseFulfil,
    PromiseListResponse,
    PromiseOut,
)

logger = logging.getLogger("revora.promises")

router = APIRouter(tags=["promises"])


#: What Revora does next, keyed on the state a merchant can see.
_NEXT_STEP = {
    "promised": "Recovery is paused until the promised date.",
    "due_soon": "Revora will check for the payment on the promised date.",
    "fulfilled": "Nothing further — the payment arrived and was verified.",
    "overdue": "Recovery has resumed. Revora will choose the next action.",
    "cancelled": "Nothing further — the promise was withdrawn.",
}


def _to_out(session: Session, promise: PromiseToPay) -> PromiseOut:
    from app.models import CommunicationLog

    event = session.get(RiskEvent, promise.event_id)
    source = session.execute(
        select(CommunicationLog).where(CommunicationLog.promise_id == promise.id)
    ).scalars().first()
    outcome = session.get(Outcome, promise.event_id)
    raw = event.raw_signal if event and isinstance(event.raw_signal, dict) else {}
    name = raw.get("customer_name") or (event.customer_id if event else "Customer")

    return PromiseOut(
        id=promise.id,
        customer_name=str(name),
        promised_amount=str(promise.promised_amount),
        currency=event.currency if event else "INR",
        promised_date=promise.promised_date.isoformat(),
        created_at=promise.created_at.isoformat(),
        resolved_at=promise.resolved_at.isoformat() if promise.resolved_at else None,
        status=display_status(promise),
        source_response=source.reply_text if source else None,
        next_step=_NEXT_STEP.get(display_status(promise), ""),
        event_id=promise.event_id,
        event_type=event.type,  # type: ignore[union-attr]
        amount_at_risk=str(event.amount) if event else "0.00",
        # Read back from the ledger, not inferred from the promise — the two
        # must not be able to disagree about whether money arrived.
        recovered=bool(outcome and outcome.resolved == OutcomeResolution.RECOVERED),
        amount_recovered=str(outcome.amount_recovered) if outcome else "0.00",
    )


@router.get("/promises", response_model=PromiseListResponse, summary="Promises to pay")
def list_promises(
    status: str | None = Query(default=None, description="Filter by displayed status."),
    since: datetime | None = Query(
        default=None, description="Only promises made at or after this instant."
    ),
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db),
) -> PromiseListResponse:
    """Every promise, newest first. Read-only — no status is written here.

    ``since`` is what lets the Promises page and the Overview summary agree:
    both ask the same endpoint for the same window, so neither can quietly show
    a different total.
    """
    stmt = select(PromiseToPay).order_by(PromiseToPay.created_at.desc())
    if since is not None:
        moment = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        stmt = stmt.where(PromiseToPay.created_at >= moment)
    rows = list(session.execute(stmt.limit(limit)).scalars())
    items = [_to_out(session, row) for row in rows]

    if status:
        items = [item for item in items if item.status == status]

    breakdown: dict[str, int] = {}
    for item in items:
        breakdown[item.status] = breakdown.get(item.status, 0) + 1

    promised = sum((Decimal(item.promised_amount) for item in items), Decimal("0.00"))
    fulfilled = sum(
        (Decimal(item.promised_amount) for item in items if item.status == "fulfilled"),
        Decimal("0.00"),
    )

    return PromiseListResponse(
        total=len(items),
        status_breakdown=breakdown,
        total_promised=str(promised.quantize(Decimal("0.01"))),
        total_fulfilled=str(fulfilled.quantize(Decimal("0.01"))),
        items=items,
    )


@router.get("/promises/{promise_id}", response_model=PromiseOut, summary="One promise")
def get_promise(promise_id: str, session: Session = Depends(get_db)) -> PromiseOut:
    promise = session.get(PromiseToPay, promise_id)
    if promise is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="That promise is no longer available.",
        )
    return _to_out(session, promise)


@router.post(
    "/promises",
    response_model=PromiseOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="Record a customer's promise to pay",
)
def create_promise(body: PromiseCreate, session: Session = Depends(get_db)) -> PromiseOut:
    """Record that a customer committed to pay by a date.

    Contacts nobody and moves no money. It pauses recovery on the case until
    the promised date, which is the whole point: a customer who has told you
    when they will pay should not be chased in the meantime.
    """
    event = session.get(RiskEvent, body.event_id)
    if event is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="That recovery case could not be found.",
        )

    try:
        promise = promise_tracker.create_promise(
            session,
            event,
            promised_amount=body.promised_amount,
            promised_date=body.promised_date,
        )
    except PromiseError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return _to_out(session, promise)


@router.post(
    "/promises/{promise_id}/fulfil",
    response_model=PromiseOut,
    summary="Record that the promised payment arrived",
)
def fulfil_promise(
    promise_id: str,
    body: PromiseFulfil | None = None,
    session: Session = Depends(get_db),
) -> PromiseOut:
    """Record a confirmed payment against a promise.

    This does not charge anything. In the demo the payment is simulated; this
    endpoint records its consequence — the ledger entry and the case moving to
    recovered — through the same write path the batch uses.

    Calling it twice is safe: the second call is a no-op rather than a second
    recovery.
    """
    promise = session.get(PromiseToPay, promise_id)
    if promise is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="That promise is no longer available.",
        )

    try:
        promise_tracker.fulfil_promise(
            session, promise, paid_amount=body.paid_amount if body else None
        )
    except PromiseError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return _to_out(session, promise)


@router.post(
    "/promises/{promise_id}/cancel", response_model=PromiseOut, summary="Withdraw a promise"
)
def cancel_promise(promise_id: str, session: Session = Depends(get_db)) -> PromiseOut:
    """Withdraw a promise. A cancelled promise never becomes a recovery."""
    promise = session.get(PromiseToPay, promise_id)
    if promise is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="That promise is no longer available.",
        )

    try:
        promise_tracker.cancel_promise(session, promise)
    except PromiseError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return _to_out(session, promise)


@router.post(
    "/promises/evaluate",
    response_model=PromiseListResponse,
    summary="Mark passed promises as broken",
)
def evaluate_promises(session: Session = Depends(get_db)) -> PromiseListResponse:
    """Record the promises whose date has passed unpaid.

    Display already derives Overdue from the date, so this changes nothing a
    merchant can see. It writes the fact down — which is what makes a broken
    promise countable and puts it in the audit trail at a definite moment.

    Deliberately a POST. A GET that wrote rows would make the audit trail depend
    on who opened which page.
    """
    promise_tracker.evaluate_overdue(session)
    return list_promises(status=None, since=None, limit=500, session=session)
