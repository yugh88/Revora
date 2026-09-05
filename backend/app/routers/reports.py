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

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db, utcnow
from app.enums import AuditStage, EventType, OutcomeResolution
from app.engine.promise_tracker import display_status
from app.models import (
    AuditLog,
    CommunicationLog,
    Decision,
    Diagnosis,
    Merchant,
    Outcome,
    PromiseToPay,
    RiskEvent,
)
from app.services.report_pdf import render_audit_pdf, render_recovery_pdf

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


PRESET_LABELS = {
    1: "Last day",
    7: "Last week",
    30: "Last month",
    90: "Last 3 months",
    180: "Last 6 months",
    365: "Last 12 months",
}


def _as_utc(moment: datetime) -> datetime:
    """Read a naive bound as UTC rather than rejecting it.

    A report is generated from a date a person picked in a browser; refusing it
    over a missing timezone would surface an error they cannot act on.
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _window(
    days: int | None,
    now: datetime,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> tuple[datetime | None, datetime, str]:
    """Resolve a period into bounds and a label a merchant would recognise.

    An explicit range wins over a preset: if someone picked dates, those are the
    dates. ``days`` remains the preset path, so existing callers are unchanged.
    """
    if date_from or date_to:
        start = _as_utc(date_from) if date_from else None
        end = _as_utc(date_to) if date_to else now
        if start and end < start:
            start, end = end, start
        label = (
            f"{start.date().isoformat()} to {end.date().isoformat()}"
            if start
            else f"Up to {end.date().isoformat()}"
        )
        return start, end, label

    if not days:
        return None, now, "All time"
    return now - timedelta(days=days), now, PRESET_LABELS.get(days, f"Last {days} days")


@router.get("/reports/recovery", response_model=RecoveryReport, summary="Recovery report")
def recovery_report(
    days: int | None = Query(default=None, ge=1, le=3650),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
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
    since, until, label = _window(days, now, date_from=date_from, date_to=date_to)

    def scoper(event_type: EventType | None):
        """Build the filter `_money_summary` expects.

        It takes a callable that narrows a statement, not a statement — reusing
        its exact contract is what keeps the report and the dashboard reading
        the same rows rather than two similar-looking queries.
        """

        def apply(stmt):
            if since is not None:
                stmt = stmt.where(RiskEvent.detected_at >= since)
            if until is not None:
                stmt = stmt.where(RiskEvent.detected_at <= until)
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
        period_to=until.isoformat(),
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
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    stage: AuditStage | None = Query(default=None),
    session: Session = Depends(get_db),
) -> AuditReport:
    """What Revora did over a period, summarised from the audit trail.

    Counts real recorded entries. An empty period returns zeros rather than an
    error: "nothing happened last Tuesday" is a legitimate answer.
    """
    now = utcnow()
    since, until, label = _window(days, now, date_from=date_from, date_to=date_to)

    stmt = select(AuditLog)
    if since is not None:
        stmt = stmt.where(AuditLog.timestamp >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.timestamp <= until)
    if stage is not None:
        stmt = stmt.where(AuditLog.stage == stage)

    entries = list(session.execute(stmt).scalars())
    stages = Counter(entry.stage.value for entry in entries)
    actions = Counter(entry.action for entry in entries)

    return AuditReport(
        generated_at=now.isoformat(),
        period_label=label,
        period_from=since.isoformat() if since else None,
        period_to=until.isoformat(),
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


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #


def _business_name(session: Session) -> str:
    """Whose report this is. Falls back rather than failing."""
    merchant = session.execute(select(Merchant).limit(1)).scalar_one_or_none()
    return getattr(merchant, "name", None) or "Revora Demo Merchant"


def _case_details(session: Session, event_ids: list[str], limit: int) -> list[dict]:
    """Gather the judgement behind each case, for the detail section.

    Reads the same rows the drill-down reads. Capped, because a report has to
    stay openable — the caller tells the reader when it truncated.
    """
    details: list[dict] = []
    events = list(
        session.execute(
            select(RiskEvent)
            .where(RiskEvent.id.in_(event_ids))
            .order_by(RiskEvent.detected_at.desc())
            .limit(limit)
        ).scalars()
    )

    for event in events:
        raw = event.raw_signal if isinstance(event.raw_signal, dict) else {}
        diagnosis = session.get(Diagnosis, event.id)
        decisions = list(
            session.execute(
                select(Decision)
                .where(Decision.event_id == event.id)
                .order_by(Decision.decided_at.desc())
            ).scalars()
        )
        outcome = session.get(Outcome, event.id)
        contacts = list(
            session.execute(
                select(CommunicationLog)
                .where(CommunicationLog.event_id == event.id)
                .order_by(CommunicationLog.created_at.desc())
            ).scalars()
        )
        promise = session.execute(
            select(PromiseToPay)
            .where(PromiseToPay.event_id == event.id)
            .order_by(PromiseToPay.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        audit = list(
            session.execute(
                select(AuditLog)
                .where(AuditLog.event_id == event.id)
                .order_by(AuditLog.timestamp)
            ).scalars()
        )

        details.append({
            "event": {
                "customer_name": raw.get("customer_name") or event.customer_id,
                "type": event.type.value,
                "amount": str(event.amount),
                "status": event.status.value,
                "detected_at": event.detected_at.isoformat(),
            },
            "diagnosis": (
                {"root_cause": diagnosis.root_cause_code.value} if diagnosis else None
            ),
            "decisions": [
                {"action_code": d.action_code, "policy_result": d.policy_result}
                for d in decisions
            ],
            "outcome": (
                {
                    "resolved": outcome.resolved.value,
                    "amount_recovered": str(outcome.amount_recovered),
                }
                if outcome
                else None
            ),
            "communications": [
                {
                    "channel": c.channel.value,
                    "status": c.status.value,
                    "body": c.body,
                    "blocked_reason": c.blocked_reason,
                    "reply_text": c.reply_text,
                }
                for c in contacts
            ],
            "promise": (
                {
                    "promised_amount": str(promise.promised_amount),
                    "promised_date": promise.promised_date.isoformat(),
                    "status": display_status(promise),
                }
                if promise
                else None
            ),
            "audit": [{"action": a.action, "stage": a.stage.value} for a in audit],
        })
    return details


def _pdf_response(payload: bytes, name: str) -> Response:
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get(
    "/reports/recovery.pdf",
    summary="Recovery report as a PDF",
    response_class=Response,
)
def recovery_report_pdf(
    days: int | None = Query(default=None, ge=1, le=3650),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    category: EventType | None = Query(default=None),
    detail_limit: int = Query(default=60, ge=0, le=200),
    session: Session = Depends(get_db),
) -> Response:
    """The recovery report, laid out for printing or filing.

    Built from the SAME `recovery_report` the dashboard reads, so the document
    and the screen cannot disagree. This endpoint only chooses a layout.
    """
    report = recovery_report(
        days=days, date_from=date_from, date_to=date_to,
        category=category, session=session,
    )

    now = utcnow()
    since, until, _ = _window(days, now, date_from=date_from, date_to=date_to)
    stmt = select(RiskEvent.id)
    if since is not None:
        stmt = stmt.where(RiskEvent.detected_at >= since)
    if until is not None:
        stmt = stmt.where(RiskEvent.detected_at <= until)
    if category is not None:
        stmt = stmt.where(RiskEvent.type == category)
    ids = list(session.execute(stmt).scalars())

    payload = render_recovery_pdf(
        report,
        _case_details(session, ids, detail_limit) if detail_limit else [],
        business=_business_name(session),
        generated=now,
    )
    stamp = now.strftime("%Y-%m-%d")
    return _pdf_response(payload, f"revora-recovery-{stamp}.pdf")


@router.get(
    "/reports/audit.pdf", summary="Audit report as a PDF", response_class=Response
)
def audit_report_pdf(
    days: int | None = Query(default=None, ge=1, le=3650),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    stage: AuditStage | None = Query(default=None),
    detail_limit: int = Query(default=300, ge=0, le=2000),
    session: Session = Depends(get_db),
) -> Response:
    """The audit trail for a period, laid out for filing."""
    report = audit_report(
        days=days, date_from=date_from, date_to=date_to, stage=stage, session=session
    )

    now = utcnow()
    since, until, _ = _window(days, now, date_from=date_from, date_to=date_to)
    stmt = select(AuditLog).order_by(AuditLog.timestamp.desc())
    if since is not None:
        stmt = stmt.where(AuditLog.timestamp >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.timestamp <= until)
    if stage is not None:
        stmt = stmt.where(AuditLog.stage == stage)

    rows: list[dict] = []
    if detail_limit:
        names: dict[str, str] = {}
        for entry in session.execute(stmt.limit(detail_limit)).scalars():
            name = "—"
            if entry.event_id:
                if entry.event_id not in names:
                    event = session.get(RiskEvent, entry.event_id)
                    raw = event.raw_signal if event and isinstance(event.raw_signal, dict) else {}
                    names[entry.event_id] = str(
                        raw.get("customer_name") or (event.customer_id if event else "—")
                    )
                name = names[entry.event_id]
            rows.append({
                "timestamp": entry.timestamp.isoformat(),
                "customer_name": name,
                "stage": entry.stage.value,
                "action": entry.action,
            })

    payload = render_audit_pdf(
        report, rows, business=_business_name(session), generated=now
    )
    stamp = now.strftime("%Y-%m-%d")
    return _pdf_response(payload, f"revora-audit-{stamp}.pdf")
