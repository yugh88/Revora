"""GET /events and GET /events/{id}. BUILD_SPEC Sections 10 and 13.

    "GET /events, GET /events/{id} — feed + drill-down (diagnosis, decision,
     stopping-rule state, audit timeline)"

READ-ONLY. This router runs no engine, writes no row, and takes no lock. It
queries what the pipeline already recorded and shapes it for the feed and the
drill-down. Nothing here can change what the engine decided, which is why adding
it does not touch the frozen decision architecture.

``POST /events`` (ingestion) is also named in Section 10 but is NOT implemented
here. Nothing in the Session 7 UI needs it, and a mutating ingest endpoint is a
real design decision — idempotency, validation, get-or-create for the customer
profile — that deserves its own session rather than being bolted on because a
router file happened to be open.

Filtering is SERVER-SIDE and every parameter maps to a real indexed column. The
frontend does not re-filter what the API already narrowed; there is one source
of truth for what "status=stopped" means.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.engine.diagnosis_engine import LOW_CONFIDENCE_THRESHOLD
from app.enums import (
    AuditStage,
    EventStatus,
    EventType,
    GatewayUsed,
    OutcomeResolution,
    PolicyResultStatus,
)
from app.models import (
    AuditLog,
    Outcome as OutcomeModel,
    Decision,
    Diagnosis,
    MLDiagnosisPrediction,
    Outcome,
    PaymentAttempt,
    RiskEvent,
    StoppingRuleState,
)
from app.schemas.risk_event import (
    AttemptDetail,
    EventMoneySummary,
    AuditEntry,
    DecisionDetail,
    DiagnosisDetail,
    EventDetailResponse,
    EventListResponse,
    EventSummary,
    MlDetail,
    OutcomeDetail,
    StoppingRuleDetail,
)

logger = logging.getLogger("revora.events")

router = APIRouter(tags=["events"])

#: Section 2's pipeline, in order. Mirrors routers/audit.py rather than
#: redefining it, so the two endpoints can never disagree about what a complete
#: trail looks like.
PIPELINE_STAGES: tuple[AuditStage, ...] = (
    AuditStage.DETECTION,
    AuditStage.DIAGNOSIS,
    AuditStage.DECISION,
    AuditStage.POLICY,
    AuditStage.EXECUTION,
    AuditStage.VERIFICATION,
    AuditStage.RECOVERY,
    AuditStage.ESCALATION,
)


def _as_utc(moment: datetime) -> datetime:
    """Attach UTC to a naive bound so a query filter cannot raise.

    Callers legitimately send either form: a browser's toISOString() always
    carries a zone, a hand-written curl often does not.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _latest_decision(session: Session, event_id: str) -> Decision | None:
    return session.execute(
        select(Decision)
        .where(Decision.event_id == event_id)
        .order_by(Decision.decided_at.desc(), Decision.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_ml(session: Session, event_id: str) -> MLDiagnosisPrediction | None:
    return session.execute(
        select(MLDiagnosisPrediction)
        .where(MLDiagnosisPrediction.event_id == event_id)
        .order_by(MLDiagnosisPrediction.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def build_summary(
    event: RiskEvent,
    diagnosis: Diagnosis | None,
    decision: Decision | None,
    ml: MLDiagnosisPrediction | None,
    outcome: Outcome | None,
) -> EventSummary:
    """Shape one feed row from already-persisted state.

    ``needs_review`` is derived here from the same two conditions
    routers/exceptions.py uses — a low-confidence rule diagnosis, or an ML
    disagreement — so the feed's "needs attention" count and the exceptions
    queue cannot drift apart.
    """
    review_reasons: list[str] = []
    if diagnosis is not None and diagnosis.confidence < LOW_CONFIDENCE_THRESHOLD:
        review_reasons.append('Root cause could not be determined confidently')
    if ml is not None and not ml.agrees_with_rule_engine:
        review_reasons.append('ML/rule disagreement — needs review')

    policy_status: str | None = None
    if decision is not None and isinstance(decision.policy_result, dict):
        policy_status = decision.policy_result.get('status')

    raw = event.raw_signal if isinstance(event.raw_signal, dict) else {}
    customer_name = raw.get('customer_name')

    return EventSummary(
        id=event.id,
        type=event.type,
        merchant_id=event.merchant_id,
        customer_id=event.customer_id,
        customer_name=(
            str(customer_name) if isinstance(customer_name, str) and customer_name
            else event.customer_id
        ),
        amount=str(event.amount),
        currency=event.currency,
        source_ref=event.source_ref,
        detected_at=event.detected_at.isoformat(),
        status=event.status,
        gateway_used=event.gateway_used,
        correlation_id=event.correlation_id,
        root_cause=diagnosis.root_cause_code.value if diagnosis else None,
        confidence=diagnosis.confidence if diagnosis else None,
        action_code=decision.action_code if decision else None,
        recovery_probability=decision.recovery_probability if decision else None,
        policy_status=policy_status,
        ml_agrees=ml.agrees_with_rule_engine if ml else None,
        needs_review=bool(review_reasons),
        review_reasons=review_reasons,
        resolved=outcome.resolved.value if outcome else None,
        amount_recovered=str(outcome.amount_recovered) if outcome else None,
    )


def _sum_money(session: Session, column, *conditions) -> Decimal:
    """Sum a Money column exactly.

    Read back through the ORM rather than with func.sum: Money is a
    TypeDecorator storing integer paise and exposing Decimal rupees, and
    SQLAlchemy applies process_result_value to an aggregate too — so func.sum
    returns RUPEES. Session 4 shipped a bug where that value was divided by 100
    a second time, understating every amount by 100x while leaving the ratio
    correct. Adding them here removes the ambiguity entirely.
    """
    stmt = select(column)
    for condition in conditions:
        stmt = stmt.where(condition)
    total = Decimal('0.00')
    for value in session.execute(stmt).scalars():
        if value is not None:
            total += value
    return total.quantize(Decimal('0.01'))


#: Event types that recur, and therefore have annualisable revenue.
_RECURRING_TYPES = (EventType.SUBSCRIPTION_FAILED, EventType.MANDATE_FAILED)


def _arr_retained(session: Session, scoped_ids) -> Decimal:
    """Annualised recurring revenue retained through verified recovery.

    Measured, not assumed. Each recovered recurring charge is annualised at the
    cadence actually recorded on its event — monthly x12, quarterly x4, annual
    x1. A case with no recorded cadence contributes NOTHING rather than being
    treated as monthly, because guessing the cadence would fabricate the number
    this figure exists to report.

    One-off recoveries are excluded entirely. Recovering an overdue invoice is
    real money, but it is not recurring revenue and counting it here would
    overstate what the business can rely on next year.
    """
    total = Decimal("0.00")
    rows = session.execute(
        select(RiskEvent.raw_signal, OutcomeModel.amount_recovered)
        .join(OutcomeModel, OutcomeModel.event_id == RiskEvent.id)
        .where(
            RiskEvent.id.in_(scoped_ids),
            RiskEvent.type.in_(_RECURRING_TYPES),
            OutcomeModel.resolved.in_(
                (OutcomeResolution.RECOVERED, OutcomeResolution.PARTIALLY_RECOVERED)
            ),
        )
    )
    for raw_signal, recovered in rows:
        if not recovered:
            continue
        period = (raw_signal or {}).get("billing_period_months")
        if not isinstance(period, int) or period <= 0:
            continue  # cadence unknown: contribute nothing rather than guess
        total += recovered * (Decimal(12) / Decimal(period))
    return total.quantize(Decimal("0.01"))


def _money_summary(session: Session, scoped, status_breakdown: dict[str, int]) -> EventMoneySummary:
    """Authoritative totals over the filtered set, from the ledger."""
    scoped_ids = scoped(select(RiskEvent.id)).scalar_subquery()

    at_risk = _sum_money(session, RiskEvent.amount, RiskEvent.id.in_(scoped_ids))
    recovered = _sum_money(
        session, OutcomeModel.amount_recovered, OutcomeModel.event_id.in_(scoped_ids)
    )

    def amount_where(resolution: str) -> Decimal:
        ids = (
            select(OutcomeModel.event_id)
            .where(OutcomeModel.event_id.in_(scoped_ids), OutcomeModel.resolved == resolution)
            .scalar_subquery()
        )
        return _sum_money(session, RiskEvent.amount, RiskEvent.id.in_(ids))

    def partly_outstanding() -> Decimal:
        """The unpaid remainder of cases that were only partly settled.

        A customer who owed 100 and paid 30 leaves 70 outstanding. Without this
        the 70 belongs to no bucket at all: it is not recovered, it is not
        written off, and its case is not marked pending — so the three amounts
        stop summing to the amount at risk and money quietly leaves the books.
        """
        total = Decimal("0.00")
        rows = session.execute(
            select(RiskEvent.amount, OutcomeModel.amount_recovered)
            .join(OutcomeModel, OutcomeModel.event_id == RiskEvent.id)
            .where(
                RiskEvent.id.in_(scoped_ids),
                OutcomeModel.resolved == OutcomeResolution.PARTIALLY_RECOVERED,
            )
        )
        for at_risk, recovered_part in rows:
            if at_risk is not None:
                total += at_risk - (recovered_part or Decimal("0.00"))
        return total.quantize(Decimal("0.01"))

    return EventMoneySummary(
        arr_retained=str(_arr_retained(session, scoped_ids)),
        amount_at_risk=str(at_risk),
        amount_recovered=str(recovered),
        amount_lost=str(amount_where(OutcomeResolution.LOST)),
        amount_pending=str(amount_where(OutcomeResolution.PENDING) + partly_outstanding()),
        recovery_rate=round(float(recovered / at_risk), 4) if at_risk > 0 else 0.0,
        active_interventions=(
            status_breakdown.get(EventStatus.INTERVENING.value, 0)
            + status_breakdown.get(EventStatus.ESCALATED.value, 0)
        ),
    )


@router.get('/events', response_model=EventListResponse, summary='Risk event feed')
def list_events(
    status: EventStatus | None = Query(default=None, description='Filter by lifecycle status.'),
    type: EventType | None = Query(default=None, description='Filter by one of the five core event types.'),
    gateway: GatewayUsed | None = Query(default=None, description='Filter by executing gateway.'),
    needs_review: bool | None = Query(
        default=None,
        description='Only events flagged for human review (low confidence or ML disagreement).',
    ),
    q: str | None = Query(
        default=None,
        description='Substring match on event id, source reference, customer id or correlation id.',
    ),
    detected_from: datetime | None = Query(
        default=None,
        description='Only events detected at or after this instant. Drives the reporting-period selector.',
    ),
    detected_to: datetime | None = Query(
        default=None, description='Only events detected at or before this instant.'
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    order: str = Query(default='desc', pattern='^(asc|desc)$'),
    session: Session = Depends(get_db),
) -> EventListResponse:
    """The feed. Section 13, page 2.

    Ordered by detection time, newest first by default — an operator opening
    this page wants what just came in, not what came in on day one.
    """
    conditions: list[Any] = []
    if status is not None:
        conditions.append(RiskEvent.status == status)
    if type is not None:
        conditions.append(RiskEvent.type == type)
    if gateway is not None:
        conditions.append(RiskEvent.gateway_used == gateway)
    # A naive bound is interpreted as UTC rather than rejected. The TZDateTime
    # guard that refuses naive values on WRITE is a real safety property and
    # stays exactly as it is — but a read filter is not a write, and letting an
    # ordinary query parameter raise a 500 would surface a backend exception to
    # a user who can do nothing about it.
    if detected_from is not None:
        conditions.append(RiskEvent.detected_at >= _as_utc(detected_from))
    if detected_to is not None:
        conditions.append(RiskEvent.detected_at <= _as_utc(detected_to))
    if q:
        needle = f'%{q.strip()}%'
        conditions.append(
            or_(
                RiskEvent.id.ilike(needle),
                RiskEvent.source_ref.ilike(needle),
                RiskEvent.customer_id.ilike(needle),
                RiskEvent.correlation_id.ilike(needle),
            )
        )

    # `needs_review` is a derived condition, not a column, so it is expressed as
    # a subquery over the two things that actually produce it.
    if needs_review is not None:
        low_confidence = select(Diagnosis.event_id).where(
            Diagnosis.confidence < LOW_CONFIDENCE_THRESHOLD
        )
        disagreed = select(MLDiagnosisPrediction.event_id).where(
            MLDiagnosisPrediction.agrees_with_rule_engine.is_(False)
        )
        flagged = or_(
            RiskEvent.id.in_(low_confidence.scalar_subquery()),
            RiskEvent.id.in_(disagreed.scalar_subquery()),
        )
        conditions.append(flagged if needs_review else ~flagged)

    def scoped(stmt):
        for condition in conditions:
            stmt = stmt.where(condition)
        return stmt

    total = int(
        session.execute(scoped(select(func.count(RiskEvent.id)))).scalar_one() or 0
    )

    status_breakdown = {
        key.value if hasattr(key, 'value') else str(key): int(count)
        for key, count in session.execute(
            scoped(select(RiskEvent.status, func.count()).group_by(RiskEvent.status))
        )
    }
    type_breakdown = {
        key.value if hasattr(key, 'value') else str(key): int(count)
        for key, count in session.execute(
            scoped(select(RiskEvent.type, func.count()).group_by(RiskEvent.type))
        )
    }

    ordering = (
        RiskEvent.detected_at.asc() if order == 'asc' else RiskEvent.detected_at.desc()
    )
    rows = list(
        session.execute(
            scoped(select(RiskEvent)).order_by(ordering, RiskEvent.id).offset(offset).limit(limit)
        ).scalars()
    )

    items = [
        build_summary(
            event,
            session.get(Diagnosis, event.id),
            _latest_decision(session, event.id),
            _latest_ml(session, event.id),
            session.get(Outcome, event.id),
        )
        for event in rows
    ]

    # Counted over the whole filtered set, not the page, so the badge in the UI
    # reports the real backlog rather than what happens to be visible.
    low_conf_ids = select(Diagnosis.event_id).where(
        Diagnosis.confidence < LOW_CONFIDENCE_THRESHOLD
    )
    disagreed_ids = select(MLDiagnosisPrediction.event_id).where(
        MLDiagnosisPrediction.agrees_with_rule_engine.is_(False)
    )
    needs_review_count = int(
        session.execute(
            scoped(
                select(func.count(RiskEvent.id)).where(
                    or_(
                        RiskEvent.id.in_(low_conf_ids.scalar_subquery()),
                        RiskEvent.id.in_(disagreed_ids.scalar_subquery()),
                    )
                )
            )
        ).scalar_one()
        or 0
    )

    money = _money_summary(session, scoped, status_breakdown)

    # Deliberately UNFILTERED: this reports how much history exists at all, so
    # the UI can distinguish "no recoveries in the last 12 months" from "we have
    # only ever recorded 6 weeks".
    earliest = session.execute(
        select(RiskEvent.detected_at).order_by(RiskEvent.detected_at.asc()).limit(1)
    ).scalar_one_or_none()

    return EventListResponse(
        total=total,
        returned=len(items),
        limit=limit,
        offset=offset,
        status_breakdown=status_breakdown,
        type_breakdown=type_breakdown,
        needs_review_count=needs_review_count,
        money=money,
        earliest_detected_at=earliest.isoformat() if earliest is not None else None,
        items=items,
    )


@router.get(
    '/events/{event_id}',
    response_model=EventDetailResponse,
    summary='One event, end to end',
)
def get_event(event_id: str, session: Session = Depends(get_db)) -> EventDetailResponse:
    """Everything recorded about one event. Section 13, page 2 drill-down."""
    event = session.get(RiskEvent, event_id)
    if event is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f'No such event: {event_id}',
        )

    diagnosis = session.get(Diagnosis, event_id)
    ml = _latest_ml(session, event_id)
    outcome = session.get(Outcome, event_id)
    state = session.get(StoppingRuleState, event_id)

    decisions = list(
        session.execute(
            select(Decision)
            .where(Decision.event_id == event_id)
            .order_by(Decision.decided_at.asc(), Decision.id.asc())
        ).scalars()
    )
    attempts = list(
        session.execute(
            select(PaymentAttempt)
            .where(PaymentAttempt.event_id == event_id)
            .order_by(PaymentAttempt.attempt_number.asc())
        ).scalars()
    )
    audit_rows = list(
        session.execute(
            select(AuditLog).where(AuditLog.event_id == event_id).order_by(AuditLog.id.asc())
        ).scalars()
    )

    present = {row.stage for row in audit_rows}

    return EventDetailResponse(
        event=build_summary(
            event, diagnosis, decisions[-1] if decisions else None, ml, outcome
        ),
        diagnosis=(
            DiagnosisDetail(
                root_cause=diagnosis.root_cause_code.value,
                confidence=diagnosis.confidence,
                evidence=list(diagnosis.evidence or []),
                diagnosed_at=diagnosis.diagnosed_at.isoformat(),
                is_low_confidence=diagnosis.confidence < LOW_CONFIDENCE_THRESHOLD,
            )
            if diagnosis
            else None
        ),
        ml=(
            MlDetail(
                predicted_root_cause=ml.predicted_root_cause.value,
                confidence=ml.confidence,
                agrees_with_rule_engine=ml.agrees_with_rule_engine,
                model_version=ml.model_version,
                predicted_at=ml.predicted_at.isoformat(),
            )
            if ml
            else None
        ),
        decisions=[
            DecisionDetail(
                id=decision.id,
                action_code=decision.action_code,
                recovery_probability=decision.recovery_probability,
                probability_source=decision.probability_source.value,
                policy_result=decision.policy_result or {},
                policy_version=decision.policy_version,
                decision_factors=decision.decision_factors or {},
                reasoning_text=decision.reasoning_text,
                decided_at=decision.decided_at.isoformat(),
            )
            for decision in decisions
        ],
        stopping_rule_state=(
            StoppingRuleDetail(
                attempts_used=state.attempts_used,
                max_attempts_for_type=state.max_attempts_for_type,
                cooldown_until=state.cooldown_until.isoformat() if state.cooldown_until else None,
                do_not_contact_snapshot=state.do_not_contact_snapshot,
                escalation_level=state.escalation_level,
                hard_stop_reason=state.hard_stop_reason,
            )
            if state
            else None
        ),
        attempts=[
            AttemptDetail(
                id=attempt.id,
                attempt_number=attempt.attempt_number,
                status=attempt.status.value,
                failure_reason=attempt.failure_reason,
                provider_ref=attempt.provider_ref,
                gateway_used=attempt.gateway_used.value,
                initiated_at=attempt.initiated_at.isoformat(),
                resolved_at=attempt.resolved_at.isoformat() if attempt.resolved_at else None,
            )
            for attempt in attempts
        ],
        outcome=(
            OutcomeDetail(
                resolved=outcome.resolved.value,
                amount_recovered=str(outcome.amount_recovered),
                resolved_at=outcome.resolved_at.isoformat() if outcome.resolved_at else None,
                resolution_channel=(
                    outcome.resolution_channel.value if outcome.resolution_channel else None
                ),
            )
            if outcome
            else None
        ),
        audit=[
            AuditEntry(
                id=row.id,
                timestamp=row.timestamp.isoformat(),
                stage=row.stage.value,
                action=row.action,
                actor=row.actor.value,
                before_state=row.before_state,
                after_state=row.after_state,
                reasoning=row.reasoning,
            )
            for row in audit_rows
        ],
        stages_present=[stage.value for stage in PIPELINE_STAGES if stage in present],
        stages_missing=[stage.value for stage in PIPELINE_STAGES if stage not in present],
    )
