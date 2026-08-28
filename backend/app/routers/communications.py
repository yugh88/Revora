"""Email, SMS and Voice recovery contacts. BUILD_SPEC Sections 7 and 10.

    revenue at risk → Revora decides to make contact → it writes the message
    → a channel is chosen → the contact is recorded → a customer response can
    be simulated → a promise to pay may follow → the payment is verified

NOTHING IS SENT
---------------
Revora has no email, SMS or voice provider. Every record this module creates is
marked simulated, and the status enum has no "sent" or "delivered" value, so the
UI has nothing to render that could imply a customer heard from anyone.

Razorpay Test Mode does not change this. A payment sandbox tests payments; it
says nothing about message delivery, and treating the two as the same thing
would let a demo claim contacts that never happened.

COMPLIANCE IS NOT BYPASSED
---------------------------
Message bodies come from the same template engine the Recovery Messages page
uses, which runs the full Section 7 gate first — contact hours, frequency cap,
urgency ceiling and the coercive-language blocklist. A refused message is stored
with status BLOCKED and an EMPTY body. Storing the text of a refused message
would let it be copied and sent, which is exactly what the gate exists to
prevent.

VOICE
-----
A voice contact is the script that would be read aloud. No audio is generated
and none is implied; representing the call and its words is the honest thing a
system without telephony can do.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db, utcnow
from app.engine import policy_engine, promise_tracker, template_engine
from app.engine.promise_tracker import PromiseError
from app.engine.template_engine import TemplateError
from app.enums import (
    Channel,
    CommunicationStatus,
    CustomerResponse,
    EventStatus,
)
from app.models import (
    CommunicationLog,
    CustomerProfile,
    Decision,
    Diagnosis,
    RiskEvent,
    StoppingRuleState,
)
from app.schemas.communication_log import (
    CommunicationListResponse,
    CommunicationOut,
    CommunicationPrepare,
    SimulatedResponse,
)

logger = logging.getLogger("revora.communications")

router = APIRouter(tags=["communications"])

#: Which channel each recovery action implies. Derived from the action the
#: engine already chose, so the channel is a consequence of the decision rather
#: than an unrelated pick.
ACTION_CHANNEL: dict[str, Channel] = {
    "update_card_email": Channel.EMAIL,
    "email_saved_cart": Channel.EMAIL,
    "friendly_reminder": Channel.EMAIL,
    "formal_notice": Channel.EMAIL,
    "sms_reminder": Channel.SMS,
    "retry_salary_window": Channel.SMS,
    "reauth_nudge": Channel.SMS,
    "in_app_nudge": Channel.IN_APP,
    "reminder_with_call_script": Channel.VOICE_SCRIPT,
    "human_handoff": Channel.VOICE_SCRIPT,
}

#: Channels a person can actually be reached on. The rest are internal outcomes.
CONTACT_CHANNELS = (Channel.EMAIL, Channel.SMS, Channel.VOICE_SCRIPT, Channel.IN_APP)


def _customer_name(event: RiskEvent) -> str:
    raw = event.raw_signal if isinstance(event.raw_signal, dict) else {}
    return str(raw.get("customer_name") or event.customer_id)


def _to_out(session: Session, record: CommunicationLog) -> CommunicationOut:
    event = session.get(RiskEvent, record.event_id)
    return CommunicationOut(
        id=record.id,
        customer_name=_customer_name(event) if event else "Customer",
        channel=record.channel,
        status=record.status.value,
        body=record.body,
        reason=record.reason,
        blocked_reason=record.blocked_reason,
        is_simulated=record.is_simulated,
        created_at=record.created_at.isoformat(),
        simulated_at=record.simulated_at.isoformat() if record.simulated_at else None,
        customer_response=record.customer_response,
        responded_at=record.responded_at.isoformat() if record.responded_at else None,
        promise_id=record.promise_id,
        event_id=record.event_id,
        event_type=event.type,  # type: ignore[union-attr]
        amount_at_risk=str(event.amount) if event else "0.00",
    )


@router.get(
    "/communications", response_model=CommunicationListResponse, summary="Recovery contacts"
)
def list_communications(
    channel: Channel | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_db),
) -> CommunicationListResponse:
    """Every recovery message, newest first. Read-only."""
    stmt = select(CommunicationLog).order_by(CommunicationLog.created_at.desc())
    if channel is not None:
        stmt = stmt.where(CommunicationLog.channel == channel)

    rows = list(session.execute(stmt.limit(limit)).scalars())
    items = [_to_out(session, row) for row in rows]

    channels: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for row in rows:
        channels[row.channel.value] = channels.get(row.channel.value, 0) + 1
        statuses[row.status.value] = statuses.get(row.status.value, 0) + 1

    return CommunicationListResponse(
        total=len(items),
        channel_breakdown=channels,
        status_breakdown=statuses,
        items=items,
    )


@router.post(
    "/communications/prepare",
    response_model=CommunicationOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="Write the recovery message for a case",
)
def prepare_communication(
    body: CommunicationPrepare, session: Session = Depends(get_db)
) -> CommunicationOut:
    """Write the message Revora would send, and record it.

    Runs the same compliance gate as the Recovery Messages page. A refused
    message is recorded as blocked with no text, so the refusal is visible and
    auditable rather than silently absent.

    Preparing contacts nobody.
    """
    event = session.get(RiskEvent, body.event_id)
    if event is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="That recovery case could not be found.",
        )

    decision = session.execute(
        select(Decision)
        .where(Decision.event_id == event.id)
        .order_by(Decision.decided_at.desc(), Decision.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    channel = body.channel or ACTION_CHANNEL.get(
        decision.action_code if decision else "", Channel.EMAIL
    )
    if channel not in CONTACT_CHANNELS:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="That is not a channel a customer can be reached on.",
        )

    diagnosis = session.get(Diagnosis, event.id)
    policy = policy_engine.resolve_policy(session, event)

    try:
        result = template_engine.generate_script(
            event=event,
            decision=decision,
            diagnosis=diagnosis,
            stopping_state=session.get(StoppingRuleState, event.id),
            policy=policy,
            customer=session.get(CustomerProfile, event.customer_id),
            channel=channel.value,
        )
    except TemplateError as exc:
        logger.exception(
            "communication_render_failed",
            extra={"event_id": event.id, "stage": "execution", "action": "prepare"},
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The recovery message could not be written.",
        ) from exc

    record = CommunicationLog(
        event_id=event.id,
        channel=channel,
        # A blocked message keeps NO body. Storing the text of something the
        # gate refused would let it be copied out and sent anyway.
        status=(
            CommunicationStatus.PREPARED if result.compliant else CommunicationStatus.BLOCKED
        ),
        body=result.script if result.compliant else "",
        reason=(diagnosis.root_cause_code.value if diagnosis else "unknown"),
        blocked_reason=None if result.compliant else result.failure_reason,
        is_simulated=True,
    )
    session.add(record)
    session.commit()
    return _to_out(session, record)


@router.post(
    "/communications/{communication_id}/simulate-send",
    response_model=CommunicationOut,
    summary="Represent sending the message — no customer is contacted",
)
def simulate_send(
    communication_id: str, session: Session = Depends(get_db)
) -> CommunicationOut:
    """Record that the demo represented sending this message.

    Named "simulate" everywhere, in the enum and in the response, because
    Revora cannot send anything and must not be able to claim it did.
    """
    record = session.get(CommunicationLog, communication_id)
    if record is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="That message is no longer available.",
        )
    if record.status == CommunicationStatus.BLOCKED:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="This message was blocked by your policy and cannot be sent.",
        )

    record.status = CommunicationStatus.SIMULATED
    record.simulated_at = utcnow()
    session.commit()
    return _to_out(session, record)


@router.post(
    "/communications/{communication_id}/simulate-response",
    response_model=CommunicationOut,
    summary="Represent how the customer replied",
)
def simulate_response(
    communication_id: str,
    body: SimulatedResponse,
    session: Session = Depends(get_db),
) -> CommunicationOut:
    """Record a simulated customer reply.

    A commitment to pay creates a real Promise to Pay against the same case,
    through the existing promise engine — which is how a promise comes to exist
    as a consequence of the conversation rather than something a merchant types
    in on the customer's behalf.

    Responses are only ever recorded here, explicitly. Nothing infers a reply
    from a message having been prepared or sent.
    """
    record = session.get(CommunicationLog, communication_id)
    if record is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="That message is no longer available.",
        )
    if record.status != CommunicationStatus.SIMULATED:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="A customer cannot respond to a message that has not been sent.",
        )

    event = session.get(RiskEvent, record.event_id)
    if event is None:  # pragma: no cover - referential integrity guards this
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="That recovery case could not be found.",
        )

    now = utcnow()
    record.customer_response = body.response
    record.responded_at = now

    if body.response == CustomerResponse.PROMISED_TO_PAY:
        if event.status in (EventStatus.RECOVERED, EventStatus.UNRECOVERABLE):
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="This case is already closed, so there is nothing left to promise.",
            )
        try:
            promise = promise_tracker.create_promise(
                session,
                event,
                promised_amount=body.promised_amount or event.amount,
                promised_date=body.promised_date or (now + timedelta(days=3)),
                now=now,
            )
        except PromiseError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        record.promise_id = promise.id

    session.commit()
    return _to_out(session, record)
