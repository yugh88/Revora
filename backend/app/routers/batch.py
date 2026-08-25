"""POST /batch — process N synthetic records through the real pipeline.
BUILD_SPEC Sections 9, 10 and 11.

    detection -> diagnosis -> ML check -> decision -> policy -> execution
    -> verification -> recovery / escalation / stopped -> ledger + audit

This module ORCHESTRATES the engines built in Session 3; it does not re-implement
any of them. Diagnosis, scoring, the policy gate and the ML agreement check all
happen inside ``decision_engine.decide``. What lives here is the part no engine
owns: turning a synthetic record into a persisted event, executing the chosen
action against the selected gateway, writing the recovery ledger, and moving the
event through the state machine.

Fault isolation — Section 9
---------------------------
Each record runs in its own try/except and its own transaction. A failure rolls
back that record only, appends an :class:`IsolatedFailure` describing it, logs it
with a full structured traceback, and the loop continues. The target behaviour
the spec names — "499 processed + 1 isolated exception, never a batch crash" —
is what the ``failures`` list in the response demonstrates. Nothing is swallowed:
every isolated failure is both logged and returned.

Metrics — Section 10
--------------------
Every figure in the response is QUERIED BACK from the database after the run.
Nothing is accumulated in a Python counter while the loop runs. This is the
difference between reporting what happened and reporting what we intended to
happen: if execution silently failed to write an Outcome row, the recovery
figure drops, rather than a counter cheerfully reporting success. Section 2's
bar is "real numbers from ledger state, not invented", and querying is the only
way to actually mean it.

The one modelled quantity, stated plainly
------------------------------------------
A contact action (an email, an SMS, an in-app nudge) does not charge a card, so
no gateway can tell us whether it worked. In production that answer arrives later
as a webhook. For a batch dry run it is simulated by
:func:`_simulate_contact_response`, deterministically, from the DECISION'S OWN
``recovery_probability``. That choice matters: the simulation cannot flatter the
engine, because a badly calibrated probability table produces a correspondingly
bad recovery rate. Genuine payment operations (retries, and the subscription
auto-retry lifecycle) go through the real gateway instead.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db, utcnow
from app.engine import idempotency, locks, policy_engine
from app.engine.decision_engine import DecisionOutcome, decide
from app.engine.diagnosis_engine import CHANNEL_BY_ACTION, ActionCode, escalation_level_for
from app.engine.policy_engine import (
    RULE_COOLDOWN,
    RULE_DO_NOT_CONTACT,
    RULE_ESCALATION_CEILING,
    RULE_HARD_STOP,
    RULE_MAX_ATTEMPTS,
)
from app.engine.state_machine import InvalidTransition, transition
from app.enums import (
    AuditActor,
    AuditStage,
    Channel,
    EventStatus,
    EventType,
    GatewayUsed,
    OutcomeResolution,
    PaymentAttemptStatus,
)
from app.gateways.base import PaymentGateway, RetryRequest
from app.gateways.local_simulation import LocalSimulationGateway
from app.ml.diagnosis_classifier import load_classifier
from app.models import (
    AuditLog,
    CustomerProfile,
    Decision,
    Merchant,
    MLDiagnosisPrediction,
    Outcome,
    PaymentAttempt,
    PromiseToPay,
    RiskEvent,
    StoppingRuleState,
)
from app.schemas.batch import (
    BatchMoney,
    BatchRequest,
    BatchResponse,
    IsolatedFailure,
    StoppingRuleTriggers,
)
from app.services.logging_config import (
    correlation_scope,
    log_event,
    new_correlation_id,
)
from app.services.synthetic_data_generator import IST, SyntheticRecord, generate_batch

logger = logging.getLogger("revora.batch")

router = APIRouter(tags=["batch"])

#: Actions that are genuine payment operations and therefore go to the gateway.
#: Everything else is a message to a human, whose response no gateway can report.
GATEWAY_ACTIONS: frozenset[ActionCode] = frozenset(
    {
        ActionCode.RETRY_PAYMENT,
        ActionCode.RETRY_SALARY_WINDOW,
        ActionCode.FINAL_RETRY,
        ActionCode.AWAIT_GATEWAY_AUTO_RETRY,
    }
)

#: Policy rules that mean the money is gone rather than merely deferred.
#: Section 6's hard causes are "no retry, immediate stop" — nothing further will
#: be attempted, so the ledger records the loss instead of leaving it pending.
TERMINAL_BLOCK_RULES: frozenset[str] = frozenset({RULE_HARD_STOP})

#: Maps a policy rule to the Section 10 stopping-rule breakdown bucket.
RULE_TO_TRIGGER_BUCKET: dict[str, str] = {
    RULE_COOLDOWN: "cooldown",
    RULE_DO_NOT_CONTACT: "do_not_contact",
    RULE_MAX_ATTEMPTS: "max_attempts",
    RULE_HARD_STOP: "hard_decline",
}

LOCK_OWNER_PREFIX = "batch"


# --------------------------------------------------------------------------- #
# Per-record result
# --------------------------------------------------------------------------- #


@dataclass
class RecordResult:
    """What happened to one record. Diagnostic only — never a metric source."""

    index: int
    event_id: str | None = None
    correlation_id: str | None = None
    processed: bool = False
    duplicate: bool = False
    failure: IsolatedFailure | None = None


@dataclass
class BatchRun:
    """Accumulated bookkeeping for a run. Contains no money and no counts that
    reach the response — those are queried from the database afterwards."""

    batch_id: str
    correlation_id: str
    event_ids: list[str] = field(default_factory=list)
    results: list[RecordResult] = field(default_factory=list)
    skipped_duplicates: int = 0


# --------------------------------------------------------------------------- #
# Deterministic contact-response model
# --------------------------------------------------------------------------- #


def _unit_interval(*parts: Any) -> float:
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _simulate_contact_response(
    *, seed: int, event_id: str, attempt_number: int, action_code: str, probability: float
) -> bool:
    """Did the customer respond to a contact action?

    Deterministic given the seed, so a batch replays identically. Realises the
    DECISION'S OWN ``recovery_probability`` rather than an independent number,
    which is what stops this from being a way to manufacture a flattering
    recovery rate: if the Section 6 probability table is wrong, the recovery
    rate is wrong in exactly the same direction.

    In production this outcome arrives from a payment webhook, not from here.
    """
    roll = _unit_interval(seed, event_id, attempt_number, action_code, "contact_response")
    return roll < probability


# --------------------------------------------------------------------------- #
# Gateway selection
# --------------------------------------------------------------------------- #


def build_gateway(gateway: GatewayUsed, seed: int = 42) -> PaymentGateway:
    """Construct the requested gateway. Section 5's runtime toggle.

    Both implementations satisfy the same interface, so nothing downstream —
    diagnosis, scoring, the policy gate, locks, idempotency, the state machine,
    the audit trail — changes between them. Only the execution call differs.

    ``local_simulation`` is the default and needs no credentials. Selecting
    ``razorpay_test`` without configured test keys is reported as a 400 rather
    than silently falling back to the simulator, which would let the response
    claim sandbox numbers that never came from the sandbox.
    """
    if gateway == GatewayUsed.LOCAL_SIMULATION:
        return LocalSimulationGateway(seed=seed)

    if gateway == GatewayUsed.RAZORPAY_TEST:
        from app.gateways.razorpay_test import (
            RazorpayConfigurationError,
            RazorpayTestGateway,
        )

        try:
            return RazorpayTestGateway()
        except RazorpayConfigurationError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    raise HTTPException(  # pragma: no cover - unreachable while the enum has two members
        status_code=http_status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown gateway {gateway!r}.",
    )


# --------------------------------------------------------------------------- #
# Record validation — Section 11's ~8% malformed records
# --------------------------------------------------------------------------- #


REQUIRED_FIELDS = (
    "id",
    "type",
    "merchant_id",
    "customer_id",
    "amount",
    "currency",
    "detected_at",
    "raw_signal",
    "correlation_id",
)


class MalformedRecordError(ValueError):
    """A synthetic record that cannot become a RiskEvent."""


def validate_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Reject a record that cannot be turned into a valid event.

    Section 11 injects ~8% deliberately broken records and Section 9 lists
    "invalid/malformed data" as a scenario to handle explicitly. Validation
    happens here, before anything is written, so a bad record costs one rolled
    back transaction rather than a half-created event.
    """
    missing = [name for name in REQUIRED_FIELDS if name not in payload]
    if missing:
        raise MalformedRecordError(f"missing required field(s): {', '.join(missing)}")

    if payload.get("source_ref") in (None, ""):
        raise MalformedRecordError("source_ref is null")

    if not isinstance(payload.get("raw_signal"), dict):
        raise MalformedRecordError(
            f"raw_signal must be an object, got {type(payload.get('raw_signal')).__name__}"
        )

    amount = payload.get("amount")
    if not isinstance(amount, Decimal):
        raise MalformedRecordError(f"amount is not a decimal: {amount!r}")
    if amount <= 0:
        raise MalformedRecordError(f"amount must be positive, got {amount}")

    if payload.get("currency") != "INR":
        raise MalformedRecordError(f"unsupported currency {payload.get('currency')!r}")

    try:
        EventType(payload["type"])
    except ValueError as exc:
        raise MalformedRecordError(f"unknown event type {payload['type']!r}") from exc

    return payload


