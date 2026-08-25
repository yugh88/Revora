"""GET /exceptions — unresolved / low-confidence cases and why the engine did not act.
BUILD_SPEC Sections 4a, 9 and 10.

The taxonomy here is NOT new. Every reason code is read back from something the
Session 3 engines already recorded:

    ml_rule_disagreement    MLDiagnosisPrediction.agrees_with_rule_engine is False
    low_confidence_diagnosis Diagnosis.confidence below the engine's threshold
    policy_blocked          Decision.policy_result.rule_triggered
    hard_decline            StoppingRuleState.hard_stop_reason / hard-stop rule
    do_not_contact          policy rule
    cooldown                policy rule
    max_attempts            policy rule
    escalation_ceiling      policy rule
    escalated               event handed to a human, outcome still open

Inventing a second taxonomy would mean /exceptions could disagree with the audit
trail about why something happened, and then neither could be trusted.

One event can qualify under several reasons — a low-confidence diagnosis that
was also policy-blocked is common. Each qualifying reason is emitted as its own
row so the counts add up and a filter by reason returns everything relevant,
rather than one row per event with the other reasons silently dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.engine.diagnosis_engine import LOW_CONFIDENCE_THRESHOLD
from app.engine.policy_engine import (
    RULE_COOLDOWN,
    RULE_DO_NOT_CONTACT,
    RULE_ESCALATION_CEILING,
    RULE_HARD_STOP,
    RULE_MAX_ATTEMPTS,
)
from app.enums import EventStatus, PolicyResultStatus
from app.models import Decision, Diagnosis, MLDiagnosisPrediction, RiskEvent, StoppingRuleState
from app.schemas.batch import ExceptionItem, ExceptionsResponse

logger = logging.getLogger("revora.exceptions")

router = APIRouter(tags=["exceptions"])

# --- reason codes ---------------------------------------------------------- #

REASON_ML_DISAGREEMENT = "ml_rule_disagreement"
REASON_LOW_CONFIDENCE = "low_confidence_diagnosis"
REASON_POLICY_BLOCKED = "policy_blocked"
REASON_HARD_DECLINE = "hard_decline"
REASON_DO_NOT_CONTACT = "do_not_contact"
REASON_COOLDOWN = "cooldown"
REASON_MAX_ATTEMPTS = "max_attempts"
REASON_ESCALATION_CEILING = "escalation_ceiling"
REASON_ESCALATED = "escalated_to_human"

#: Policy rules that get their own reason code, because a judge asking "why
#: didn't it act?" wants "the customer opted out", not "policy_blocked".
RULE_TO_REASON: dict[str, str] = {
    RULE_HARD_STOP: REASON_HARD_DECLINE,
    RULE_DO_NOT_CONTACT: REASON_DO_NOT_CONTACT,
    RULE_COOLDOWN: REASON_COOLDOWN,
    RULE_MAX_ATTEMPTS: REASON_MAX_ATTEMPTS,
    RULE_ESCALATION_CEILING: REASON_ESCALATION_CEILING,
}

#: Human-readable explanation per reason code.
REASON_TEXT: dict[str, str] = {
    REASON_ML_DISAGREEMENT: "ML/rule disagreement — needs review",
    REASON_LOW_CONFIDENCE: "Root cause could not be determined confidently — needs review",
    REASON_POLICY_BLOCKED: "Policy gate blocked every permitted action",
    REASON_HARD_DECLINE: "Hard decline — no retry permitted, stopped immediately",
    REASON_DO_NOT_CONTACT: "Customer is marked do-not-contact — no action taken",
    REASON_COOLDOWN: "Cooldown window active — contacting again would breach the policy",
    REASON_MAX_ATTEMPTS: "Attempt limit reached for this event type",
    REASON_ESCALATION_CEILING: "Escalation ceiling reached — will not auto-escalate further",
    REASON_ESCALATED: "Handed to a human — commercial outcome still open",
}


@dataclass
class _Context:
    """Everything already recorded about one event, gathered once."""

    event: RiskEvent
    diagnosis: Diagnosis | None
    decision: Decision | None
    ml: MLDiagnosisPrediction | None
    state: StoppingRuleState | None


def _load_contexts(
    session: Session, event_ids: Iterable[str] | None = None
) -> list[_Context]:
    """Gather event + diagnosis + latest decision + latest ML row + state."""
    stmt = select(RiskEvent)
    ids = list(event_ids) if event_ids is not None else None
    if ids is not None:
        if not ids:
            return []
        stmt = stmt.where(RiskEvent.id.in_(ids))

    contexts: list[_Context] = []
    for event in session.execute(stmt).scalars():
        decision = session.execute(
            select(Decision)
            .where(Decision.event_id == event.id)
            .order_by(Decision.decided_at.desc(), Decision.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        ml = session.execute(
            select(MLDiagnosisPrediction)
            .where(MLDiagnosisPrediction.event_id == event.id)
            .order_by(MLDiagnosisPrediction.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        contexts.append(
            _Context(
                event=event,
                diagnosis=session.get(Diagnosis, event.id),
                decision=decision,
                ml=ml,
                state=session.get(StoppingRuleState, event.id),
            )
        )
    return contexts


def _build_item(context: _Context, reason_code: str, stage: str) -> ExceptionItem:
    event = context.event
    decision = context.decision
    policy_result: dict[str, Any] = (
        decision.policy_result if decision and isinstance(decision.policy_result, dict) else {}
    )
    return ExceptionItem(
        event_id=event.id,
        event_type=event.type.value,
        amount=str(event.amount),
        currency=event.currency,
        status=event.status.value,
        reason=REASON_TEXT.get(reason_code, reason_code),
        reason_code=reason_code,
        stage=stage,
        rule_triggered=policy_result.get("rule_triggered"),
        threshold_checked=policy_result.get("threshold_checked"),
        actual_value=policy_result.get("actual_value"),
        threshold_value=policy_result.get("threshold_value"),
        action_code=decision.action_code if decision else None,
        rule_root_cause=(
            context.diagnosis.root_cause_code.value if context.diagnosis else None
        ),
        rule_confidence=context.diagnosis.confidence if context.diagnosis else None,
        ml_root_cause=(
            context.ml.predicted_root_cause.value if context.ml else None
        ),
        ml_confidence=context.ml.confidence if context.ml else None,
        ml_agrees=context.ml.agrees_with_rule_engine if context.ml else None,
        correlation_id=event.correlation_id,
        detected_at=event.detected_at.isoformat(),
        occurred_at=(
            decision.decided_at.isoformat() if decision else event.detected_at.isoformat()
        ),
    )


def derive_exceptions(context: _Context) -> list[ExceptionItem]:
    """Every reason this event needs a human, derived from recorded state.

    Pure: reads what the engines wrote, decides nothing new.
    """
    items: list[ExceptionItem] = []

    # --- Section 4a: the classifier disagreed, or was not confident --------
    if context.ml is not None and not context.ml.agrees_with_rule_engine:
        items.append(_build_item(context, REASON_ML_DISAGREEMENT, "diagnosis"))

    # --- the rule engine itself was unsure ---------------------------------
    if (
        context.diagnosis is not None
        and context.diagnosis.confidence < LOW_CONFIDENCE_THRESHOLD
    ):
        items.append(_build_item(context, REASON_LOW_CONFIDENCE, "diagnosis"))

    # --- the policy gate refused -------------------------------------------
    decision = context.decision
    if decision is not None and isinstance(decision.policy_result, dict):
        if decision.policy_result.get("status") == PolicyResultStatus.BLOCKED.value:
            rule = decision.policy_result.get("rule_triggered")
            reason_code = RULE_TO_REASON.get(rule, REASON_POLICY_BLOCKED)
            items.append(_build_item(context, reason_code, "policy"))

    # --- handed to a human, outcome still open -----------------------------
    if context.event.status == EventStatus.ESCALATED:
        items.append(_build_item(context, REASON_ESCALATED, "escalation"))

    return items


def collect_exceptions(
    session: Session, *, event_ids: Iterable[str] | None = None
) -> list[ExceptionItem]:
    """All exception rows, newest first."""
    items: list[ExceptionItem] = []
    for context in _load_contexts(session, event_ids):
        items.extend(derive_exceptions(context))
    items.sort(key=lambda item: item.occurred_at, reverse=True)
    return items


def count_exceptions(session: Session, *, event_ids: Iterable[str] | None = None) -> int:
    """How many exception rows exist. Used by /batch's ``exceptions_raised``."""
    return len(collect_exceptions(session, event_ids=event_ids))


@router.get(
    "/exceptions",
    response_model=ExceptionsResponse,
    summary="Unresolved / low-confidence cases and why the engine did not act",
)
def get_exceptions(
    reason_code: str | None = Query(
        default=None, description="Filter to one reason code, e.g. ml_rule_disagreement."
    ),
    event_type: str | None = Query(default=None, description="Filter by event type."),
    status: str | None = Query(default=None, description="Filter by current event status."),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> ExceptionsResponse:
    """List cases needing review. Section 10.

    The breakdown is computed over the FILTERED set before pagination, so the
    counts describe what the caller asked about rather than the current page.
    """
    items = collect_exceptions(session)

    if reason_code:
        items = [item for item in items if item.reason_code == reason_code]
    if event_type:
        items = [item for item in items if item.event_type == event_type]
    if status:
        items = [item for item in items if item.status == status]

    breakdown: dict[str, int] = {}
    for item in items:
        breakdown[item.reason_code] = breakdown.get(item.reason_code, 0) + 1

    page = items[offset : offset + limit]
    return ExceptionsResponse(
        total=len(items),
        returned=len(page),
        reason_breakdown=breakdown,
        items=page,
    )
