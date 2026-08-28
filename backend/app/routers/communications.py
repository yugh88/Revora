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
from datetime import datetime, timedelta, timezone

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


def recommend_channel(
    session: Session, event: RiskEvent, decision: Decision | None
) -> tuple[Channel, str]:
    """Choose how to reach this customer, and say why in plain language.

    Revora is the agent; a merchant should not have to decide between email, SMS
    and voice. This is NOT a second model. It reads signals the system already
    holds, in a fixed order of authority:

      1. the action the decision engine chose — already scored, already
         policy-gated, and it implies a channel
      2. the customer's own recorded preference
      3. escalation level, because a case that has been escalated warrants a
         more direct channel than another email

    Rules, not learning. A separate classifier for something the decision engine
    has effectively already decided would be a second opinion nobody asked for
    and a second thing to keep honest.
    """
    customer = session.get(CustomerProfile, event.customer_id)
    state = session.get(StoppingRuleState, event.id)
    escalation = int(getattr(state, "escalation_level", 0) or 0)

    action = decision.action_code if decision else None
    implied = ACTION_CHANNEL.get(action or "")

    # An escalated case has already been emailed without result. Continuing to
    # email it is how recovery becomes noise.
    if escalation >= 2:
        return (
            Channel.VOICE_SCRIPT,
            "This case has been escalated, so Revora chose a call rather than "
            "another message.",
        )

    if implied is not None and implied in CONTACT_CHANNELS:
        reason = {
            Channel.EMAIL: "The recovery action Revora chose needs a link the customer can open, so email fits best.",
            Channel.SMS: "A short reminder is enough here, so Revora chose a text message.",
            Channel.VOICE_SCRIPT: "This case needs a conversation, so Revora chose a call.",
            Channel.IN_APP: "The customer is mid-session, so Revora chose an in-app message.",
        }[implied]
        return implied, reason

    preferred = getattr(customer, "preferred_channel", None)
    if preferred in CONTACT_CHANNELS:
        return (
            preferred,
            "Revora used the channel this customer has responded on before.",
        )

    return (
        Channel.EMAIL,
        "No stronger signal was available, so Revora chose email as the least "
        "intrusive way to reach this customer.",
    )


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
        channel_reason=record.channel_reason,
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
    since: datetime | None = Query(
        default=None, description="Only contacts prepared at or after this instant."
    ),
    limit: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db),
) -> CommunicationListResponse:
    """Recovery contact history, newest first. Read-only."""
    stmt = select(CommunicationLog).order_by(CommunicationLog.created_at.desc())
    if channel is not None:
        stmt = stmt.where(CommunicationLog.channel == channel)
    if since is not None:
        # Naive bounds are read as UTC rather than rejected: a query filter must
        # never surface a backend exception to someone who cannot act on it.
        moment = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        stmt = stmt.where(CommunicationLog.created_at >= moment)

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

    # The agent picks the channel unless the caller overrides it, and always
    # records why — a merchant should be able to see the reasoning, not just
    # the outcome.
    recommended, why = recommend_channel(session, event, decision)
    channel = body.channel or recommended
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
        channel_reason=why if body.channel is None else "You chose this channel.",
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
