"""Render reports as PDF.

PRESENTATION ONLY
-----------------
Nothing in this module computes a figure. It is handed the very objects the
JSON report endpoints return — built by the same functions the dashboard reads —
and lays them out on a page. That is deliberate: a PDF generator that did its
own arithmetic would be a second source of truth, and the first time it
disagreed with the screen nobody would know which to believe.

reportlab is used because it is a pure-Python library with no system
dependencies, no headless browser and no paid service. A PDF has to be
generated on a server that may have neither a display nor network access.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger("revora.reports.pdf")

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6b7280")
LINE = colors.HexColor("#e5e7eb")
ACCENT = colors.HexColor("#4f46e5")
GOOD = colors.HexColor("#16a34a")
WARN = colors.HexColor("#d97706")
BAD = colors.HexColor("#dc2626")

#: A report has to stay openable. Beyond this many cases the detail section is
#: truncated and says so, rather than producing a thousand-page file nobody can
#: use and the browser struggles to render.
MAX_DETAIL_CASES = 60


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontSize=20, leading=24,
            textColor=INK, alignment=TA_LEFT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontSize=9.5, textColor=MUTED,
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontSize=12, leading=15,
            textColor=INK, spaceBefore=12, spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"], fontSize=9.5, leading=12,
            textColor=INK, spaceBefore=7, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontSize=8.6, leading=11.6,
            textColor=INK,
        ),
        "muted": ParagraphStyle(
            "muted", parent=base["Normal"], fontSize=8, leading=10.5,
            textColor=MUTED,
        ),
    }


def _money(value: Any) -> str:
    """Indian grouping, no decimals. Exactness lives in the ledger, not here."""
    try:
        amount = Decimal(str(value or "0"))
    except Exception:  # noqa: BLE001
        return str(value)
    whole = f"{amount:.0f}"
    negative = whole.startswith("-")
    digits = whole.lstrip("-")
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join(parts + [tail])
    return f"{'-' if negative else ''}₹{digits}"


def _human(value: Any) -> str:
    """Engine vocabulary into words. `cash_flow_delay` reads as prose."""
    if value is None or value == "":
        return "—"
    text = str(value)
    if "_" in text and text.lower() == text:
        text = text.replace("_", " ")
        return text[:1].upper() + text[1:]
    return text


def _when(value: Any) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value)).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return str(value)


def _header(story: list, styles: dict, title: str, period: str, generated: datetime,
            business: str) -> None:
    story.append(Paragraph("Revora", styles["title"]))
    story.append(
        Paragraph(
            f"{title} &nbsp;·&nbsp; {business}<br/>"
            f"Period: {period} &nbsp;·&nbsp; Generated {generated.strftime('%d %b %Y, %H:%M')}",
            styles["subtitle"],
        )
    )
    story.append(Table([[""]], colWidths=[170 * mm], rowHeights=[1],
                       style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1, ACCENT)])))
    story.append(Spacer(1, 8))


def _kv_table(rows: list[list[str]], widths: list[float]) -> Table:
    table = Table(rows, colWidths=widths, hAlign="LEFT")
    table.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("TEXTCOLOR", (0, 1), (-1, -1), INK),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ])
    )
    return table


def _footer(canvas, doc) -> None:
    """Page number and a standing reminder of what this document is."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(
        18 * mm, 12 * mm,
        "Revora · figures read from the recovery ledger · demo environment, "
        "no real payments or customer contacts",
    )
    canvas.drawRightString(192 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _document(buffer: BytesIO, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=20 * mm,
        title=title, author="Revora",
    )


def render_recovery_pdf(report: Any, cases: list[dict[str, Any]], *, business: str,
                        generated: datetime) -> bytes:
    """Recovery performance, plus the judgement behind each case.

    ``report`` is the RecoveryReport the JSON endpoint returns; ``cases`` are
    detail dictionaries. Both are supplied by the caller, so this function
    cannot disagree with the API.
    """
    styles = _styles()
    buffer = BytesIO()
    doc = _document(buffer, "Revora recovery report")
    story: list = []

    _header(story, styles, "Recovery report", report.period_label, generated, business)

    story.append(Paragraph("Summary", styles["h2"]))
    story.append(_kv_table(
        [
            ["Revenue at risk", "Recovered", "In progress", "Written off", "Recovery rate", "ARR retained"],
            [
                _money(report.amount_at_risk), _money(report.amount_recovered),
                _money(report.amount_pending), _money(report.amount_lost),
                f"{report.recovery_rate * 100:.1f}%", _money(report.arr_retained),
            ],
        ],
        [30 * mm, 28 * mm, 28 * mm, 28 * mm, 26 * mm, 30 * mm],
    ))

    story.append(Paragraph("Where revenue was at risk", styles["h2"]))
    rows = [["Category", "Cases", "At risk", "Recovered", "Rate"]]
    for row in report.by_category:
        rows.append([
            _human(row.label), str(row.cases), _money(row.amount_at_risk),
            _money(row.amount_recovered), f"{row.recovery_rate * 100:.1f}%",
        ])
    if len(rows) == 1:
        rows.append(["No cases in this period", "—", "—", "—", "—"])
    story.append(_kv_table(rows, [52 * mm, 20 * mm, 32 * mm, 32 * mm, 22 * mm]))

    story.append(Paragraph("Promises to pay", styles["h2"]))
    story.append(_kv_table(
        [["Promises made", "Kept", "Broken"],
         [str(report.promises_made), str(report.promises_kept), str(report.promises_broken)]],
        [40 * mm, 30 * mm, 30 * mm],
    ))

    if cases:
        story.append(PageBreak())
        story.append(Paragraph("Case detail", styles["h2"]))
        story.append(Paragraph(
            "Every judgement Revora made, in order: what it found, why, what it "
            "decided, whether your policy allowed it, and what happened.",
            styles["muted"],
        ))
        for case in cases[:MAX_DETAIL_CASES]:
            story.append(_case_block(case, styles))
        if len(cases) > MAX_DETAIL_CASES:
            story.append(Spacer(1, 6))
            story.append(Paragraph(
                f"Showing the first {MAX_DETAIL_CASES} of {len(cases)} cases. "
                "Narrow the date range to include the rest.",
                styles["muted"],
            ))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def _case_block(case: dict[str, Any], styles: dict) -> KeepTogether:
    """One case, kept on a single page so a reader never loses its thread."""
    event = case.get("event") or {}
    diagnosis = case.get("diagnosis") or {}
    decisions = case.get("decisions") or []
    decision = decisions[0] if decisions else {}
    policy = decision.get("policy_result") or {}
    outcome = case.get("outcome") or {}
    audit = case.get("audit") or []

    flow: list = [
        Paragraph(
            f"<b>{event.get('customer_name') or 'Customer'}</b> · "
            f"{_human(event.get('type'))} · {_money(event.get('amount'))}",
            styles["h3"],
        ),
        _kv_table(
            [
                ["Detected", "Status", "Cause", "Action", "Policy", "Recovered"],
                [
                    _when(event.get("detected_at")),
                    _human(event.get("status")),
                    _human(diagnosis.get("root_cause")),
                    _human(decision.get("action_code")),
                    "Blocked" if policy.get("status") == "blocked" else "Allowed",
                    _money(outcome.get("amount_recovered")),
                ],
            ],
            [30 * mm, 24 * mm, 32 * mm, 34 * mm, 20 * mm, 26 * mm],
        ),
    ]

    if policy.get("status") == "blocked" and policy.get("rule_triggered"):
        flow.append(Paragraph(
            f"Revora did not act: {_human(policy.get('rule_triggered')).lower()}.",
            styles["muted"],
        ))

    communications = case.get("communications") or []
    if communications:
        latest = communications[0]
        body = (latest.get("body") or latest.get("blocked_reason") or "").strip()
        flow.append(Paragraph(
            f"<b>{_human(latest.get('channel'))}</b> — {_human(latest.get('status'))}"
            + (f": {body[:260]}" if body else ""),
            styles["body"],
        ))
        if latest.get("reply_text"):
            flow.append(Paragraph(
                f"Customer replied: “{latest['reply_text']}”", styles["muted"]
            ))

    promise = case.get("promise")
    if promise:
        flow.append(Paragraph(
            f"Promised {_money(promise.get('promised_amount'))} by "
            f"{_when(promise.get('promised_date'))} — {_human(promise.get('status'))}.",
            styles["body"],
        ))

    if audit:
        steps = " → ".join(_human(entry.get("action")) for entry in audit[:8])
        flow.append(Paragraph(f"Audit: {steps}", styles["muted"]))

    flow.append(Spacer(1, 7))
    return KeepTogether(flow)


def render_audit_pdf(report: Any, entries: list[dict[str, Any]], *, business: str,
                     generated: datetime) -> bytes:
    """The audit trail for a period: what Revora did, and when."""
    styles = _styles()
    buffer = BytesIO()
    doc = _document(buffer, "Revora audit report")
    story: list = []

    _header(story, styles, "Audit report", report.period_label, generated, business)

    story.append(Paragraph("Summary", styles["h2"]))
    story.append(_kv_table(
        [["Recorded steps", "Cases touched"],
         [str(report.total_entries), str(report.cases_touched)]],
        [40 * mm, 40 * mm],
    ))

    story.append(Paragraph("By stage", styles["h2"]))
    rows = [["Stage", "Steps"]] + [
        [_human(row.label), str(row.entries)] for row in report.by_stage
    ]
    if len(rows) == 1:
        rows.append(["Nothing recorded in this period", "—"])
    story.append(_kv_table(rows, [60 * mm, 30 * mm]))

    if report.by_action:
        story.append(Paragraph("Most frequent actions", styles["h2"]))
        story.append(_kv_table(
            [["Action", "Times"]] + [[_human(r.label), str(r.entries)] for r in report.by_action],
            [80 * mm, 30 * mm],
        ))

    if entries:
        story.append(PageBreak())
        story.append(Paragraph("Recorded steps", styles["h2"]))
        story.append(Paragraph(
            "Append-only. Every step Revora took, in the order it took them.",
            styles["muted"],
        ))
        rows = [["When", "Customer", "Stage", "What happened"]]
        for entry in entries:
            rows.append([
                _when(entry.get("timestamp")),
                entry.get("customer_name") or "—",
                _human(entry.get("stage")),
                _human(entry.get("action")),
            ])
        story.append(_kv_table(rows, [32 * mm, 38 * mm, 30 * mm, 62 * mm]))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
