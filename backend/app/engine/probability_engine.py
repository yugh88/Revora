"""Deterministic Recovery Probability Engine (P0). BUILD_SPEC Section 6.

Section 6 fixes the formula:

    score(action) = P(recovery | root_cause, action, attempt_number) x amount_at_risk
                    - cost(action)
                    - annoyance_penalty(attempt_number)

and requires that ``P(...)`` "starts as a hand-set lookup table you define
yourself (documented in `probability_engine.py`)". That table is below, with the
reasoning for each number, because a scoring engine whose constants are
unexplained is not auditable.

P1 is deliberately NOT implemented here. Section 6 calls the logistic-regression
swap-in a stretch, and ``Decision.probability_source`` already carries the
``deterministic | ml_p1`` distinction, so P1 can be added later without touching
callers. Everything this engine produces is ``ProbabilitySource.DETERMINISTIC``.

Units
-----
Every monetary quantity is INR and exact: ``Decimal``, never float. ``score`` is
a net expected rupee value and CAN be negative — that is the engine correctly
saying an action costs more than it is expected to recover. Probabilities are
plain floats in [0, 1].
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.engine.diagnosis_engine import ActionCode
from app.enums import ProbabilitySource, RootCauseCode

# --------------------------------------------------------------------------- #
# P(recovery | root_cause, action, attempt_number)
# --------------------------------------------------------------------------- #
#
# Hand-set, documented, and deliberately conservative. The shape of the table
# matters more than any individual number: causes the customer can fix when
# prompted (an expired card, an unauthenticated mandate) respond well to the
# right nudge; causes rooted in the customer having no money respond poorly to
# any message and better to timing; causes that are a flat refusal from the
# bank respond to nothing.
#
# Read as: given this root cause, how often does THIS action recover the money
# on a FIRST attempt?

BASE_RECOVERY_PROBABILITY: dict[tuple[RootCauseCode, ActionCode], float] = {
    # --- card_expired: the customer must update a card. Email carries a link
    #     and converts well; an SMS nudge is weaker but non-zero.
    (RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL): 0.55,
    (RootCauseCode.CARD_EXPIRED, ActionCode.SMS_REMINDER): 0.30,
    (RootCauseCode.CARD_EXPIRED, ActionCode.HUMAN_HANDOFF): 0.60,
    # --- insufficient_funds: nothing persuades an empty account. Timing does.
    (RootCauseCode.INSUFFICIENT_FUNDS, ActionCode.UPDATE_CARD_EMAIL): 0.12,
    (RootCauseCode.INSUFFICIENT_FUNDS, ActionCode.SMS_REMINDER): 0.22,
    (RootCauseCode.INSUFFICIENT_FUNDS, ActionCode.RETRY_PAYMENT): 0.25,
    (RootCauseCode.INSUFFICIENT_FUNDS, ActionCode.HUMAN_HANDOFF): 0.35,
    # --- network_timeout / bank_server_down: transient infrastructure. A plain
    #     retry is very likely to succeed; messaging the customer is pointless.
    (RootCauseCode.NETWORK_TIMEOUT, ActionCode.UPDATE_CARD_EMAIL): 0.20,
    (RootCauseCode.NETWORK_TIMEOUT, ActionCode.SMS_REMINDER): 0.18,
    (RootCauseCode.NETWORK_TIMEOUT, ActionCode.RETRY_PAYMENT): 0.72,
    (RootCauseCode.NETWORK_TIMEOUT, ActionCode.HUMAN_HANDOFF): 0.40,
    (RootCauseCode.BANK_SERVER_DOWN, ActionCode.UPDATE_CARD_EMAIL): 0.18,
    (RootCauseCode.BANK_SERVER_DOWN, ActionCode.SMS_REMINDER): 0.16,
    (RootCauseCode.BANK_SERVER_DOWN, ActionCode.RETRY_PAYMENT): 0.65,
    (RootCauseCode.BANK_SERVER_DOWN, ActionCode.HUMAN_HANDOFF): 0.35,
    # --- 3ds_failed: an authentication stumble; prompting the customer to try
    #     again works reasonably often.
    (RootCauseCode.THREE_DS_FAILED, ActionCode.UPDATE_CARD_EMAIL): 0.38,
    (RootCauseCode.THREE_DS_FAILED, ActionCode.SMS_REMINDER): 0.42,
    (RootCauseCode.THREE_DS_FAILED, ActionCode.HUMAN_HANDOFF): 0.45,
    # --- risk_engine_blocked: a deliberate block. Automation should not fight
    #     it; a human can review and release.
    (RootCauseCode.RISK_ENGINE_BLOCKED, ActionCode.UPDATE_CARD_EMAIL): 0.05,
    (RootCauseCode.RISK_ENGINE_BLOCKED, ActionCode.SMS_REMINDER): 0.05,
    (RootCauseCode.RISK_ENGINE_BLOCKED, ActionCode.HUMAN_HANDOFF): 0.45,
    # --- checkout_abandoned: an in-session nudge is worth far more than a
    #     later email, because intent decays fast.
    (RootCauseCode.PAYMENT_STEP_DROPPED, ActionCode.IN_APP_NUDGE): 0.34,
    (RootCauseCode.PAYMENT_STEP_DROPPED, ActionCode.EMAIL_SAVED_CART): 0.16,
    (RootCauseCode.OTP_TIMEOUT, ActionCode.IN_APP_NUDGE): 0.45,
    (RootCauseCode.OTP_TIMEOUT, ActionCode.EMAIL_SAVED_CART): 0.18,
    (RootCauseCode.SESSION_EXPIRED, ActionCode.IN_APP_NUDGE): 0.40,
    (RootCauseCode.SESSION_EXPIRED, ActionCode.EMAIL_SAVED_CART): 0.20,
    (RootCauseCode.PRICE_SHOCK, ActionCode.IN_APP_NUDGE): 0.12,
    (RootCauseCode.PRICE_SHOCK, ActionCode.EMAIL_SAVED_CART): 0.15,
    (RootCauseCode.NO_PREFERRED_METHOD, ActionCode.IN_APP_NUDGE): 0.20,
    (RootCauseCode.NO_PREFERRED_METHOD, ActionCode.EMAIL_SAVED_CART): 0.10,
    (RootCauseCode.UNKNOWN, ActionCode.IN_APP_NUDGE): 0.22,
    (RootCauseCode.UNKNOWN, ActionCode.EMAIL_SAVED_CART): 0.12,
    # --- subscription_failed: the only permitted move is to wait for the
    #     gateway's own retry. The probability is the gateway's, not ours.
    (RootCauseCode.CARD_EXPIRED, ActionCode.AWAIT_GATEWAY_AUTO_RETRY): 0.18,
    (RootCauseCode.INSUFFICIENT_FUNDS, ActionCode.AWAIT_GATEWAY_AUTO_RETRY): 0.40,
    (RootCauseCode.USER_PAUSED, ActionCode.AWAIT_GATEWAY_AUTO_RETRY): 0.10,
    # --- invoice_overdue: a forgotten invoice is the easiest money in the
    #     system. A cash-flow delay is the hardest, and pressure does not help.
    (RootCauseCode.FORGOTTEN, ActionCode.FRIENDLY_REMINDER): 0.62,
    (RootCauseCode.FORGOTTEN, ActionCode.REMINDER_WITH_CALL_SCRIPT): 0.70,
    (RootCauseCode.FORGOTTEN, ActionCode.FORMAL_NOTICE): 0.68,
    (RootCauseCode.FORGOTTEN, ActionCode.HUMAN_HANDOFF): 0.72,
    (RootCauseCode.AWAITING_APPROVAL, ActionCode.FRIENDLY_REMINDER): 0.30,
    (RootCauseCode.AWAITING_APPROVAL, ActionCode.REMINDER_WITH_CALL_SCRIPT): 0.52,
    (RootCauseCode.AWAITING_APPROVAL, ActionCode.FORMAL_NOTICE): 0.48,
    (RootCauseCode.AWAITING_APPROVAL, ActionCode.HUMAN_HANDOFF): 0.60,
    (RootCauseCode.CASH_FLOW_DELAY, ActionCode.FRIENDLY_REMINDER): 0.15,
    (RootCauseCode.CASH_FLOW_DELAY, ActionCode.REMINDER_WITH_CALL_SCRIPT): 0.28,
    (RootCauseCode.CASH_FLOW_DELAY, ActionCode.FORMAL_NOTICE): 0.32,
    (RootCauseCode.CASH_FLOW_DELAY, ActionCode.HUMAN_HANDOFF): 0.42,
    (RootCauseCode.DISPUTED_AMOUNT, ActionCode.FRIENDLY_REMINDER): 0.08,
    (RootCauseCode.DISPUTED_AMOUNT, ActionCode.REMINDER_WITH_CALL_SCRIPT): 0.25,
    (RootCauseCode.DISPUTED_AMOUNT, ActionCode.FORMAL_NOTICE): 0.20,
    (RootCauseCode.DISPUTED_AMOUNT, ActionCode.HUMAN_HANDOFF): 0.55,
    (RootCauseCode.DELIVERY_FAILURE, ActionCode.FRIENDLY_REMINDER): 0.10,
    (RootCauseCode.DELIVERY_FAILURE, ActionCode.REMINDER_WITH_CALL_SCRIPT): 0.22,
    (RootCauseCode.DELIVERY_FAILURE, ActionCode.FORMAL_NOTICE): 0.18,
    (RootCauseCode.DELIVERY_FAILURE, ActionCode.HUMAN_HANDOFF): 0.58,
    # --- broken_ptp: a promise already broken once. Softer approaches have
    #     demonstrably failed, so escalation is what remains.
    (RootCauseCode.BROKEN_PTP, ActionCode.FRIENDLY_REMINDER): 0.12,
    (RootCauseCode.BROKEN_PTP, ActionCode.REMINDER_WITH_CALL_SCRIPT): 0.35,
    (RootCauseCode.BROKEN_PTP, ActionCode.FORMAL_NOTICE): 0.40,
    (RootCauseCode.BROKEN_PTP, ActionCode.HUMAN_HANDOFF): 0.50,
    # --- mandate_failed: re-authentication is a customer action and converts
    #     well when asked directly. Balance problems respond to salary timing.
    (RootCauseCode.NOT_AUTHENTICATED, ActionCode.REAUTH_NUDGE): 0.50,
    (RootCauseCode.NOT_AUTHENTICATED, ActionCode.RETRY_PAYMENT): 0.20,
    (RootCauseCode.NOT_AUTHENTICATED, ActionCode.FINAL_RETRY): 0.18,
    (RootCauseCode.NOT_AUTHENTICATED, ActionCode.HUMAN_HANDOFF): 0.45,
    (RootCauseCode.INSUFFICIENT_BALANCE, ActionCode.RETRY_PAYMENT): 0.22,
    (RootCauseCode.INSUFFICIENT_BALANCE, ActionCode.RETRY_SALARY_WINDOW): 0.48,
    (RootCauseCode.INSUFFICIENT_BALANCE, ActionCode.FINAL_RETRY): 0.20,
    (RootCauseCode.INSUFFICIENT_BALANCE, ActionCode.HUMAN_HANDOFF): 0.38,
    (RootCauseCode.EXPIRED, ActionCode.REAUTH_NUDGE): 0.42,
    (RootCauseCode.EXPIRED, ActionCode.RETRY_PAYMENT): 0.06,
    (RootCauseCode.EXPIRED, ActionCode.FINAL_RETRY): 0.05,
    (RootCauseCode.EXPIRED, ActionCode.HUMAN_HANDOFF): 0.40,
}

#: Used when a (cause, action) pair is not in the table. Low on purpose: an
#: unlisted pairing is one nobody reasoned about, and it should not win.
DEFAULT_RECOVERY_PROBABILITY = 0.10

#: Multiplier by attempt number. A second approach to the same customer about
#: the same debt converts materially worse than the first; a third worse again.
ATTEMPT_DECAY: dict[int, float] = {1: 1.00, 2: 0.65, 3: 0.40}
DEFAULT_ATTEMPT_DECAY = 0.25

# --------------------------------------------------------------------------- #
# cost(action)
# --------------------------------------------------------------------------- #
#
# Direct INR cost of performing the action once: messaging fees, and for human
# actions a share of an agent's time. Order-of-magnitude realistic for India.

COST_BY_ACTION: dict[ActionCode, Decimal] = {
    ActionCode.IN_APP_NUDGE: Decimal("0.02"),
    ActionCode.UPDATE_CARD_EMAIL: Decimal("0.10"),
    ActionCode.EMAIL_SAVED_CART: Decimal("0.10"),
    ActionCode.FRIENDLY_REMINDER: Decimal("0.10"),
    ActionCode.SMS_REMINDER: Decimal("0.25"),
    ActionCode.REAUTH_NUDGE: Decimal("0.25"),
    ActionCode.RETRY_PAYMENT: Decimal("2.00"),
    ActionCode.RETRY_SALARY_WINDOW: Decimal("2.00"),
    ActionCode.FINAL_RETRY: Decimal("2.00"),
    ActionCode.REMINDER_WITH_CALL_SCRIPT: Decimal("45.00"),
    ActionCode.FORMAL_NOTICE: Decimal("250.00"),
    ActionCode.HUMAN_HANDOFF: Decimal("400.00"),
    ActionCode.AWAIT_GATEWAY_AUTO_RETRY: Decimal("0.00"),
    ActionCode.NO_ACTION: Decimal("0.00"),
}
DEFAULT_ACTION_COST = Decimal("1.00")

# --------------------------------------------------------------------------- #
# annoyance_penalty(attempt_number)
# --------------------------------------------------------------------------- #
#
# A rupee-denominated proxy for goodwill damage. It is not a cash cost; it is
# the price we are willing to put on pestering someone, so that the formula
# stops chasing small balances indefinitely. Grows faster than linearly because
# the third message is far more irritating than the second.

ANNOYANCE_PENALTY: dict[int, Decimal] = {
    1: Decimal("0.00"),
    2: Decimal("25.00"),
    3: Decimal("90.00"),
}
ANNOYANCE_PENALTY_BEYOND = Decimal("200.00")


@dataclass(frozen=True)
class ActionScore:
    """A scored candidate action, with every term of the formula kept separate.

    The components are retained rather than collapsed into a single number so
    ``Decision.decision_factors`` can show the arithmetic and a reviewer can see
    exactly why one action beat another.
    """

    action: ActionCode
    probability: float
    amount_at_risk: Decimal
    expected_value: Decimal
    cost: Decimal
    annoyance_penalty: Decimal
    score: Decimal
    attempt_number: int
    source: ProbabilitySource = ProbabilitySource.DETERMINISTIC

    def as_factors(self) -> dict[str, object]:
        """Serialisable breakdown for decision_factors / the drill-down UI."""
        return {
            "action": self.action.value,
            "probability": round(self.probability, 4),
            "amount_at_risk": str(self.amount_at_risk),
            "expected_value": str(self.expected_value),
            "cost": str(self.cost),
            "annoyance_penalty": str(self.annoyance_penalty),
            "score": str(self.score),
            "attempt_number": self.attempt_number,
            "probability_source": self.source.value,
        }


def recovery_probability(
    root_cause: RootCauseCode,
    action: ActionCode,
    attempt_number: int,
    *,
    customer_multiplier: float = 1.0,
) -> float:
    """P(recovery | root_cause, action, attempt_number, this customer).

    The hand-set base rate for the pairing, decayed by attempt number, then
    adjusted for what is known about this particular customer. Always clamped
    into [0, 1].

    ``customer_multiplier`` defaults to 1.0 so every existing caller behaves
    exactly as before — the customer signal is an extension of this function,
    not a replacement for it, and a caller with no customer data gets the same
    answer it always did.
    """
    base = BASE_RECOVERY_PROBABILITY.get(
        (root_cause, action), DEFAULT_RECOVERY_PROBABILITY
    )
    decay = ATTEMPT_DECAY.get(attempt_number, DEFAULT_ATTEMPT_DECAY)
    return max(0.0, min(1.0, base * decay * customer_multiplier))


#: How far customer history may move a base probability, either way.
#:
#: Bounded on purpose. History is a real signal but a noisy one, and letting it
#: swing a probability from 0.2 to 0.9 would make the recorded base rates
#: decorative. A third either way is enough to change which action wins without
#: letting one feature take over the decision.
CUSTOMER_ADJUSTMENT_RANGE = 0.33


def customer_likelihood(
    *,
    payment_success_rate: float | None,
    avg_payment_delay_days: float | None,
    promises_kept: int = 0,
    promises_broken: int = 0,
) -> tuple[float, str | None]:
    """How likely THIS customer is to pay, relative to an average one.

    Returns a multiplier around 1.0 and, where a signal was strong enough to
    matter, a sentence explaining it in words a merchant would use.

    This is not a new model and not a new score to display. It exists to move
    the probability the decision engine already uses, so that a customer who
    always pays gets a lighter touch and one who has broken three promises does
    not get the same cheerful reminder as everyone else.

    Signals are only applied when they are actually present. A customer with no
    history returns exactly 1.0 and no explanation — absence of evidence must
    not be read as evidence of unreliability, or every new customer would be
    treated as a bad one.
    """
    adjustment = 0.0
    reasons: list[tuple[float, str]] = []

    if payment_success_rate is not None:
        # Centred on 0.5: better than half the time helps, worse hurts.
        delta = (payment_success_rate - 0.5) * 0.5
        adjustment += delta
        if payment_success_rate >= 0.8:
            reasons.append((abs(delta), "this customer usually pays"))
        elif payment_success_rate <= 0.3:
            reasons.append((abs(delta), "this customer often does not pay"))

    if avg_payment_delay_days:
        # Chronic lateness lowers the odds for THIS attempt without writing the
        # customer off — they may well pay, just not yet.
        if avg_payment_delay_days > 20:
            adjustment -= 0.12
            reasons.append((0.12, "they usually pay well after the due date"))
        elif avg_payment_delay_days > 7:
            adjustment -= 0.05

    total_promises = promises_kept + promises_broken
    if total_promises:
        kept_rate = promises_kept / total_promises
        delta = (kept_rate - 0.5) * 0.4
        adjustment += delta
        if promises_broken >= 2 and kept_rate < 0.5:
            reasons.append((abs(delta), "they have broken previous payment promises"))
        elif promises_kept >= 2 and kept_rate >= 0.75:
            reasons.append((abs(delta), "they have kept their promises before"))

    adjustment = max(-CUSTOMER_ADJUSTMENT_RANGE, min(CUSTOMER_ADJUSTMENT_RANGE, adjustment))
    reason = max(reasons, key=lambda item: item[0])[1] if reasons else None
    return 1.0 + adjustment, reason


def cost(action: ActionCode) -> Decimal:
    """Direct INR cost of performing ``action`` once."""
    return COST_BY_ACTION.get(action, DEFAULT_ACTION_COST)


def annoyance_penalty(attempt_number: int) -> Decimal:
    """Goodwill penalty in INR for the Nth approach to this customer."""
    return ANNOYANCE_PENALTY.get(attempt_number, ANNOYANCE_PENALTY_BEYOND)


def score_action(
    root_cause: RootCauseCode,
    action: ActionCode,
    attempt_number: int,
    amount_at_risk: Decimal,
    *,
    customer_multiplier: float = 1.0,
) -> ActionScore:
    """Evaluate Section 6's formula for one candidate action.

    score = P(recovery | cause, action, attempt, customer) x amount_at_risk
            - cost(action) - annoyance_penalty(attempt)

    The customer multiplier tilts the probability toward what is known about
    this particular payer. It changes which action wins; it can never make an
    illegal action legal, because the ranking is still walked through the policy
    gate afterwards and the gate does the choosing.
    """
    probability = recovery_probability(
        root_cause, action, attempt_number, customer_multiplier=customer_multiplier
    )
    action_cost = cost(action)
    penalty = annoyance_penalty(attempt_number)

    # Decimal x float is not permitted, and float money is not permitted either,
    # so the probability is converted exactly before multiplying.
    expected_value = (amount_at_risk * Decimal(str(probability))).quantize(Decimal("0.01"))
    total = (expected_value - action_cost - penalty).quantize(Decimal("0.01"))

    return ActionScore(
        action=action,
        probability=probability,
        amount_at_risk=amount_at_risk,
        expected_value=expected_value,
        cost=action_cost,
        annoyance_penalty=penalty,
        score=total,
        attempt_number=attempt_number,
    )


def rank_actions(
    root_cause: RootCauseCode,
    actions: list[ActionCode],
    attempt_number: int,
    amount_at_risk: Decimal,
    *,
    customer_multiplier: float = 1.0,
) -> list[ActionScore]:
    """Score every candidate, best first.

    Section 6: "Pick highest-scoring action that passes the policy gate." This
    returns the ranking; the policy gate does the picking, walking the list in
    order. Ties break on the action's own name so the ordering is total and
    reproducible rather than dependent on dict iteration.
    """
    scored = [
        score_action(
            root_cause,
            action,
            attempt_number,
            amount_at_risk,
            customer_multiplier=customer_multiplier,
        )
        for action in actions
    ]
    return sorted(scored, key=lambda s: (-s.score, s.action.value))
