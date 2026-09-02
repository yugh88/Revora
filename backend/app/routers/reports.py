"""Backend-generated reports. BUILD_SPEC Section 10.

Computed from the same rows the dashboard reads — the recovery ledger and the
audit trail — so a report and the screen it was produced from cannot disagree.
Nothing here is stored, and nothing is calculated a second way: the money
summary is the same function `/events` uses.

Read-only. Producing a report must never be able to change what it reports on.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db, utcnow
from app.enums import AuditStage, EventType, OutcomeResolution
from app.models import AuditLog, Outcome, PromiseToPay, RiskEvent

logger = logging.getLogger("revora.reports")

router = APIRouter(tags=["reports"])


class RecoveryReportRow(BaseModel):
    label: str
    cases: int
    amount_at_risk: str
    amount_recovered: str
    recovery_rate: float


class RecoveryReport(BaseModel):
    generated_at: str
    period_label: str
    period_from: str | None
    period_to: str
    total_cases: int
    amount_at_risk: str
    amount_recovered: str
    amount_pending: str
    amount_lost: str
    recovery_rate: float
    arr_retained: str
    promises_made: int
    promises_kept: int
    promises_broken: int
    by_category: list[RecoveryReportRow]


class AuditReportRow(BaseModel):
    label: str
    entries: int


class AuditReport(BaseModel):
    generated_at: str
    period_label: str
    period_from: str | None
    period_to: str
    total_entries: int
    by_stage: list[AuditReportRow]
    by_action: list[AuditReportRow]
    cases_touched: int


def _window(days: int | None, now: datetime) -> tuple[datetime | None, str]:
    """Resolve a period into a bound and a label a merchant would recognise."""
    if not days:
        return None, "All time"
    labels = {
        1: "Last day",
        7: "Last week",
        30: "Last month",
        90: "Last 3 months",
        180: "Last 6 months",
        365: "Last 12 months",
    }
    return now - timedelta(days=days), labels.get(days, f"Last {days} days")


@router.get("/reports/recovery", response_model=RecoveryReport, summary="Recovery report")
def recovery_report(
    days: int | None = Query(default=None, ge=1, le=3650),
    category: EventType | None = Query(default=None),
    session: Session = Depends(get_db),
) -> RecoveryReport:
    """Recovery performance over a period, broken down by category.

    Every figure is read from the ledger. The headline totals come from the very
    same `_money_summary` the dashboard uses, so the report cannot drift away
    from the screen a merchant compared it against.
    """
    from app.routers.events import _money_summary

    now = utcnow()
    since, label = _window(days, now)

    def scoper(event_type: EventType | None):
        """Build the filter `_money_summary` expects.

        It takes a callable that narrows a statement, not a statement — reusing
        its exact contract is what keeps the report and the dashboard reading
        the same rows rather than two similar-looking queries.
        """

        def apply(stmt):
            if since is not None:
                stmt = stmt.where(RiskEvent.detected_at >= since)
            if event_type is not None:
                stmt = stmt.where(RiskEvent.type == event_type)
            return stmt

        return apply

    def ids_for(event_type: EventType | None) -> list[str]:
        return list(session.execute(scoper(event_type)(select(RiskEvent.id))).scalars())

    overall_ids = ids_for(category)
    statuses: dict[str, int] = {}
    money = _money_summary(session, scoper(category), statuses)

    rows: list[RecoveryReportRow] = []
    for event_type in EventType:
        if category is not None and event_type != category:
            continue
        ids = ids_for(event_type)
        if not ids:
            continue
        breakdown: dict[str, int] = {}
        part = _money_summary(session, scoper(event_type), breakdown)
        rows.append(
            RecoveryReportRow(
                label=event_type.value,
                cases=len(ids),
                amount_at_risk=part.amount_at_risk,
                amount_recovered=part.amount_recovered,
                recovery_rate=part.recovery_rate,
            )
        )
    rows.sort(key=lambda row: Decimal(row.amount_at_risk), reverse=True)

    promises = list(
        session.execute(
            select(PromiseToPay).where(PromiseToPay.event_id.in_(overall_ids))
        ).scalars()
    )
    kept = sum(1 for p in promises if p.status.value == "kept")
    broken = sum(1 for p in promises if p.status.value == "broken")

    return RecoveryReport(
        generated_at=now.isoformat(),
        period_label=label,
        period_from=since.isoformat() if since else None,
        period_to=now.isoformat(),
        total_cases=len(overall_ids),
        amount_at_risk=money.amount_at_risk,
        amount_recovered=money.amount_recovered,
        amount_pending=money.amount_pending,
        amount_lost=money.amount_lost,
        recovery_rate=money.recovery_rate,
        arr_retained=money.arr_retained,
        promises_made=len(promises),
        promises_kept=kept,
        promises_broken=broken,
        by_category=rows,
    )


@router.get("/reports/audit", response_model=AuditReport, summary="Audit report")
def audit_report(
    days: int | None = Query(default=None, ge=1, le=3650),
    stage: AuditStage | None = Query(default=None),
    session: Session = Depends(get_db),
) -> AuditReport:
    """What Revora did over a period, summarised from the audit trail.

    Counts real recorded entries. An empty period returns zeros rather than an
    error: "nothing happened last Tuesday" is a legitimate answer.
    """
    now = utcnow()
    since, label = _window(days, now)

    stmt = select(AuditLog)
    if since is not None:
        stmt = stmt.where(AuditLog.timestamp >= since)
    if stage is not None:
        stmt = stmt.where(AuditLog.stage == stage)

    entries = list(session.execute(stmt).scalars())
    stages = Counter(entry.stage.value for entry in entries)
    actions = Counter(entry.action for entry in entries)

    return AuditReport(
        generated_at=now.isoformat(),
        period_label=label,
        period_from=since.isoformat() if since else None,
        period_to=now.isoformat(),
        total_entries=len(entries),
        by_stage=[
            AuditReportRow(label=name, entries=count)
            for name, count in stages.most_common()
        ],
        by_action=[
            AuditReportRow(label=name, entries=count)
            for name, count in actions.most_common(15)
        ],
        cases_touched=len({entry.event_id for entry in entries if entry.event_id}),
    )
