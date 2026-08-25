"""GET /audit — searchable immutable log. BUILD_SPEC Sections 4 and 10.

Read-only over the existing ``AuditLog`` table. There is no second audit
mechanism and no write path here: Section 4 calls the log immutable and
append-only, and Session 1 enforces that with a ``before_flush`` listener that
rejects any UPDATE or DELETE of an audit row. This module only queries.

Two endpoints, because they answer different questions:

``GET /audit``
    Filter across everything — by event, correlation id, stage, actor, action or
    time range. This is the searchable log Section 10 asks for.

``GET /audit/trail/{event_id}``
    One event's pipeline in order, plus which of the Section 2 stages are
    present and which are missing. That second part matters: a judge tracing
    detection -> diagnosis -> decision -> policy -> execution -> verification ->
    recovery/escalation wants to see the gaps as clearly as the entries, and an
    event that stopped at the policy gate SHOULD be missing the execution
    stages. Showing what is absent is how the trail proves the engine declined
    to act rather than quietly failing to.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.enums import AuditStage
from app.models import AuditLog, RiskEvent
from app.schemas.batch import AuditItem, AuditResponse, AuditTrailResponse

logger = logging.getLogger("revora.audit")

router = APIRouter(tags=["audit"])

#: The Section 2 pipeline, in order. Used to report which stages a given event
#: reached. recovery and escalation are alternatives, so both are listed and
#: only one is normally present.
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


def to_item(row: AuditLog) -> AuditItem:
    return AuditItem(
        id=row.id,
        timestamp=row.timestamp.isoformat(),
        event_id=row.event_id,
        correlation_id=row.correlation_id,
        actor=row.actor.value,
        stage=row.stage.value,
        action=row.action,
        before_state=row.before_state,
        after_state=row.after_state,
        reasoning=row.reasoning,
    )


@router.get("/audit", response_model=AuditResponse, summary="Searchable immutable audit log")
def get_audit(
    event_id: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    stage: AuditStage | None = Query(default=None),
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    since: datetime | None = Query(
        default=None, description="ISO timestamp; entries at or after this instant."
    ),
    until: datetime | None = Query(
        default=None, description="ISO timestamp; entries at or before this instant."
    ),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> AuditResponse:
    """Search the audit log. Section 10.

    Ordering defaults to ascending by ``id``, which is insertion order. Sorting
    by timestamp alone would be ambiguous — several entries for one event are
    written inside the same transaction and can share a timestamp — and the
    order of the pipeline is exactly what a reader is here to see.
    """
    conditions = []
    if event_id:
        conditions.append(AuditLog.event_id == event_id)
    if correlation_id:
        conditions.append(AuditLog.correlation_id == correlation_id)
    if stage:
        conditions.append(AuditLog.stage == stage)
    if actor:
        conditions.append(AuditLog.actor == actor)
    if action:
        conditions.append(AuditLog.action == action)
    if since:
        conditions.append(AuditLog.timestamp >= since)
    if until:
        conditions.append(AuditLog.timestamp <= until)

    total_stmt = select(func.count(AuditLog.id))
    for condition in conditions:
        total_stmt = total_stmt.where(condition)
    total = int(session.execute(total_stmt).scalar_one() or 0)

    breakdown_stmt = select(AuditLog.stage, func.count()).group_by(AuditLog.stage)
    for condition in conditions:
        breakdown_stmt = breakdown_stmt.where(condition)
    stage_breakdown = {
        row_stage.value: int(count) for row_stage, count in session.execute(breakdown_stmt)
    }

    stmt = select(AuditLog)
    for condition in conditions:
        stmt = stmt.where(condition)
    stmt = stmt.order_by(
        AuditLog.id.desc() if order == "desc" else AuditLog.id.asc()
    ).offset(offset).limit(limit)

    rows = list(session.execute(stmt).scalars())
    return AuditResponse(
        total=total,
        returned=len(rows),
        offset=offset,
        limit=limit,
        stage_breakdown=stage_breakdown,
        items=[to_item(row) for row in rows],
    )


@router.get(
    "/audit/trail/{event_id}",
    response_model=AuditTrailResponse,
    summary="One event's full pipeline trail, in order",
)
def get_audit_trail(event_id: str, session: Session = Depends(get_db)) -> AuditTrailResponse:
    """Trace one event end to end.

    Reports both the stages present and the stages missing. An event stopped by
    the policy gate legitimately has no execution or verification entries, and
    saying so explicitly is more useful than leaving the reader to notice.
    """
    event = session.get(RiskEvent, event_id)
    if event is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No such event: {event_id}",
        )

    rows = list(
        session.execute(
            select(AuditLog).where(AuditLog.event_id == event_id).order_by(AuditLog.id.asc())
        ).scalars()
    )

    present = {row.stage for row in rows}
    stages_present = [stage.value for stage in PIPELINE_STAGES if stage in present]
    stages_missing = [stage.value for stage in PIPELINE_STAGES if stage not in present]

    return AuditTrailResponse(
        event_id=event_id,
        correlation_id=event.correlation_id,
        stages_present=stages_present,
        stages_missing=stages_missing,
        entry_count=len(rows),
        items=[to_item(row) for row in rows],
    )
