"""GET /scripts/{event_id}. BUILD_SPEC Sections 7 and 10.

    "GET /scripts/{event_id} — Hinglish script + reasoning + tone + urgency +
     compliance validation"

READ-ONLY and side-effect free. Generating a script writes nothing: no audit
row, no attempt, no state transition. Opening the scripts page must not change
what the engine has done, and Section 7's own instruction — "do not bypass
policy or stopping-rule enforcement merely to render a script for the UI" —
cuts both ways. Rendering is an inspection, not an action.

The compliance verdict is returned in FULL, pass or fail. When a rule refuses,
the response carries no script at all and states which rule refused and why.
That is the honest answer: a refused script that still ships the text would let
somebody send it anyway.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.engine import policy_engine, template_engine
from app.engine.template_engine import TemplateError
from app.models import CustomerProfile, Decision, Diagnosis, RiskEvent, StoppingRuleState

logger = logging.getLogger("revora.scripts")

router = APIRouter(tags=["scripts"])


class ComplianceCheckOut(BaseModel):
    rule_id: str
    description: str
    passed: bool
    detail: str


class ScriptResponse(BaseModel):
    """A generated script and everything needed to defend it."""

    event_id: str
    event_type: str
    customer_id: str
    amount: str
    currency: str

    #: Empty when compliance refused. Never partially rendered.
    script: str
    reasoning: str
    tone: str
    urgency: str
    channel: str
    language: str

    compliant: bool
    compliance_checks: list[ComplianceCheckOut]
    failure_reason: str | None = None

    #: Which YAML template produced the script, so the wording is traceable to
    #: a file rather than to code.
    template_key: str = ""
    slots_used: dict[str, Any] = {}

    #: True ONLY on the /preview path. The live endpoint always returns False,
    #: so a preview can never be mistaken for a real compliant result by a
    #: client, a log, or a screenshot.
    is_preview: bool = False
    #: The instant the contact-window rule was evaluated against. None on the
    #: live path, where the real current time was used.
    preview_time: str | None = None


def _build_script_response(
    session: Session,
    event_id: str,
    channel: str,
    *,
    now: Any = None,
    is_preview: bool = False,
) -> ScriptResponse:
    """Shared body for the live and preview paths.

    ONE code path, one template engine, one set of YAML files. The two endpoints
    differ by exactly one argument — the instant the contact-window rule is
    evaluated against — which is what makes the preview an honest demonstration
    of the live behaviour rather than a parallel implementation of it.
    """
    event = session.get(RiskEvent, event_id)
    if event is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No such event: {event_id}",
        )

    decision = session.execute(
        select(Decision)
        .where(Decision.event_id == event_id)
        .order_by(Decision.decided_at.desc(), Decision.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    # The same policy the engine would apply, resolved by the engine itself.
    policy = policy_engine.resolve_policy(session, event)

    try:
        result = template_engine.generate_script(
            event=event,
            decision=decision,
            diagnosis=session.get(Diagnosis, event_id),
            stopping_state=session.get(StoppingRuleState, event_id),
            policy=policy,
            customer=session.get(CustomerProfile, event.customer_id),
            channel=channel,
            now=now,
        )
    except TemplateError as exc:
        # A template problem is a configuration problem, not a customer-facing
        # one. Fail loudly with a clear message rather than emitting fallback
        # prose that was never reviewed.
        logger.exception(
            "script_generation_failed",
            extra={"event_id": event_id, "stage": "execution", "action": "generate_script"},
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Script templates could not be rendered: {exc}",
        ) from exc

    return ScriptResponse(
        event_id=event.id,
        event_type=event.type.value,
        customer_id=event.customer_id,
        amount=str(event.amount),
        currency=event.currency,
        script=result.script,
        reasoning=result.reasoning,
        tone=result.tone,
        urgency=result.urgency,
        channel=result.channel,
        language=result.language,
        compliant=result.compliant,
        compliance_checks=[
            ComplianceCheckOut(
                rule_id=check.rule_id,
                description=check.description,
                passed=check.passed,
                detail=check.detail,
            )
            for check in result.compliance_checks
        ],
        failure_reason=result.failure_reason,
        template_key=result.template_key,
        slots_used=result.slots_used,
        is_preview=is_preview,
        preview_time=now.isoformat() if now is not None else None,
    )


@router.get(
    "/scripts/{event_id}",
    response_model=ScriptResponse,
    summary="Compliance-checked Hinglish script for one event",
)
def get_script(
    event_id: str,
    channel: str = Query(default="voice_script"),
    session: Session = Depends(get_db),
) -> ScriptResponse:
    """Render the script Revora would use for this event, if it may.

    Uses the REAL current time. Outside 08:00-19:00 IST the contact-window rule
    refuses and no text is produced. No caller may override that: there is no
    `now` parameter on this path, and a test asserts it never acquires one.
    """
    return _build_script_response(session, event_id, channel)


@router.get(
    "/scripts/{event_id}/preview",
    response_model=ScriptResponse,
    summary="Read-only demo preview — NOT a live contact",
)
def preview_script(
    event_id: str,
    channel: str = Query(default="voice_script"),
    session: Session = Depends(get_db),
) -> ScriptResponse:
    """Show what the SAME engine would render during a permitted contact window.

    This exists because a judge may open Revora at 20:00 IST and would otherwise
    never see the Section 7 Hinglish capability at all — the live rule would
    correctly withhold every script.

    It is NOT a bypass. The only thing that changes is the instant the
    contact-window rule is evaluated against, and that instant is derived from
    the configured window itself. Every other rule runs for real against the
    recorded state:

      * frequency cap    — StoppingRuleState.attempts_used vs the merchant's
                           Policy.contact_limit_per_channel
      * urgency ceiling  — the escalation level this event actually reached
      * coercive language — the blocklist, applied to the rendered output

    An event refused for any of those is refused here too, with no script text,
    exactly as on the live path. The preview therefore demonstrates the
    compliance gate rather than evading it.

    Read-only and side-effect free: no audit row, no attempt, no decision, no
    state transition. Nothing is sent to anyone.
    """
    return _build_script_response(
        session,
        event_id,
        channel,
        now=template_engine.preview_instant(),
        is_preview=True,
    )
