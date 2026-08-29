"""Decision engine — orchestrates the pipeline into a Decision.
BUILD_SPEC Sections 4, 4a and 6.

    diagnosis -> ML independent check -> probability -> policy -> Decision

Authority, stated once and enforced throughout
-----------------------------------------------
The RULE ENGINE decides the root cause used for the action. The ML classifier
runs on every event and its opinion is recorded, but it never changes
``action_code``, never changes the probability, and never changes the policy
outcome. Section 4a is unambiguous that rules stay authoritative "for the action
actually taken (safety/auditability)", and the ML layer exists to surface
disagreement for review rather than to silently override or be ignored.

Concretely, the only things an ML prediction can do are:
  * write an MLDiagnosisPrediction row,
  * set ``agrees_with_rule_engine``,
  * append an audit entry when it disagrees or is unconfident.

Non-blocking, structurally
--------------------------
The ML call is wrapped so that a missing model file, a corrupt bundle or an
exception inside ``predict()`` all produce ``None`` and a log line. The
deterministic path below runs identically either way. There is no branch in
which an ML failure prevents a Decision from being produced.

reasoning_text
--------------
Populated here with a plain, deterministic sentence assembled from
``decision_factors``. It is NOT LLM-generated and never will be. Section 7's
template engine (session 8) replaces this with YAML-template rendering; the
field is filled now because ``Decision.reasoning_text`` is non-nullable and a
Decision with an empty justification would be a worse placeholder than a factual
one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.engine import idempotency, policy_engine, probability_engine
from app.engine.diagnosis_engine import (
    CHANNEL_BY_ACTION,
    LOW_CONFIDENCE_THRESHOLD,
    ActionCode,
    DiagnosisResult,
    candidate_actions,
    classify,
    diagnose,
)
from app.engine.policy_engine import PolicyResult
from app.engine.probability_engine import ActionScore
from app.engine.state_machine import transition
from app.enums import (
    AuditActor,
    AuditStage,
    EventStatus,
    EventType,
    PolicyResultStatus,
    ProbabilitySource,
    RootCauseCode,
)
from app.ml.diagnosis_classifier import (
    ML_CONFIDENCE_THRESHOLD,
    DiagnosisClassifier,
    MLPrediction,
    extract_features,
    load_classifier,
    predict,
)
from app.models.audit_log import AuditLog
from app.enums import PromiseStatus
from app.models.customer_profile import CustomerProfile
from app.models.promise_to_pay import PromiseToPay
from app.models.risk_event import RiskEvent as _RiskEvent
from app.models.decision import Decision
from app.models.diagnosis import Diagnosis, MLDiagnosisPrediction
from app.models.risk_event import RiskEvent
from app.models.stopping_rule_state import StoppingRuleState

logger = logging.getLogger(__name__)

#: The exact phrase Section 4a specifies for the /exceptions queue.
ML_DISAGREEMENT_REASON = "ML/rule disagreement — needs review"

ACTION_ML_DISAGREEMENT = "ml_rule_disagreement"
ACTION_ML_UNAVAILABLE = "ml_unavailable"
ACTION_DECISION_MADE = "decision_made"


@dataclass(frozen=True)
class DecisionOutcome:
    """Everything one pass of the pipeline produced.

    Returned alongside the persisted Decision so callers (session 4's /batch)
    can aggregate without re-querying: ml_agreement_rate, stopping-rule trigger
    counts and the exceptions queue are all built from these fields.
    """

    decision: Decision
    diagnosis: Diagnosis
    diagnosis_result: DiagnosisResult
    chosen: ActionScore | None
    policy_result: PolicyResult
    ml_prediction: MLPrediction | None
    ml_agrees: bool | None
    needs_review: bool
    review_reasons: list[str]
    ranked: list[ActionScore]


def _audit(
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


# --------------------------------------------------------------------------- #
# ML independent check — Section 4a
# --------------------------------------------------------------------------- #


def run_ml_check(
    session: Session,
    event: RiskEvent,
    rule_cause: RootCauseCode,
    *,
    classifier: DiagnosisClassifier | None,
    customer: CustomerProfile | None,
    attempt_number: int,
    now: datetime,
) -> tuple[MLPrediction | None, bool | None]:
    """Run the classifier beside the rule engine and record what it said.

    Returns ``(prediction, agrees)``. Both are None when no ML opinion could be
    obtained — a normal state, not an error.

    Nothing in here influences the action taken.
    """
    if classifier is None:
        return None, None

    try:
        raw = event.raw_signal if isinstance(event.raw_signal, dict) else {}
        success_rate = customer.payment_success_rate if customer is not None else 0.0
        features = extract_features(
            amount=event.amount,
            attempt_number=attempt_number,
            detected_at=event.detected_at,
            gateway_error_code=raw.get("gateway_error_code"),
            customer_success_rate=success_rate,
            event_type=event.type,
            now=now,
        )
    except Exception:  # noqa: BLE001 - feature extraction must never block
        logger.exception(
            "ml_feature_extraction_failed event_id=%s: continuing on rules alone", event.id
        )
        return None, None

    prediction = predict(classifier, features)
    if prediction is None:
        return None, None

    # Agreement requires BOTH the same cause AND sufficient confidence.
    # A lucky match at 12% confidence is not corroboration.
    agrees = prediction.root_cause == rule_cause and prediction.is_confident

    session.add(
        MLDiagnosisPrediction(
            event_id=event.id,
            predicted_root_cause=prediction.root_cause,
            confidence=prediction.confidence,
            agrees_with_rule_engine=agrees,
            model_version=prediction.model_version,
            predicted_at=now,
        )
    )
    session.flush()
    return prediction, agrees


# --------------------------------------------------------------------------- #
# reasoning_text — deterministic, no LLM
# --------------------------------------------------------------------------- #


def build_reasoning_text(
    event: RiskEvent,
    diagnosis: DiagnosisResult,
    chosen: ActionScore | None,
    policy_result: PolicyResult,
) -> str:
    """Assemble a factual justification from the decision factors.

    Plain string formatting over values already computed. No model, no
    generation, no external call. Session 8's template_engine replaces this.
    """
    cause = diagnosis.root_cause.value
    confidence = f"{diagnosis.confidence:.0%}"

    if chosen is None or not policy_result.allowed:
        rule = policy_result.rule_triggered or "no eligible action"
        return (
            f"No action taken on {event.id}. Diagnosed {cause} "
            f"(confidence {confidence}). Policy gate blocked every candidate: "
            f"{rule} ({policy_result.threshold_checked} "
            f"actual={policy_result.actual_value} "
            f"threshold={policy_result.threshold_value})."
        )

    return (
        f"Diagnosed {cause} (confidence {confidence}) on a "
        f"{event.currency} {event.amount} {event.type.value} event. "
        f"Selected {chosen.action.value} at attempt {chosen.attempt_number}: "
        f"expected recovery {chosen.probability:.0%} x {event.currency} "
        f"{chosen.amount_at_risk} = {chosen.expected_value}, "
        f"less cost {chosen.cost} and annoyance penalty "
        f"{chosen.annoyance_penalty}, for a net score of {chosen.score}. "
        f"Policy allowed the action."
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def decide(
    session: Session,
    event: RiskEvent,
    *,
    classifier: DiagnosisClassifier | None = None,
    load_ml: bool = True,
    now: datetime | None = None,
    advance_state: bool = True,
) -> DecisionOutcome:
    """Run the full pipeline for one event and persist a Decision.

    Args:
        session: Active session. This function does NOT commit — the caller owns
            the transaction, which is what gives session 4's /batch its
            per-record fault-isolation boundary.
        event: The event to decide on.
        classifier: A preloaded classifier. Pass one when processing a batch so
            the model is read from disk once rather than per event.
        load_ml: When True and no classifier is given, attempt to load one.
            Pass False to run the pipeline deterministically with no ML at all.
        now: Reference instant; defaults to the current UTC time.
        advance_state: Move an OPEN event to DIAGNOSING. Later transitions
            (intervening / recovered / stopped) belong to execution, not here.

    Returns:
        A :class:`DecisionOutcome`.
    """
    moment = now or utcnow()
    event_type = event.type if isinstance(event.type, EventType) else EventType(event.type)

    if advance_state and event.status == EventStatus.OPEN:
        transition(
            session,
            event,
            EventStatus.DIAGNOSING,
            reasoning="Decision pipeline started: classifying root cause.",
        )

    # --- 1. rule-based diagnosis: AUTHORITATIVE ---------------------------
    diagnosis_row = diagnose(session, event, now=moment)
    diagnosis_result = DiagnosisResult(
        root_cause=diagnosis_row.root_cause_code,
        confidence=diagnosis_row.confidence,
        evidence=list(diagnosis_row.evidence or []),
    )

    customer = session.get(CustomerProfile, event.customer_id)

    # Promise history for this customer, across every case they have had. A
    # payer who keeps their word is a different proposition from one who does
    # not, and the ledger already knows which is which.
    promises_kept = 0
    promises_broken = 0
    try:
        rows = session.execute(
            select(PromiseToPay.status)
            .join(_RiskEvent, _RiskEvent.id == PromiseToPay.event_id)
            .where(_RiskEvent.customer_id == event.customer_id)
        ).scalars()
        for status in rows:
            if status == PromiseStatus.KEPT:
                promises_kept += 1
            elif status == PromiseStatus.BROKEN:
                promises_broken += 1
    except Exception:  # noqa: BLE001
        logger.exception(
            "promise_history_unavailable",
            extra={"event_id": event.id, "stage": "decision"},
        )
    attempt_number = idempotency.next_attempt_number(session, event.id)

    # --- 2. ML independent check: never authoritative ----------------------
    active_classifier = classifier
    if active_classifier is None and load_ml:
        active_classifier = load_classifier()
    if active_classifier is None and load_ml:
        _audit(
            session,
            event,
            stage=AuditStage.DIAGNOSIS,
            action=ACTION_ML_UNAVAILABLE,
            reasoning=(
                "Classifier unavailable; proceeding on deterministic diagnosis "
                "alone. ML is an enhancement layer, not a dependency."
            ),
        )

    ml_prediction, ml_agrees = run_ml_check(
        session,
        event,
        diagnosis_result.root_cause,
        classifier=active_classifier,
        customer=customer,
        attempt_number=attempt_number,
        now=moment,
    )

    review_reasons: list[str] = []
    if diagnosis_result.is_low_confidence:
        review_reasons.append(
            f"Rule diagnosis confidence {diagnosis_result.confidence:.2f} is below "
            f"the {LOW_CONFIDENCE_THRESHOLD:.2f} threshold."
        )
    if ml_prediction is not None and not ml_agrees:
        if ml_prediction.root_cause != diagnosis_result.root_cause:
            detail = (
                f"rules say {diagnosis_result.root_cause.value}, "
                f"model says {ml_prediction.root_cause.value} "
                f"at {ml_prediction.confidence:.2f} confidence"
            )
        else:
            detail = (
                f"model agrees on {ml_prediction.root_cause.value} but only at "
                f"{ml_prediction.confidence:.2f}, below the "
                f"{ML_CONFIDENCE_THRESHOLD:.2f} threshold"
            )
        review_reasons.append(f"{ML_DISAGREEMENT_REASON}: {detail}")
        _audit(
            session,
            event,
            stage=AuditStage.DIAGNOSIS,
            action=ACTION_ML_DISAGREEMENT,
            before=diagnosis_result.root_cause.value,
            after=ml_prediction.root_cause.value,
            reasoning=(
                f"{ML_DISAGREEMENT_REASON}. {detail}. The rule-based diagnosis "
                "remains authoritative for the action taken; this entry exists "
                "so the disagreement is reviewed rather than ignored."
            ),
        )

    # --- 3. candidate actions from the Section 6 table --------------------
    # Policy is resolved FIRST because the table's "Human handoff if amount >
    # threshold" condition needs the merchant's threshold to decide what is even
    # eligible. Resolving it afterwards would offer a handoff on every event.
    policy = policy_engine.resolve_policy(session, event)
    stopping_state = session.get(StoppingRuleState, event.id)

    candidates = candidate_actions(
        event, diagnosis_result, attempt_number, policy.amount_threshold
    )
    amount_at_risk = event.amount if event.amount is not None else Decimal("0.00")

    # --- 4. score, then 5. walk the ranking through the policy gate -------
    # What is known about THIS payer tilts the scoring. Extending the existing
    # probability rather than adding a parallel "payment prediction" the engine
    # would then ignore — a score nothing acts on is decoration.
    customer_multiplier, likelihood_reason = probability_engine.customer_likelihood(
        payment_success_rate=(
            customer.payment_success_rate if customer is not None else None
        ),
        avg_payment_delay_days=(
            customer.avg_payment_delay_days if customer is not None else None
        ),
        promises_kept=promises_kept,
        promises_broken=promises_broken,
    )

    ranked = probability_engine.rank_actions(
        diagnosis_result.root_cause,
        candidates,
        attempt_number,
        amount_at_risk,
        customer_multiplier=customer_multiplier,
    )

    chosen: ActionScore | None = None
    policy_result: PolicyResult | None = None

    for candidate in ranked:
        result = policy_engine.evaluate(
            session,
            event,
            candidate.action,
            policy=policy,
            diagnosis=diagnosis_result,
            probability=candidate.probability,
            attempt_number=attempt_number,
            stopping_state=stopping_state,
            customer=customer,
            now=moment,
        )
        if result.allowed:
            chosen = candidate
            policy_result = result
            break
        if policy_result is None:
            # Remember the FIRST refusal: it is the one against the
            # highest-scoring action, and therefore the binding constraint.
            policy_result = result

    if policy_result is None:
        policy_result = policy_engine.blocked_because_no_action_permitted(diagnosis_result)

    if chosen is None:
        review_reasons.append(
            f"No action permitted: {policy_result.rule_triggered}."
        )

    action_code = chosen.action if chosen is not None else ActionCode.NO_ACTION

    # --- 6. assemble decision_factors --------------------------------------
    raw = event.raw_signal if isinstance(event.raw_signal, dict) else {}
    decision_factors: dict[str, Any] = {
        "root_cause": diagnosis_result.root_cause.value,
        "confidence": round(diagnosis_result.confidence, 4),
        "evidence": list(diagnosis_result.evidence),
        "amount": str(amount_at_risk),
        "currency": event.currency,
        "event_type": event_type.value,
        "attempt_number": attempt_number,
        "payment_likelihood_multiplier": round(customer_multiplier, 4),
        "payment_likelihood_reason": likelihood_reason,
        "promises_kept": promises_kept,
        "promises_broken": promises_broken,
        "customer_success_rate": (
            round(customer.payment_success_rate, 4) if customer is not None else None
        ),
        "channel_preference": (
            customer.preferred_channel.value if customer is not None else None
        ),
        "channel_used": CHANNEL_BY_ACTION.get(action_code, "none"),
        "days_overdue": raw.get("days_overdue"),
        "is_b2b": event.is_b2b,
        "gateway_error_code": raw.get("gateway_error_code"),
        "candidates_considered": [score.as_factors() for score in ranked],
        "selected": chosen.as_factors() if chosen is not None else None,
        "ml": {
            "available": ml_prediction is not None,
            "predicted_root_cause": (
                ml_prediction.root_cause.value if ml_prediction is not None else None
            ),
            "confidence": (
                round(ml_prediction.confidence, 4) if ml_prediction is not None else None
            ),
            "model_version": (
                ml_prediction.model_version if ml_prediction is not None else None
            ),
            "agrees_with_rule_engine": ml_agrees,
            "authoritative": False,
        },
        "needs_review": bool(review_reasons),
        "review_reasons": list(review_reasons),
    }

    recovery_probability = chosen.probability if chosen is not None else 0.0

    decision = Decision(
        event_id=event.id,
        decision_factors=decision_factors,
        recovery_probability=recovery_probability,
        probability_source=ProbabilitySource.DETERMINISTIC,
        policy_result=policy_result.as_dict(),
        policy_version=policy.policy_version,
        action_code=action_code.value,
        reasoning_text=build_reasoning_text(
            event, diagnosis_result, chosen, policy_result
        ),
        decided_at=moment,
    )
    session.add(decision)
    session.flush()

    _audit(
        session,
        event,
        stage=AuditStage.DECISION,
        action=ACTION_DECISION_MADE,
        before=diagnosis_result.root_cause.value,
        after=action_code.value,
        reasoning=decision.reasoning_text,
    )

    return DecisionOutcome(
        decision=decision,
        diagnosis=diagnosis_row,
        diagnosis_result=diagnosis_result,
        chosen=chosen,
        policy_result=policy_result,
        ml_prediction=ml_prediction,
        ml_agrees=ml_agrees,
        needs_review=bool(review_reasons),
        review_reasons=review_reasons,
        ranked=ranked,
    )