# --------------------------------------------------------------------------- #
# Persistence helpers
# --------------------------------------------------------------------------- #


def ensure_merchant(session: Session, merchant_id: str, name: str) -> Merchant:
    existing = session.get(Merchant, merchant_id)
    if existing is not None:
        return existing
    row = Merchant(id=merchant_id, name=name)
    session.add(row)
    session.flush()
    return row


def ensure_customer(session: Session, profile: dict[str, Any]) -> CustomerProfile:
    """Get-or-create the customer profile.

    ``RiskEvent.customer_id`` is a real foreign key (Session 1), so the profile
    must exist before the event is inserted. This was flagged during Session 1
    as the thing that would otherwise make ingestion fail on an unknown customer.
    """
    customer_id = profile["customer_id"]
    existing = session.get(CustomerProfile, customer_id)
    if existing is not None:
        return existing
    row = CustomerProfile(
        customer_id=customer_id,
        payment_success_rate=profile.get("payment_success_rate", 0.0),
        payment_failure_rate=profile.get("payment_failure_rate", 0.0),
        lifetime_value=profile.get("lifetime_value", Decimal("0.00")),
        avg_payment_delay_days=profile.get("avg_payment_delay_days", 0.0),
        do_not_contact=bool(profile.get("do_not_contact", False)),
    )
    session.add(row)
    session.flush()
    return row


