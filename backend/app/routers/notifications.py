"""Merchant notifications. Derived, never stored.

Everything here is computed from state that already exists — the recovery
ledger, promises, stopping-rule state, policy verdicts. There is deliberately no
notifications table.

WHY NOT STORE THEM
-------------------
A stored notification is a copy of a fact, and copies drift. "Promise overdue"
written into a row on Tuesday keeps saying overdue after the customer pays on
Wednesday, and now the product contradicts itself: the alert says one thing and
the ledger says another. Deriving on read means a notification cannot outlive
the situation that justified it.

The cost is that "unread" has no server-side home, so the client tracks which
alerts it has already shown. That is the right trade: a wrong badge count is a
small annoyance, a notification that lies about money is not.

NOTHING IS INVENTED
--------------------
Every alert points at a real row and carries the identifier needed to open it.
If nothing has happened, the list is empty — there is no filler to make the
panel look busy.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db, utcnow
from app.engine.promise_tracker import display_status
from app.enums import (
    CommunicationStatus,
    EventStatus,
    OutcomeResolution,
    PolicyResultStatus,
)
from app.models import (
    CommunicationLog,
    Decision,
    Outcome,
    PromiseToPay,
    RiskEvent,
    StoppingRuleState,
)

logger = logging.getLogger("revora.notifications")

router = APIRouter(tags=["notifications"])


class Notification(BaseModel):
    """One thing worth a merchant's attention."""

    id: str
    kind: str
    title: str
    detail: str
    #: info | attention | good — drives tone, not urgency theatre.
    severity: str
    occurred_at: str
    #: Where clicking it should go.
    href: str


class NotificationListResponse(BaseModel):
    total: int
    items: list[Notification]


def _customer(event: RiskEvent | None) -> str:
    if event is None:
        return "A customer"
    raw = event.raw_signal if isinstance(event.raw_signal, dict) else {}
    return str(raw.get("customer_name") or event.customer_id)


def _money(value: Decimal | None) -> str:
    return f"₹{(value or Decimal('0')):,.0f}"


