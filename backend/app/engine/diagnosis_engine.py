"""Deterministic root-cause diagnosis. BUILD_SPEC Section 6.

This engine is AUTHORITATIVE. Section 4a is explicit: "rule-based
`diagnosis_engine` remains authoritative for the action actually taken
(safety/auditability)". The ML classifier in app/ml/ runs beside it as an
independent check and never overrides it.

Everything here is a lookup or a comparison — no model, no randomness, no LLM.
Two events with identical signals always produce an identical diagnosis, which
is what lets the audit trail be replayed and defended.

How a cause is reached
----------------------
1. The gateway error code, when present, is the strongest evidence. Razorpay's
   vocabulary is precise: BAD_REQUEST_CARD_EXPIRED means one thing.
2. The code is then constrained by event type. ``ROOT_CAUSES_BY_EVENT_TYPE``
   (Section 6's table, encoded in app/enums.py during Session 1) is the
   authority on which causes are legal where, so a mandate code can never yield
   a card cause.
3. Codes that carry no discriminating information — Razorpay's generic
   ``BAD_REQUEST_PAYMENT_FAILED`` is the main one — deliberately produce a LOW
   confidence result rather than a confident guess. Section 11 requires ~5% of
   synthetic events to be genuinely ambiguous; those must land in the
   low-confidence bucket and reach /exceptions rather than being force-classified.
4. Event types with no gateway code at all (invoice_overdue never had a charge
   attempted) fall back to structural signals — days overdue, explicit dispute
   or approval markers on the raw signal.

Honest limitation, stated rather than hidden
--------------------------------------------
For ``invoice_overdue``, a due date alone cannot distinguish `forgotten` from
`cash_flow_delay` from `awaiting_approval`. Absent an explicit marker the engine
returns its best structural guess at MODERATE-to-LOW confidence, and those
events are expected to surface for review. Inflating that confidence would make
the demo look better and the system worse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.database import utcnow
from app.enums import ROOT_CAUSES_BY_EVENT_TYPE, EventType, RootCauseCode
from app.models.diagnosis import Diagnosis
from app.models.risk_event import RiskEvent

#: Below this, a diagnosis is treated as low-confidence: the event is routed to
#: /exceptions for review rather than acted on with false certainty.
LOW_CONFIDENCE_THRESHOLD = 0.60

# Confidence tiers, named so the numbers are not scattered magic values.
CONFIDENCE_DEFINITIVE = 0.95  # unambiguous gateway code, valid for this type
CONFIDENCE_STRONG = 0.85  # explicit structural marker on the raw signal
CONFIDENCE_MODERATE = 0.65  # solid structural inference (e.g. days overdue)
CONFIDENCE_WEAK = 0.40  # plausible but undetermined
CONFIDENCE_AMBIGUOUS = 0.30  # generic code carrying no information


class ActionCode(str, Enum):
    """Interventions from the Section 6 table. No others may be invented.

    Defined here rather than in app/enums.py because no column stores an
    ``ActionCode`` as an enum — ``Decision.action_code`` is a string. This is
    engine vocabulary, and Section 6's table is what fixes it.
    """

    # payment_degraded
    UPDATE_CARD_EMAIL = "update_card_email"
    SMS_REMINDER = "sms_reminder"
    # checkout_abandoned
    IN_APP_NUDGE = "in_app_nudge"
    EMAIL_SAVED_CART = "email_saved_cart"
    # subscription_failed
    AWAIT_GATEWAY_AUTO_RETRY = "await_gateway_auto_retry"
    # invoice_overdue
    FRIENDLY_REMINDER = "friendly_reminder"
    REMINDER_WITH_CALL_SCRIPT = "reminder_with_call_script"
    FORMAL_NOTICE = "formal_notice"
    # mandate_failed
    REAUTH_NUDGE = "reauth_nudge"
    RETRY_PAYMENT = "retry_payment"
    RETRY_SALARY_WINDOW = "retry_salary_window"
    FINAL_RETRY = "final_retry"
    # shared
    HUMAN_HANDOFF = "human_handoff"
    NO_ACTION = "no_action"


#: Which contact channel an action uses. Feeds the policy engine's per-channel
#: contact cap and, in session 8, template selection.
CHANNEL_BY_ACTION: dict[ActionCode, str] = {
    ActionCode.UPDATE_CARD_EMAIL: "email",
    ActionCode.SMS_REMINDER: "sms",
    ActionCode.IN_APP_NUDGE: "in_app",
    ActionCode.EMAIL_SAVED_CART: "email",
    ActionCode.AWAIT_GATEWAY_AUTO_RETRY: "none",
    ActionCode.FRIENDLY_REMINDER: "email",
    ActionCode.REMINDER_WITH_CALL_SCRIPT: "voice_script",
    ActionCode.FORMAL_NOTICE: "email",
    ActionCode.REAUTH_NUDGE: "sms",
    ActionCode.RETRY_PAYMENT: "none",
    ActionCode.RETRY_SALARY_WINDOW: "none",
    ActionCode.FINAL_RETRY: "none",
    ActionCode.HUMAN_HANDOFF: "human_handoff",
    ActionCode.NO_ACTION: "none",
}

#: Causes Section 6 marks as hard: "no retry, immediate stop".
HARD_STOP_CAUSES: frozenset[RootCauseCode] = frozenset(
    {
        RootCauseCode.ISSUER_DECLINED,  # payment_degraded
        RootCauseCode.BANK_REJECTED,  # mandate_failed — "same logic as issuer_declined"
        RootCauseCode.HALTED_AFTER_MAX_RETRIES,  # subscription_failed — hard stop on `halted`
        RootCauseCode.MANDATE_REVOKED,  # nothing left to charge against
        RootCauseCode.REVOKED,
    }
)

#: Razorpay-vocabulary error code -> the cause it indicates. The inverse of what
#: the synthetic generator emits, but defined independently here: in production
#: these codes arrive from Razorpay, not from our generator, so the engine must
#: own its own reading of them.
CAUSE_BY_ERROR_CODE: dict[str, RootCauseCode] = {
    "BAD_REQUEST_CARD_EXPIRED": RootCauseCode.CARD_EXPIRED,
    "BAD_REQUEST_PAYMENT_INSUFFICIENT_FUNDS": RootCauseCode.INSUFFICIENT_FUNDS,
    "GATEWAY_ERROR_ISSUER_DECLINED": RootCauseCode.ISSUER_DECLINED,
    "GATEWAY_ERROR_TIMEOUT": RootCauseCode.NETWORK_TIMEOUT,
    "GATEWAY_ERROR_ISSUER_DOWN": RootCauseCode.BANK_SERVER_DOWN,
    "BAD_REQUEST_3DS_AUTHENTICATION_FAILED": RootCauseCode.THREE_DS_FAILED,
    "BAD_REQUEST_RISK_THRESHOLD_EXCEEDED": RootCauseCode.RISK_ENGINE_BLOCKED,
    "BAD_REQUEST_MANDATE_NOT_AUTHENTICATED": RootCauseCode.NOT_AUTHENTICATED,
    "BAD_REQUEST_MANDATE_INSUFFICIENT_BALANCE": RootCauseCode.INSUFFICIENT_BALANCE,
    "BAD_REQUEST_MANDATE_BANK_REJECTED": RootCauseCode.BANK_REJECTED,
    "BAD_REQUEST_MANDATE_EXPIRED": RootCauseCode.EXPIRED,
    "BAD_REQUEST_MANDATE_REVOKED": RootCauseCode.MANDATE_REVOKED,
    "BAD_REQUEST_SUBSCRIPTION_PAUSED": RootCauseCode.USER_PAUSED,
    "BAD_REQUEST_SUBSCRIPTION_HALTED": RootCauseCode.HALTED_AFTER_MAX_RETRIES,
    "BAD_REQUEST_PAYMENT_TIMED_OUT": RootCauseCode.OTP_TIMEOUT,
}

#: Codes that carry no discriminating information. Razorpay returns
#: BAD_REQUEST_PAYMENT_FAILED for a wide range of underlying reasons, so it must
#: never produce a confident diagnosis.
AMBIGUOUS_ERROR_CODES: frozenset[str] = frozenset({"BAD_REQUEST_PAYMENT_FAILED"})

#: Fallback cause per event type when no signal determines one.
FALLBACK_CAUSE: dict[EventType, RootCauseCode] = {
    EventType.PAYMENT_DEGRADED: RootCauseCode.INSUFFICIENT_FUNDS,
    EventType.CHECKOUT_ABANDONED: RootCauseCode.UNKNOWN,
    EventType.SUBSCRIPTION_FAILED: RootCauseCode.INSUFFICIENT_FUNDS,
    EventType.INVOICE_OVERDUE: RootCauseCode.FORGOTTEN,
    EventType.MANDATE_FAILED: RootCauseCode.NOT_AUTHENTICATED,
}


@dataclass(frozen=True)
class DiagnosisResult:
    """A root-cause verdict with the evidence that produced it."""

    root_cause: RootCauseCode
    confidence: float
    evidence: list[str] = field(default_factory=list)

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < LOW_CONFIDENCE_THRESHOLD

    @property
    def is_hard_stop(self) -> bool:
        """Section 6: no retry, immediate stop."""
        return self.root_cause in HARD_STOP_CAUSES


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def classify(event: RiskEvent, *, now: datetime | None = None) -> DiagnosisResult:
    """Determine the root cause of ``event``. Pure — touches no database.

    Returns a cause that is always legal for the event's type, per Section 6.
    """
    moment = now or utcnow()
    event_type = (
        event.type if isinstance(event.type, EventType) else EventType(event.type)
    )
    permitted = ROOT_CAUSES_BY_EVENT_TYPE[event_type]
    raw = event.raw_signal if isinstance(event.raw_signal, dict) else {}
    evidence: list[str] = [f"event_type={event_type.value}"]

    error_code = raw.get("gateway_error_code")
    if isinstance(error_code, str) and error_code:
        evidence.append(f"gateway_error_code={error_code}")

        if error_code in AMBIGUOUS_ERROR_CODES:
            # Generic code: real ambiguity, reported as such.
            cause = _ambiguous_cause_for(event_type, permitted)
            evidence.append("generic gateway code carries no discriminating signal")
            evidence.append("routed for review: confidence below threshold")
            return DiagnosisResult(cause, CONFIDENCE_AMBIGUOUS, evidence)

        mapped = CAUSE_BY_ERROR_CODE.get(error_code)
        if mapped is not None and mapped in permitted:
            evidence.append(f"code maps to {mapped.value}, valid for {event_type.value}")
            return DiagnosisResult(mapped, CONFIDENCE_DEFINITIVE, evidence)

        if mapped is not None:
            # A real code, but not legal for this event type — a genuine
            # inconsistency worth surfacing rather than silently coercing.
            evidence.append(
                f"code maps to {mapped.value}, which is NOT valid for "
                f"{event_type.value} — signal inconsistent"
            )
            return DiagnosisResult(
                FALLBACK_CAUSE[event_type], CONFIDENCE_WEAK, evidence
            )

        evidence.append("unrecognised gateway code")
        return DiagnosisResult(FALLBACK_CAUSE[event_type], CONFIDENCE_WEAK, evidence)

    # --- no gateway code: structural signals only --------------------------
    if event_type == EventType.INVOICE_OVERDUE:
        return _diagnose_invoice(event, raw, evidence, moment)

    if event_type == EventType.CHECKOUT_ABANDONED:
        evidence.append("no gateway error code: customer left before a charge was attempted")
        if raw.get("cart_value_changed"):
            evidence.append("cart value changed before drop-off")
            return DiagnosisResult(RootCauseCode.PRICE_SHOCK, CONFIDENCE_STRONG, evidence)
        if raw.get("available_methods") == 0:
            evidence.append("no preferred payment method available")
            return DiagnosisResult(
                RootCauseCode.NO_PREFERRED_METHOD, CONFIDENCE_STRONG, evidence
            )
        evidence.append("no discriminating signal: cause undetermined")
        return DiagnosisResult(RootCauseCode.UNKNOWN, CONFIDENCE_WEAK, evidence)

    evidence.append("no gateway error code and no structural signal")
    return DiagnosisResult(FALLBACK_CAUSE[event_type], CONFIDENCE_WEAK, evidence)


def _ambiguous_cause_for(
    event_type: EventType, permitted: frozenset[RootCauseCode]
) -> RootCauseCode:
    """Best-guess cause for a generic code, always legal for the type."""
    if RootCauseCode.UNKNOWN in permitted:
        return RootCauseCode.UNKNOWN
    return FALLBACK_CAUSE[event_type]


def _diagnose_invoice(
    event: RiskEvent, raw: dict[str, Any], evidence: list[str], moment: datetime
) -> DiagnosisResult:
    """Invoice diagnosis from structural signals. Section 6, invoice_overdue row.

    Explicit markers are trusted. A due date alone is not enough to separate the
    remaining causes, so those return moderate confidence at best.
    """
    if raw.get("dispute_raised"):
        evidence.append("dispute_raised flag present on signal")
        return DiagnosisResult(RootCauseCode.DISPUTED_AMOUNT, CONFIDENCE_STRONG, evidence)
    if raw.get("approval_pending"):
        evidence.append("approval_pending flag present on signal")
        return DiagnosisResult(RootCauseCode.AWAITING_APPROVAL, CONFIDENCE_STRONG, evidence)
    if raw.get("delivery_failed"):
        evidence.append("delivery_failed flag present on signal")
        return DiagnosisResult(RootCauseCode.DELIVERY_FAILURE, CONFIDENCE_STRONG, evidence)
    if raw.get("broken_promise_of_event_id"):
        evidence.append("raised by the promise-to-pay watcher after a broken promise")
        return DiagnosisResult(RootCauseCode.BROKEN_PTP, CONFIDENCE_DEFINITIVE, evidence)

    days_overdue = raw.get("days_overdue")
    if isinstance(days_overdue, int):
        evidence.append(f"days_overdue={days_overdue}")
        if days_overdue < 7:
            evidence.append("recently due: most often simply forgotten")
            return DiagnosisResult(RootCauseCode.FORGOTTEN, CONFIDENCE_MODERATE, evidence)
        if days_overdue > 30:
            evidence.append("long overdue: consistent with a cash-flow delay")
            evidence.append(
                "no explicit marker present — due date alone cannot separate "
                "cash_flow_delay from awaiting_approval; flagged for review"
            )
            return DiagnosisResult(RootCauseCode.CASH_FLOW_DELAY, CONFIDENCE_WEAK, evidence)
        if event.is_b2b:
            evidence.append("B2B receivable in the 7-30d window: approval cycles are typical")
            return DiagnosisResult(
                RootCauseCode.AWAITING_APPROVAL, CONFIDENCE_MODERATE, evidence
            )
        evidence.append("7-30d window with no explicit marker")
        return DiagnosisResult(RootCauseCode.FORGOTTEN, CONFIDENCE_WEAK, evidence)

    evidence.append("no days_overdue on signal: cause undetermined")
    return DiagnosisResult(RootCauseCode.FORGOTTEN, CONFIDENCE_WEAK, evidence)


# --------------------------------------------------------------------------- #
# Section 6 intervention table
# --------------------------------------------------------------------------- #


def candidate_actions(
    event: RiskEvent,
    result: DiagnosisResult,
    attempt_number: int,
    amount_threshold: Decimal | None = None,
) -> list[ActionCode]:
    """Actions Section 6 permits for this event, cause and attempt.

    Returns candidates in table order. The probability engine scores them and
    the policy engine gates them; this function only decides what is *eligible*.
    An empty list means Section 6 permits nothing — a hard stop.

    ``amount_threshold`` implements the table's "Human handoff if amount >
    threshold" condition. Without it a handoff would be eligible on every event,
    and because a human converts better than an email the scorer would pick it
    for mid-sized balances too — escalating cases the table never intended to
    escalate. When None, no amount-conditional handoff is offered.

    Note the asymmetry, which is deliberate and follows the table: for
    ``invoice_overdue`` past 30 days the escalation column names human handoff
    outright, so it is offered there regardless of amount.
    """
    event_type = (
        event.type if isinstance(event.type, EventType) else EventType(event.type)
    )

    # Hard causes short-circuit every table row: "no retry, immediate stop".
    if result.is_hard_stop:
        return []

    over_threshold = (
        amount_threshold is not None
        and event.amount is not None
        and event.amount > amount_threshold
    )

    if event_type == EventType.PAYMENT_DEGRADED:
        if attempt_number <= 1:
            actions = [ActionCode.UPDATE_CARD_EMAIL]
        elif attempt_number == 2:
            actions = [ActionCode.SMS_REMINDER]
        else:
            return []  # "else after 2 attempts"
        if over_threshold:
            actions.append(ActionCode.HUMAN_HANDOFF)
        return actions

    if event_type == EventType.CHECKOUT_ABANDONED:
        # Escalation column is "—": low-value, high-volume, never escalated.
        if attempt_number <= 1:
            return [ActionCode.IN_APP_NUDGE]
        if attempt_number == 2:
            return [ActionCode.EMAIL_SAVED_CART]  # "max 1"
        return []  # "after 1 email"

    if event_type == EventType.SUBSCRIPTION_FAILED:
        # "React to Razorpay's own auto-retry/webhook state — do not force
        # extra retries." The only legal move is to wait and observe.
        return [ActionCode.AWAIT_GATEWAY_AUTO_RETRY]

    if event_type == EventType.INVOICE_OVERDUE:
        raw = event.raw_signal if isinstance(event.raw_signal, dict) else {}
        days_overdue = raw.get("days_overdue")
        days = days_overdue if isinstance(days_overdue, int) else 0
        if days < 7:
            return [ActionCode.FRIENDLY_REMINDER]
        if days <= 30:
            actions = [ActionCode.REMINDER_WITH_CALL_SCRIPT]
            if over_threshold:
                actions.append(ActionCode.HUMAN_HANDOFF)
            return actions
        # ">30d: formal notice, human handoff, escalation L2 (ceiling)"
        return [ActionCode.FORMAL_NOTICE, ActionCode.HUMAN_HANDOFF]

    if event_type == EventType.MANDATE_FAILED:
        # Section 6: "Real sequence, not generic 2-attempt."
        if attempt_number <= 1:
            if result.root_cause == RootCauseCode.NOT_AUTHENTICATED:
                return [ActionCode.REAUTH_NUDGE]
            return [ActionCode.RETRY_PAYMENT]
        if attempt_number == 2:
            if result.root_cause == RootCauseCode.INSUFFICIENT_BALANCE:
                return [ActionCode.RETRY_SALARY_WINDOW]
            return [ActionCode.RETRY_PAYMENT]
        if attempt_number == 3:
            actions = [ActionCode.FINAL_RETRY]
            if over_threshold:
                actions.append(ActionCode.HUMAN_HANDOFF)
            return actions
        return []  # "hard stop after Day+7 attempt"

    return []


#: Escalation level each action implies. Section 6 caps invoice_overdue at L2.
ESCALATION_LEVEL_BY_ACTION: dict[ActionCode, int] = {
    ActionCode.REMINDER_WITH_CALL_SCRIPT: 1,
    ActionCode.FINAL_RETRY: 1,
    ActionCode.FORMAL_NOTICE: 2,
    ActionCode.HUMAN_HANDOFF: 2,
}


def escalation_level_for(action: ActionCode) -> int:
    """Escalation level an action would move the event to. 0 = no escalation."""
    return ESCALATION_LEVEL_BY_ACTION.get(action, 0)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def diagnose(
    session: Session, event: RiskEvent, *, now: datetime | None = None
) -> Diagnosis:
    """Classify ``event`` and persist the Diagnosis row.

    An event is diagnosed once (``Diagnosis.event_id`` is the primary key). If a
    diagnosis already exists it is returned unchanged rather than recomputed, so
    a replayed batch cannot rewrite history.
    """
    existing = session.get(Diagnosis, event.id)
    if existing is not None:
        return existing

    result = classify(event, now=now)
    row = Diagnosis(
        event_id=event.id,
        root_cause_code=result.root_cause,
        confidence=result.confidence,
        evidence=list(result.evidence),
        diagnosed_at=now or utcnow(),
    )
    session.add(row)
    session.flush()
    return row