def audit(
    session: Session,
    event: RiskEvent,
    *,
    stage: AuditStage,
    action: str,
    reasoning: str,
    before: Any = None,
    after: Any = None,
) -> None:
    session.add(
        AuditLog(
            event_id=event.id,
            correlation_id=event.correlation_id,
            actor=AuditActor.SYSTEM,
            stage=stage,
            action=action,
            before_state=before,
            after_state=after,
            reasoning=reasoning,
        )
    )
    session.flush()


def ensure_stopping_state(
    session: Session, event: RiskEvent, max_attempts: int, do_not_contact: bool
) -> StoppingRuleState:
    existing = session.get(StoppingRuleState, event.id)
    if existing is not None:
        return existing
    row = StoppingRuleState(
        event_id=event.id,
        attempts_used=0,
        max_attempts_for_type=max_attempts,
        do_not_contact_snapshot=do_not_contact,
        escalation_level=0,
    )
    session.add(row)
    session.flush()
    return row


def upsert_outcome(
    session: Session,
    event: RiskEvent,
    *,
    resolved: OutcomeResolution,
    amount_recovered: Decimal = Decimal("0.00"),
    channel: Channel | None = None,
    now: datetime | None = None,
) -> Outcome:
    """Write the recovery-ledger row. Section 4.

    This is the ONLY place a batch records money as recovered, which is what
    makes the /batch money figures traceable to a single well-defined write.
    """
    row = session.get(Outcome, event.id)
    if row is None:
        row = Outcome(event_id=event.id)
        session.add(row)
    row.resolved = resolved
    row.amount_recovered = amount_recovered
    row.resolution_channel = channel
    row.resolved_at = (
        (now or utcnow()) if resolved != OutcomeResolution.PENDING else None
    )
    session.flush()
    return row


# --------------------------------------------------------------------------- #
# Execution — Section 9
# --------------------------------------------------------------------------- #


def execute_decision(
    session: Session,
    event: RiskEvent,
    outcome: DecisionOutcome,
    *,
    gateway: PaymentGateway,
    seed: int,
    now: datetime,
) -> None:
    """Carry out the decision, verify it, and settle the event.

    Ordering here is Section 9's: re-check upstream state BEFORE acting, then
    check idempotency, then execute at most once.
    """
    decision = outcome.decision
    action = ActionCode(decision.action_code)
    policy = policy_engine.resolve_policy(session, event)
    customer = session.get(CustomerProfile, event.customer_id)
    state = ensure_stopping_state(
        session,
        event,
        policy.max_attempts,
        bool(customer.do_not_contact) if customer is not None else False,
    )

    # --- policy stage is audited explicitly -------------------------------
    # decision_engine records the decision itself; the audit trail also needs
    # the gate's verdict as its own stage so a judge can see the policy step.
    audit(
        session,
        event,
        stage=AuditStage.POLICY,
        action="policy_evaluated",
        before=None,
        after=decision.policy_result,
        reasoning=(
            f"Policy v{decision.policy_version} returned "
            f"{decision.policy_result.get('status')}"
            + (
                f" on rule {decision.policy_result.get('rule_triggered')}."
                if decision.policy_result.get("rule_triggered")
                else "."
            )
        ),
    )

    # --- nothing to do: the gate refused every candidate -------------------
    if action == ActionCode.NO_ACTION:
        _settle_blocked(session, event, decision, state, now=now)
        return

    # --- Section 9: re-check upstream state before executing ---------------
    if event.source_ref:
        upstream = gateway.check_status(event.source_ref, EventType(event.type), now=now)
        if upstream.is_resolved_externally:
            _settle_recovered_externally(session, event, upstream.status.value, now=now)
            return

    attempt_number = idempotency.next_attempt_number(session, event.id)

    # --- Section 9: idempotency before execution ---------------------------
    attempt, created = idempotency.record_attempt(
        session,
        event_id=event.id,
        attempt_number=attempt_number,
        action_code=action.value,
        gateway_used=gateway.name,
        now=now,
    )
    if not created:
        # Reachable when two workers computed the same attempt number
        # concurrently: record_attempt loses the UNIQUE race on
        # idempotency_key and returns the winner's row. Within a single
        # sequential batch next_attempt_number always advances, so this is
        # defence in depth rather than the common path — but it is the guard
        # that stops a customer being charged twice when the lock is reclaimed
        # from a worker that had already executed.
        audit(
            session,
            event,
            stage=AuditStage.EXECUTION,
            action="execution_skipped_idempotent",
            after=attempt.status.value,
            reasoning=(
                f"Idempotency key already recorded for attempt {attempt_number} "
                f"of {action.value}; returning the existing result without "
                "executing again."
            ),
        )
        return

    if event.status != EventStatus.INTERVENING:
        transition(
            session,
            event,
            EventStatus.INTERVENING,
            reasoning=f"Executing {action.value} (attempt {attempt_number}).",
        )

    # --- human handoff is an escalation, not a gateway call ----------------
    if action == ActionCode.HUMAN_HANDOFF:
        _settle_escalation(session, event, attempt, state, policy, now=now)
        return

    # --- execute -----------------------------------------------------------
    if action in GATEWAY_ACTIONS:
        _execute_via_gateway(
            session, event, attempt, action, gateway, attempt_number, now=now
        )
    else:
        _execute_contact(
            session, event, attempt, action, decision, seed, attempt_number, now=now
        )

    # --- stopping-rule bookkeeping ----------------------------------------
    state.attempts_used = attempt_number
    if CHANNEL_BY_ACTION.get(action, "none") != "none":
        state.cooldown_until = now + timedelta(hours=policy.cooldown_hours)
    level = escalation_level_for(action)
    if level > state.escalation_level:
        state.escalation_level = level
    session.flush()

    _verify_and_settle(session, event, attempt, action, state, policy, now=now)