def collect(session: Session, limit: int = 40) -> list[Notification]:
    """Build the alert list from current state.

    Ordered newest first at the end. Each source below reads real rows and
    stops at ``limit`` overall, so a large ledger cannot turn the panel into a
    thousand-item scroll.
    """
    out: list[Notification] = []

    # --- promises: the most time-sensitive thing a merchant has ---
    for promise in session.execute(select(PromiseToPay)).scalars():
        event = session.get(RiskEvent, promise.event_id)
        status = display_status(promise)
        when = (promise.resolved_at or promise.created_at).isoformat()

        if status == "fulfilled":
            out.append(
                Notification(
                    id=f"promise_kept_{promise.id}",
                    kind="promise_fulfilled",
                    title=f"Promise fulfilled — {_money(promise.promised_amount)} recovered",
                    detail=f"{_customer(event)} paid the amount they committed to.",
                    severity="good",
                    occurred_at=when,
                    href="/promises",
                )
            )
        elif status == "overdue":
            out.append(
                Notification(
                    id=f"promise_overdue_{promise.id}",
                    kind="promise_overdue",
                    title="Promise overdue — review recommended",
                    detail=(
                        f"{_customer(event)} committed to pay "
                        f"{_money(promise.promised_amount)} by "
                        f"{promise.promised_date.date().isoformat()}, and it has not "
                        "been verified."
                    ),
                    severity="attention",
                    occurred_at=when,
                    href="/promises",
                )
            )
        elif status in ("promised", "due_soon"):
            out.append(
                Notification(
                    id=f"promise_made_{promise.id}",
                    kind="promise_made",
                    title=f"{_money(promise.promised_amount)} promised for "
                    f"{promise.promised_date.date().isoformat()}",
                    detail=f"{_customer(event)} told Revora when they expect to pay.",
                    severity="info",
                    occurred_at=when,
                    href="/promises",
                )
            )

    # --- money actually recovered ---
    recovered = session.execute(
        select(Outcome)
        .where(Outcome.resolved == OutcomeResolution.RECOVERED)
        .order_by(Outcome.resolved_at.desc())
        .limit(8)
    ).scalars()
    for outcome in recovered:
        event = session.get(RiskEvent, outcome.event_id)
        out.append(
            Notification(
                id=f"recovered_{outcome.event_id}",
                kind="revenue_recovered",
                title=f"{_money(outcome.amount_recovered)} recovered",
                detail=f"Payment recovered for {_customer(event)}.",
                severity="good",
                occurred_at=(outcome.resolved_at or utcnow()).isoformat(),
                href=f"/events/{outcome.event_id}",
            )
        )

    # --- cases a person has to look at ---
    escalated = session.execute(
        select(RiskEvent).where(RiskEvent.status == EventStatus.ESCALATED).limit(8)
    ).scalars()
    for event in escalated:
        out.append(
            Notification(
                id=f"escalated_{event.id}",
                kind="human_review",
                title="Human review required",
                detail=(
                    f"Revora could not safely resolve {_customer(event)}'s case on its "
                    "own and handed it over."
                ),
                severity="attention",
                occurred_at=event.detected_at.isoformat(),
                href=f"/events/{event.id}",
            )
        )

    # --- attempt limits reached ---
    exhausted = [
        state
        for state in session.execute(select(StoppingRuleState).limit(200)).scalars()
        if state.attempts_used >= state.max_attempts_for_type > 0
    ][:5]
    for state in exhausted:
        event = session.get(RiskEvent, state.event_id)
        out.append(
            Notification(
                id=f"attempts_{state.event_id}",
                kind="attempt_limit",
                title="Recovery attempt limit reached",
                detail=(
                    f"Revora has made every attempt your policy allows on "
                    f"{_customer(event)}'s case."
                ),
                severity="attention",
                occurred_at=(event.detected_at.isoformat() if event else utcnow().isoformat()),
                href=f"/events/{state.event_id}",
            )
        )

    # --- recovery Revora was not allowed to attempt ---
    blocked = [
        decision
        for decision in session.execute(
            select(Decision).order_by(Decision.decided_at.desc()).limit(120)
        ).scalars()
        if isinstance(decision.policy_result, dict)
        and decision.policy_result.get("status") == PolicyResultStatus.BLOCKED.value
    ][:5]
    for decision in blocked:
        event = session.get(RiskEvent, decision.event_id)
        out.append(
            Notification(
                id=f"blocked_{decision.id}",
                kind="policy_blocked",
                title="Recovery blocked by your policy",
                detail=(
                    f"Revora stopped short of contacting {_customer(event)} because "
                    "your limits did not allow it."
                ),
                severity="info",
                occurred_at=decision.decided_at.isoformat(),
                href=f"/events/{decision.event_id}",
            )
        )

    # --- messages policy would not permit ---
    held = session.execute(
        select(CommunicationLog)
        .where(CommunicationLog.status == CommunicationStatus.BLOCKED)
        .order_by(CommunicationLog.created_at.desc())
        .limit(4)
    ).scalars()
    for record in held:
        event = session.get(RiskEvent, record.event_id)
        out.append(
            Notification(
                id=f"held_{record.id}",
                kind="message_held",
                title="Recovery message held back",
                detail=(
                    f"A message to {_customer(event)} was not written because your "
                    "policy did not allow it."
                ),
                severity="info",
                occurred_at=record.created_at.isoformat(),
                href="/communications",
            )
        )

    out.sort(key=lambda item: item.occurred_at, reverse=True)
    return out[:limit]


@router.get(
    "/notifications", response_model=NotificationListResponse, summary="Merchant alerts"
)
def list_notifications(
    limit: int = Query(default=40, ge=1, le=100), session: Session = Depends(get_db)
) -> NotificationListResponse:
    """Everything worth a merchant's attention right now. Read-only.

    Computed fresh on every call, so an alert cannot outlive the situation that
    justified it. Empty when nothing has happened — there is no filler.
    """
    items = collect(session, limit=limit)
    return NotificationListResponse(total=len(items), items=items)
