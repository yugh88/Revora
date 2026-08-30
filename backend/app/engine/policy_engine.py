"""Merchant-configurable policy gate. BUILD_SPEC Sections 4 and 6.

Policy is AUTHORITATIVE for whether a selected action may be taken. The
probability engine can rank an action first and the policy engine can still
refuse it; nothing downstream may override that refusal.

The structured result
---------------------
Section 4 fixes the shape of ``Decision.policy_result`` exactly:

    {status, rule_triggered, threshold_checked, actual_value, threshold_value}

Those five keys and no others. The point is that "why we didn't act" renders on
/exceptions as a concrete comparison — "attempts_used 2 >= max_attempts 2" —
rather than as a bare boolean. Adding keys would break the contract the
frontend and Section 4 both depend on, so :meth:`PolicyResult.as_dict` emits
exactly five.

Rule order matters
------------------
Checks run cheapest-and-most-absolute first. A do-not-contact customer is never
messaged regardless of how much money is at stake, so that rule is evaluated
before anything involving amounts or probabilities. The FIRST rule to trigger is
the one reported: it is the binding constraint, and reporting a later one would
misdescribe why the action was refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.engine.diagnosis_engine import (
    CHANNEL_BY_ACTION,
    ActionCode,
    DiagnosisResult,
    escalation_level_for,
)
from app.enums import EventType, PolicyResultStatus
from app.models.customer_profile import CustomerProfile
from app.models.decision import Decision
from app.models.policy import Policy
from app.models.risk_event import RiskEvent
from app.models.stopping_rule_state import StoppingRuleState

# --------------------------------------------------------------------------- #
# Rule identifiers — stable strings, reported in policy_result.rule_triggered
# and aggregated by /batch as stopping_rule_triggers (Section 10).
# --------------------------------------------------------------------------- #

RULE_DO_NOT_CONTACT = "do_not_contact"
RULE_HARD_STOP = "hard_stop_cause"
RULE_COOLDOWN = "cooldown_active"
RULE_OPEN_PROMISE = "customer_promised_to_pay"
RULE_MAX_ATTEMPTS = "max_attempts_reached"
RULE_CONTACT_LIMIT = "contact_limit_per_channel"
RULE_ESCALATION_CEILING = "escalation_ceiling"
RULE_AMOUNT_THRESHOLD = "amount_threshold_requires_human"
RULE_PROBABILITY_THRESHOLD = "recovery_probability_below_threshold"
RULE_NO_ELIGIBLE_ACTION = "no_action_permitted_by_intervention_table"

#: Policy used when a merchant has no row for this event type yet. Version 0
#: marks it as a synthesised default rather than something a merchant chose;
#: /policies (session 8) creates real version-1 rows. It is never persisted.
DEFAULT_POLICY_VERSION = 0
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_COOLDOWN_HOURS = 24
DEFAULT_AMOUNT_THRESHOLD = Decimal("25000.00")
DEFAULT_PROBABILITY_THRESHOLD = 0.05
DEFAULT_CONTACT_LIMIT = 2
DEFAULT_ESCALATION_CEILING = 2


@dataclass(frozen=True)
class PolicyResult:
    """Outcome of the gate, in the exact shape Section 4 specifies."""

    status: PolicyResultStatus
    rule_triggered: str | None = None
    threshold_checked: str | None = None
    actual_value: Any = None
    threshold_value: Any = None

    @property
    def allowed(self) -> bool:
        return self.status == PolicyResultStatus.ALLOWED

    def as_dict(self) -> dict[str, Any]:
        """Exactly the five keys Section 4 defines. No more."""
        return {
            "status": self.status.value,
            "rule_triggered": self.rule_triggered,
            "threshold_checked": self.threshold_checked,
            "actual_value": self.actual_value,
            "threshold_value": self.threshold_value,
        }


def _allow() -> PolicyResult:
    return PolicyResult(status=PolicyResultStatus.ALLOWED)


def _block(rule: str, checked: str, actual: Any, threshold: Any) -> PolicyResult:
    return PolicyResult(
        status=PolicyResultStatus.BLOCKED,
        rule_triggered=rule,
        threshold_checked=checked,
        actual_value=actual,
        threshold_value=threshold,
    )


# --------------------------------------------------------------------------- #
# Policy resolution
# --------------------------------------------------------------------------- #


def resolve_policy(session: Session, event: RiskEvent) -> Policy:
    """Current policy for this merchant and event type.

    "Current" means the highest ``policy_version`` for the pair — policies are
    versioned, never mutated, so decisions stay pinned to the version that gated
    them (see models/policy.py).

    When no row exists a transient default is returned, NOT added to the
    session. Persisting a policy the merchant never configured would silently
    invent configuration.
    """
    event_type = event.type if isinstance(event.type, EventType) else EventType(event.type)
    row = session.execute(
        select(Policy)
        .where(Policy.merchant_id == event.merchant_id, Policy.event_type == event_type)
        .order_by(Policy.policy_version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is not None:
        return row

    return Policy(
        policy_version=DEFAULT_POLICY_VERSION,
        merchant_id=event.merchant_id,
        event_type=event_type,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        cooldown_hours=DEFAULT_COOLDOWN_HOURS,
        amount_threshold=DEFAULT_AMOUNT_THRESHOLD,
        recovery_probability_threshold=DEFAULT_PROBABILITY_THRESHOLD,
        contact_limit_per_channel=DEFAULT_CONTACT_LIMIT,
        escalation_ceiling=DEFAULT_ESCALATION_CEILING,
    )


def _channel_contact_count(session: Session, event_id: str, channel: str) -> int:
    """How many prior decisions for this event used the same channel.

    Policy.contact_limit_per_channel is a frequency cap. Until session 8 records
    channel on each contact, prior Decision rows are the record of what was
    sent, so the count is derived from them by mapping action -> channel.
    """
    if channel in ("none", "human_handoff"):
        return 0
    same_channel = {
        action.value for action, ch in CHANNEL_BY_ACTION.items() if ch == channel
    }
    if not same_channel:
        return 0
    rows = session.execute(
        select(Decision.action_code).where(Decision.event_id == event_id)
    ).scalars()
    return sum(1 for action_code in rows if action_code in same_channel)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def _has_open_promise(session: Session, event: RiskEvent, moment: datetime) -> bool:
    """Is this case waiting on a commitment the customer has not yet missed?

    Imported lazily: the promise tracker reaches back into the batch router for
    the ledger writer, and importing it at module scope would close a cycle.
    """
    from app.engine.promise_tracker import has_open_promise

    return has_open_promise(session, event.id, now=moment)


def evaluate(
    session: Session,
    event: RiskEvent,
    action: ActionCode,
    *,
    policy: Policy,
    diagnosis: DiagnosisResult,
    probability: float,
    attempt_number: int,
    stopping_state: StoppingRuleState | None = None,
    customer: CustomerProfile | None = None,
    now: datetime | None = None,
) -> PolicyResult:
    """Decide whether ``action`` may be taken on ``event``.

    Rules are evaluated most-absolute first; the first to trigger is reported as
    the binding constraint.
    """
    moment = now or utcnow()

    # --- 1. do-not-contact: absolute, regardless of amount or probability ---
    profile = customer or session.get(CustomerProfile, event.customer_id)
    channel = CHANNEL_BY_ACTION.get(action, "none")
    contacts_customer = channel not in ("none",)
    if profile is not None and profile.do_not_contact and contacts_customer:
        return _block(RULE_DO_NOT_CONTACT, "customer.do_not_contact", True, False)

    # --- 2. an open promise: the customer has told us when they will pay ---
    #
    # Chasing someone before the date they committed to is precisely how a
    # recovery agent becomes a nuisance, and it wastes the goodwill that made
    # them answer in the first place. Only CONTACT is paused: a gateway retry
    # costs the customer nothing and may well succeed on its own, so a promise
    # does not stop Revora quietly checking whether the money has arrived.
    #
    # The pause lifts by itself the moment the promised date passes, because
    # has_open_promise reads the date rather than a stored flag — a broken
    # promise resumes recovery with no sweep required.
    if contacts_customer and _has_open_promise(session, event, moment):
        return _block(
            RULE_OPEN_PROMISE,
            "promise.promised_date",
            "in the future",
            "must have passed before contacting again",
        )

    # --- 3. hard-stop causes: Section 6, "no retry, immediate stop" ---------
    if diagnosis.is_hard_stop and action != ActionCode.NO_ACTION:
        return _block(
            RULE_HARD_STOP,
            "diagnosis.root_cause",
            diagnosis.root_cause.value,
            "must not be a hard-stop cause",
        )

    state = stopping_state or session.get(StoppingRuleState, event.id)

    # --- 3. cooldown -------------------------------------------------------
    if state is not None and state.is_in_cooldown(moment) and contacts_customer:
        return _block(
            RULE_COOLDOWN,
            "stopping_rule_state.cooldown_until",
            moment.isoformat(),
            state.cooldown_until.isoformat() if state.cooldown_until else None,
        )

    # --- 4. attempt cap ----------------------------------------------------
    attempts_used = state.attempts_used if state is not None else attempt_number - 1
    if attempts_used >= policy.max_attempts:
        return _block(
            RULE_MAX_ATTEMPTS,
            "policy.max_attempts",
            attempts_used,
            policy.max_attempts,
        )

    # --- 5. per-channel frequency cap (Section 7 rule 3) -------------------
    if contacts_customer:
        used = _channel_contact_count(session, event.id, channel)
        if used >= policy.contact_limit_per_channel:
            return _block(
                RULE_CONTACT_LIMIT,
                f"policy.contact_limit_per_channel[{channel}]",
                used,
                policy.contact_limit_per_channel,
            )

    # --- 6. escalation ceiling: Section 6, never past L2 --------------------
    target_level = escalation_level_for(action)
    if target_level > policy.escalation_ceiling:
        return _block(
            RULE_ESCALATION_CEILING,
            "policy.escalation_ceiling",
            target_level,
            policy.escalation_ceiling,
        )

    # --- 7. amount threshold: large balances go to a human -----------------
    # Section 6: "Human handoff if amount > threshold". Not a refusal to act —
    # a refusal to act AUTOMATICALLY. The decision engine responds by selecting
    # the human handoff instead.
    if (
        event.amount is not None
        and event.amount > policy.amount_threshold
        and action != ActionCode.HUMAN_HANDOFF
        and contacts_customer
    ):
        return _block(
            RULE_AMOUNT_THRESHOLD,
            "policy.amount_threshold",
            str(event.amount),
            str(policy.amount_threshold),
        )

    # --- 8. probability floor ----------------------------------------------
    if probability < policy.recovery_probability_threshold:
        return _block(
            RULE_PROBABILITY_THRESHOLD,
            "policy.recovery_probability_threshold",
            round(probability, 4),
            policy.recovery_probability_threshold,
        )

    return _allow()


def blocked_because_no_action_permitted(diagnosis: DiagnosisResult) -> PolicyResult:
    """Result used when Section 6's table permits nothing at all.

    Distinct from a policy refusal: the intervention table itself has run out of
    moves (attempts exhausted, or a hard cause). Reported so /exceptions can
    tell "policy said no" apart from "there was nothing to say yes to".
    """
    if diagnosis.is_hard_stop:
        return _block(
            RULE_HARD_STOP,
            "diagnosis.root_cause",
            diagnosis.root_cause.value,
            "must not be a hard-stop cause",
        )
    return _block(
        RULE_NO_ELIGIBLE_ACTION,
        "section_6_intervention_table",
        0,
        "at least 1 eligible action",
    )