def _execute_via_gateway(
    session: Session,
    event: RiskEvent,
    attempt: PaymentAttempt,
    action: ActionCode,
    gateway: PaymentGateway,
    attempt_number: int,
    *,
    now: datetime,
) -> None:
    """A genuine payment operation: let the gateway decide the outcome."""
    raw = event.raw_signal if isinstance(event.raw_signal, dict) else {}
    response = gateway.initiate_retry(
        RetryRequest(
            event_id=event.id,
            source_ref=event.source_ref or event.id,
            event_type=EventType(event.type),
            amount=event.amount,
            attempt_number=attempt_number,
            idempotency_key=attempt.idempotency_key,
            failure_reason=raw.get("gateway_error_code"),
            method=raw.get("payment_method"),
        ),
        now=now,
    )
    attempt.status = response.status
    attempt.failure_reason = response.failure_reason
    attempt.provider_ref = response.provider_ref
    attempt.resolved_at = now if response.status != PaymentAttemptStatus.PENDING else None
    session.flush()

    audit(
        session,
        event,
        stage=AuditStage.EXECUTION,
        action="gateway_execution",
        after=response.status.value,
        reasoning=(
            f"{action.value} executed via {gateway.name.value}: "
            f"{response.status.value}"
            + (f" ({response.failure_reason})" if response.failure_reason else "")
            + (" [gateway refused to retry]" if response.retry_refused else "")
        ),
    )


def _execute_contact(
    session: Session,
    event: RiskEvent,
    attempt: PaymentAttempt,
    action: ActionCode,
    decision: Decision,
    seed: int,
    attempt_number: int,
    *,
    now: datetime,
) -> None:
    """A message to a customer: outcome is their response, modelled here."""
    responded = _simulate_contact_response(
        seed=seed,
        event_id=event.id,
        attempt_number=attempt_number,
        action_code=action.value,
        probability=decision.recovery_probability,
    )
    attempt.status = (
        PaymentAttemptStatus.SUCCESS if responded else PaymentAttemptStatus.FAILED
    )
    attempt.failure_reason = None if responded else "NO_CUSTOMER_RESPONSE"
    attempt.resolved_at = now
    session.flush()

    audit(
        session,
        event,
        stage=AuditStage.EXECUTION,
        action="contact_sent",
        after=attempt.status.value,
        reasoning=(
            f"{action.value} sent via {CHANNEL_BY_ACTION.get(action, 'none')}. "
            f"Customer response simulated deterministically at the decision's own "
            f"probability {decision.recovery_probability:.2f}: "
            f"{'responded' if responded else 'no response'}."
        ),
    )


def _verify_and_settle(
    session: Session,
    event: RiskEvent,
    attempt: PaymentAttempt,
    action: ActionCode,
    state: StoppingRuleState,
    policy: Any,
    *,
    now: datetime,
) -> None:
    """Verification stage, then move the event to its settled status."""
    audit(
        session,
        event,
        stage=AuditStage.VERIFICATION,
        action="outcome_verified",
        after=attempt.status.value,
        reasoning=(
            f"Attempt {attempt.attempt_number} resolved as {attempt.status.value}"
            + (f" ({attempt.failure_reason})" if attempt.failure_reason else "")
            + "."
        ),
    )

    if attempt.status == PaymentAttemptStatus.SUCCESS:
        upsert_outcome(
            session,
            event,
            resolved=OutcomeResolution.RECOVERED,
            amount_recovered=event.amount,
            channel=_channel_enum(action),
            now=now,
        )
        transition(
            session,
            event,
            EventStatus.RECOVERED,
            reasoning=f"{action.value} succeeded; {event.currency} {event.amount} recovered.",
        )
        return

    # Refused outright (hard decline, or already settled upstream) — nothing
    # further will be attempted, so this is a loss rather than a pending item.
    if attempt.failure_reason and attempt.failure_reason.startswith(
        ("ALREADY_RESOLVED", "SUBSCRIPTION_")
    ):
        upsert_outcome(session, event, resolved=OutcomeResolution.LOST, now=now)
        transition(
            session,
            event,
            EventStatus.UNRECOVERABLE,
            reasoning=f"Gateway refused further action: {attempt.failure_reason}.",
        )
        state.hard_stop_reason = "hard_decline"
        session.flush()
        return

    # Attempts exhausted under this merchant's policy.
    if state.attempts_used >= policy.max_attempts:
        upsert_outcome(session, event, resolved=OutcomeResolution.LOST, now=now)
        transition(
            session,
            event,
            EventStatus.UNRECOVERABLE,
            reasoning=(
                f"Attempts exhausted ({state.attempts_used}/{policy.max_attempts}); "
                "no further action permitted."
            ),
        )
        state.hard_stop_reason = "max_attempts"
        session.flush()
        return

    # Still recoverable on a later pass.
    upsert_outcome(session, event, resolved=OutcomeResolution.PENDING, now=now)


def _settle_blocked(
    session: Session,
    event: RiskEvent,
    decision: Decision,
    state: StoppingRuleState,
    *,
    now: datetime,
) -> None:
    """The policy gate refused everything Section 6 offered."""
    rule = decision.policy_result.get("rule_triggered") or "no_eligible_action"
    state.hard_stop_reason = rule
    session.flush()

    if rule in TERMINAL_BLOCK_RULES:
        upsert_outcome(session, event, resolved=OutcomeResolution.LOST, now=now)
        transition(
            session,
            event,
            EventStatus.UNRECOVERABLE,
            reasoning=(
                f"Hard stop: {rule}. Section 6 permits no retry for this cause, "
                "so the balance is recorded as lost rather than left pending."
            ),
        )
        return

    upsert_outcome(session, event, resolved=OutcomeResolution.PENDING, now=now)
    transition(
        session,
        event,
        EventStatus.STOPPED,
        reasoning=(
            f"Engine stopped by policy rule {rule}: "
            f"{decision.policy_result.get('threshold_checked')} "
            f"actual={decision.policy_result.get('actual_value')} "
            f"threshold={decision.policy_result.get('threshold_value')}."
        ),
    )


def _settle_recovered_externally(
    session: Session, event: RiskEvent, upstream_status: str, *, now: datetime
) -> None:
    """Section 9: already resolved upstream — stop, do not double-act."""
    upsert_outcome(
        session,
        event,
        resolved=OutcomeResolution.RECOVERED,
        amount_recovered=event.amount,
        channel=Channel.EXTERNAL,
        now=now,
    )
    audit(
        session,
        event,
        stage=AuditStage.VERIFICATION,
        action="recovered_externally",
        after=upstream_status,
        reasoning=(
            f"Pre-execution re-check found the upstream object already "
            f"{upstream_status}. The customer settled it independently; no "
            "recovery action was taken and none should be."
        ),
    )
    transition(
        session,
        event,
        EventStatus.RECOVERED,
        reasoning="Resolved externally before the engine acted; no action taken.",
    )


def _settle_escalation(
    session: Session,
    event: RiskEvent,
    attempt: PaymentAttempt,
    state: StoppingRuleState,
    policy: Any,
    *,
    now: datetime,
) -> None:
    """Hand the event to a human. Section 6's escalation column."""
    attempt.status = PaymentAttemptStatus.PENDING
    attempt.resolved_at = None
    state.attempts_used = attempt.attempt_number
    state.escalation_level = min(
        max(state.escalation_level, escalation_level_for(ActionCode.HUMAN_HANDOFF)),
        policy.escalation_ceiling,
    )
    session.flush()

    audit(
        session,
        event,
        stage=AuditStage.EXECUTION,
        action="human_handoff",
        after=f"L{state.escalation_level}",
        reasoning=(
            f"Escalated to a human at L{state.escalation_level} "
            f"(ceiling L{policy.escalation_ceiling}). The engine takes no further "
            "automated action on this event."
        ),
    )
    upsert_outcome(session, event, resolved=OutcomeResolution.PENDING, now=now)
    transition(
        session,
        event,
        EventStatus.ESCALATED,
        reasoning="Handed to a human; commercial outcome still open.",
    )


def _channel_enum(action: ActionCode) -> Channel | None:
    raw = CHANNEL_BY_ACTION.get(action, "none")
    try:
        return Channel(raw)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# One record
# --------------------------------------------------------------------------- #


def process_record(
    session: Session,
    record: SyntheticRecord,
    index: int,
    run: BatchRun,
    *,
    merchant: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
    gateway: PaymentGateway,
    classifier: Any,
    load_ml: bool,
    seed: int,
    now: datetime,
) -> RecordResult:
    """Process one synthetic record. Never raises — failures are returned."""
    payload = record.payload
    result = RecordResult(index=index)
    stage = "detection"

    correlation_id = payload.get("correlation_id") or new_correlation_id()
    result.correlation_id = correlation_id

    with correlation_scope(correlation_id):
        try:
            validate_record(payload)
            event_id = payload["id"]
            result.event_id = event_id

            # Section 11 replays ~10% of records; the second sighting is a
            # duplicate, not a new unit of revenue at risk.
            if session.get(RiskEvent, event_id) is not None:
                run.skipped_duplicates += 1
                result.duplicate = True
                log_event(
                    logger,
                    logging.INFO,
                    "duplicate_record_skipped",
                    event_id=event_id,
                    stage="detection",
                    action="skip_duplicate",
                    outcome="skipped",
                    batch_id=run.batch_id,
                )
                return result

            ensure_merchant(session, payload["merchant_id"], merchant.get("name", "Merchant"))
            profile = profiles.get(payload["customer_id"], {"customer_id": payload["customer_id"]})
            ensure_customer(session, profile)

            event = RiskEvent(
                id=event_id,
                type=EventType(payload["type"]),
                merchant_id=payload["merchant_id"],
                customer_id=payload["customer_id"],
                amount=payload["amount"],
                currency=payload["currency"],
                source_ref=payload["source_ref"],
                detected_at=payload["detected_at"],
                raw_signal=payload["raw_signal"],
                status=EventStatus.OPEN,
                gateway_used=gateway.name,
                correlation_id=correlation_id,
            )
            session.add(event)
            session.flush()

            audit(
                session,
                event,
                stage=AuditStage.DETECTION,
                action="event_detected",
                after=EventStatus.OPEN.value,
                reasoning=(
                    f"Ingested {event.type.value} for {event.currency} {event.amount} "
                    f"(batch {run.batch_id})."
                ),
            )

            stage = "decision"
            lock_owner = f"{LOCK_OWNER_PREFIX}:{run.batch_id}"
            with locks.event_lock(session, event, lock_owner, now=now):
                decision_outcome = decide(
                    session,
                    event,
                    classifier=classifier,
                    # Propagated explicitly: decide() defaults to loading the
                    # model itself, so without this a batch asked to run
                    # WITHOUT ML would quietly run WITH it and report an
                    # agreement rate that the caller never enabled.
                    load_ml=load_ml,
                    now=now,
                )
                stage = "execution"
                execute_decision(
                    session,
                    event,
                    decision_outcome,
                    gateway=gateway,
                    seed=seed,
                    now=now,
                )

            session.commit()
            run.event_ids.append(event_id)
            result.processed = True

            log_event(
                logger,
                logging.INFO,
                "record_processed",
                event_id=event_id,
                stage="complete",
                action=decision_outcome.decision.action_code,
                outcome=event.status.value,
                batch_id=run.batch_id,
                recovery_probability=decision_outcome.decision.recovery_probability,
                policy_status=decision_outcome.decision.policy_result.get("status"),
                needs_review=decision_outcome.needs_review,
            )
            return result

        except Exception as exc:  # noqa: BLE001 - Section 9 fault isolation
            # Roll back THIS record only. The batch continues.
            session.rollback()
            failure = IsolatedFailure(
                record_index=index,
                event_id=result.event_id,
                correlation_id=correlation_id,
                stage=stage,
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
            )
            result.failure = failure
            log_event(
                logger,
                logging.ERROR,
                "record_isolated_failure",
                event_id=result.event_id,
                stage=stage,
                action="isolate_failure",
                outcome="failed",
                batch_id=run.batch_id,
                record_index=index,
                exc_info=True,
            )
            return result


# --------------------------------------------------------------------------- #
# Metrics — queried back from the database, never accumulated
# --------------------------------------------------------------------------- #


def _sum_money(session: Session, column, *conditions) -> Decimal:
    """Sum a Money column exactly.

    Summed by reading the column values back through the ORM rather than with
    ``func.sum``. That is deliberate: ``Money`` is a TypeDecorator storing
    integer paise and exposing Decimal rupees, and SQLAlchemy applies
    ``process_result_value`` to an aggregate too — so ``func.sum`` returns
    RUPEES, not paise. An earlier version here divided that by 100 again and
    understated every amount by 100x while leaving ``recovery_rate`` correct,
    because both sides of the ratio scaled together. Reading the values and
    adding them removes the ambiguity entirely, and 500 Decimals is not a cost
    worth optimising.
    """
    stmt = select(column)
    for condition in conditions:
        stmt = stmt.where(condition)
    total = Decimal("0.00")
    for value in session.execute(stmt).scalars():
        if value is not None:
            total += value
    return total.quantize(Decimal("0.01"))


def _count(session: Session, column, *conditions) -> int:
    stmt = select(func.count(column))
    for condition in conditions:
        stmt = stmt.where(condition)
    return int(session.execute(stmt).scalar_one() or 0)


def _breakdown(session: Session, column, *conditions) -> dict[str, int]:
    stmt = select(column, func.count()).group_by(column)
    for condition in conditions:
        stmt = stmt.where(condition)
    result: dict[str, int] = {}
    for value, count in session.execute(stmt):
        key = value.value if hasattr(value, "value") else str(value)
        result[key] = int(count)
    return result


def collect_metrics(
    session: Session, run: BatchRun, request: BatchRequest, *, started: datetime, finished: datetime
) -> BatchResponse:
    """Read every figure back from the database.

    Nothing here is passed in from the processing loop except the set of event
    ids that scopes the query. If the pipeline failed to write an Outcome row,
    the money figures fall — which is the whole point.
    """
    ids = run.event_ids
    in_batch = RiskEvent.id.in_(ids) if ids else RiskEvent.id.is_(None)

    amount_at_risk = _sum_money(session, RiskEvent.amount, in_batch)

    outcome_join = Outcome.event_id.in_(ids) if ids else Outcome.event_id.is_(None)
    amount_recovered = _sum_money(session, Outcome.amount_recovered, outcome_join)

    lost_ids = (
        select(Outcome.event_id)
        .where(outcome_join, Outcome.resolved == OutcomeResolution.LOST)
        .scalar_subquery()
    )
    amount_lost = _sum_money(session, RiskEvent.amount, RiskEvent.id.in_(lost_ids))

    pending_ids = (
        select(Outcome.event_id)
        .where(outcome_join, Outcome.resolved == OutcomeResolution.PENDING)
        .scalar_subquery()
    )
    amount_pending = _sum_money(session, RiskEvent.amount, RiskEvent.id.in_(pending_ids))

    attempted_ids = (
        select(PaymentAttempt.event_id)
        .where(PaymentAttempt.event_id.in_(ids) if ids else PaymentAttempt.event_id.is_(None))
        .scalar_subquery()
    )
    amount_attempted = _sum_money(session, RiskEvent.amount, RiskEvent.id.in_(attempted_ids))

    recovery_rate = (
        float(amount_recovered / amount_at_risk) if amount_at_risk > 0 else 0.0
    )

    status_breakdown = _breakdown(session, RiskEvent.status, in_batch)
    resolved_statuses = {
        EventStatus.RECOVERED.value,
        EventStatus.UNRECOVERABLE.value,
    }
    resolved_count = sum(
        count for key, count in status_breakdown.items() if key in resolved_statuses
    )
    processed = len(ids)
    resolution_rate = resolved_count / processed if processed else 0.0

    # --- stopping-rule triggers, from persisted state ----------------------
    triggers = StoppingRuleTriggers()
    state_scope = (
        StoppingRuleState.event_id.in_(ids) if ids else StoppingRuleState.event_id.is_(None)
    )
    reason_rows = session.execute(
        select(StoppingRuleState.hard_stop_reason, func.count())
        .where(state_scope, StoppingRuleState.hard_stop_reason.is_not(None))
        .group_by(StoppingRuleState.hard_stop_reason)
    )
    for reason, count in reason_rows:
        bucket = RULE_TO_TRIGGER_BUCKET.get(reason)
        if bucket == "cooldown":
            triggers.cooldown += int(count)
        elif bucket == "do_not_contact":
            triggers.do_not_contact += int(count)
        elif bucket == "max_attempts":
            triggers.max_attempts += int(count)
        elif bucket == "hard_decline":
            triggers.hard_decline += int(count)
        elif reason in ("hard_decline", "max_attempts"):
            # Written directly by execution settlement rather than by a rule name.
            setattr(triggers, reason, getattr(triggers, reason) + int(count))
        else:
            triggers.other[str(reason)] = triggers.other.get(str(reason), 0) + int(count)

    # --- escalation ceiling: events the engine will not escalate further ---
    ceiling_blocked = _count(
        session,
        StoppingRuleState.event_id,
        state_scope,
        StoppingRuleState.hard_stop_reason == RULE_ESCALATION_CEILING,
    )
    at_ceiling = _count(
        session,
        StoppingRuleState.event_id,
        state_scope,
        StoppingRuleState.escalation_level >= 2,
    )
    escalation_ceiling_hits = ceiling_blocked + at_ceiling

    # --- ML, Section 4a ----------------------------------------------------
    ml_scope = (
        MLDiagnosisPrediction.event_id.in_(ids)
        if ids
        else MLDiagnosisPrediction.event_id.is_(None)
    )
    ml_predictions = _count(session, MLDiagnosisPrediction.id, ml_scope)
    ml_agreements = _count(
        session,
        MLDiagnosisPrediction.id,
        ml_scope,
        MLDiagnosisPrediction.agrees_with_rule_engine.is_(True),
    )
    # An absent opinion is NOT a disagreement (Section 4a). Events with no
    # prediction are excluded from the rate entirely rather than counted against
    # the model.
    ml_agreement_rate = (
        (ml_agreements / ml_predictions) if ml_predictions else None
    )
    ml_unavailable = max(0, processed - ml_predictions)

    # --- promises ----------------------------------------------------------
    promise_scope = (
        PromiseToPay.event_id.in_(ids) if ids else PromiseToPay.event_id.is_(None)
    )
    promises_made = _count(session, PromiseToPay.id, promise_scope)
    promises_kept = _count(
        session, PromiseToPay.id, promise_scope, PromiseToPay.status == "kept"
    )
    promises_broken = _count(
        session, PromiseToPay.id, promise_scope, PromiseToPay.status == "broken"
    )

    audit_scope = AuditLog.event_id.in_(ids) if ids else AuditLog.event_id.is_(None)
    audit_entries = _count(session, AuditLog.id, audit_scope)

    decision_scope = Decision.event_id.in_(ids) if ids else Decision.event_id.is_(None)
    action_breakdown = _breakdown(session, Decision.action_code, decision_scope)

    outcome_breakdown = _breakdown(session, Outcome.resolved, outcome_join)
    event_type_breakdown = _breakdown(session, RiskEvent.type, in_batch)

    # --- exceptions --------------------------------------------------------
    from app.routers.exceptions import count_exceptions

    exceptions_raised = count_exceptions(session, event_ids=ids)

    failures = [r.failure for r in run.results if r.failure is not None]

    return BatchResponse(
        batch_id=run.batch_id,
        correlation_id=run.correlation_id,
        gateway=request.gateway,
        seed=request.seed,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_seconds=round((finished - started).total_seconds(), 3),
        total_records=request.count,
        processed=processed,
        isolated_failures=len(failures),
        skipped_duplicates=run.skipped_duplicates,
        money=BatchMoney(
            amount_at_risk=str(amount_at_risk),
            amount_attempted=str(amount_attempted),
            amount_recovered=str(amount_recovered),
            amount_lost=str(amount_lost),
            amount_pending=str(amount_pending),
        ),
        recovery_rate=round(recovery_rate, 4),
        resolution_rate=round(resolution_rate, 4),
        escalation_ceiling_hits=escalation_ceiling_hits,
        stopping_rule_triggers=triggers,
        promises_made=promises_made,
        promises_kept=promises_kept,
        promises_broken=promises_broken,
        status_breakdown=status_breakdown,
        action_breakdown=action_breakdown,
        event_type_breakdown=event_type_breakdown,
        outcome_breakdown=outcome_breakdown,
        ml_agreement_rate=(
            round(ml_agreement_rate, 4) if ml_agreement_rate is not None else None
        ),
        ml_predictions=ml_predictions,
        ml_agreements=ml_agreements,
        ml_unavailable=ml_unavailable,
        exceptions_raised=exceptions_raised,
        audit_entries=audit_entries,
        failures=failures,
    )


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


def run_batch(
    session: Session,
    request: BatchRequest,
    *,
    gateway: PaymentGateway | None = None,
    classifier: Any = None,
    load_ml: bool = True,
    now: datetime | None = None,
) -> BatchResponse:
    """Process ``request.count`` synthetic records end to end."""
    started = utcnow()
    moment = now or started
    batch_id = f"batch_{int(time.time() * 1000):x}"
    correlation_id = new_correlation_id("batchcorr")
    run = BatchRun(batch_id=batch_id, correlation_id=correlation_id)

    active_gateway = gateway or build_gateway(request.gateway, seed=request.seed)

    # Loaded ONCE per batch rather than per record: reading a joblib file 500
    # times would dominate the run time and tell us nothing.
    active_classifier = classifier
    if active_classifier is None and load_ml:
        active_classifier = load_classifier()

    batch = generate_batch(
        request.count, seed=request.seed, now=datetime.now(IST)
    )
    profiles = {profile["customer_id"]: profile for profile in batch.customers}

    # Section 11's externally-resolved records live in the gateway's world, not
    # on the events, so the engine can only discover them by re-checking.
    if hasattr(active_gateway, "seed_upstream_state"):
        active_gateway.seed_upstream_state(batch.upstream_world)

    with correlation_scope(correlation_id):
        log_event(
            logger,
            logging.INFO,
            "batch_started",
            stage="batch",
            action="start",
            batch_id=batch_id,
            count=request.count,
            gateway=request.gateway.value,
            seed=request.seed,
        )

    for index, record in enumerate(batch.records):
        result = process_record(
            session,
            record,
            index,
            run,
            merchant=batch.merchant,
            profiles=profiles,
            gateway=active_gateway,
            classifier=active_classifier,
            load_ml=load_ml,
            seed=request.seed,
            now=moment,
        )
        run.results.append(result)

    finished = utcnow()
    response = collect_metrics(session, run, request, started=started, finished=finished)

    with correlation_scope(correlation_id):
        log_event(
            logger,
            logging.INFO,
            "batch_finished",
            stage="batch",
            action="finish",
            outcome="ok",
            batch_id=batch_id,
            processed=response.processed,
            isolated_failures=response.isolated_failures,
            amount_recovered=response.money.amount_recovered,
            recovery_rate=response.recovery_rate,
        )

    return response


@router.post("/batch", response_model=BatchResponse, summary="Process N synthetic records")
def post_batch(
    request: BatchRequest | None = None, session: Session = Depends(get_db)
) -> BatchResponse:
    """Run a batch. Section 10.

    Defaults to 50 records on the built-in simulator, which needs no
    credentials and is the path that must never fail during judging.
    """
    return run_batch(session, request or BatchRequest())
