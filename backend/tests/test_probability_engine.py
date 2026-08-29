"""Tests for engine/probability_engine.py. BUILD_SPEC Section 6.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

The formula under test:

    score(action) = P(recovery | root_cause, action, attempt_number) x amount_at_risk
                    - cost(action) - annoyance_penalty(attempt_number)

The core tests recompute it by hand from the published constants and compare
against the engine, rather than asserting whatever the engine happens to return.
A test written the second way passes even when the arithmetic is wrong, which
makes it worse than no test at all.

Also asserted: money stays exact. Everything monetary is ``Decimal``, and a
float sneaking in would show up as a rounding mismatch here long before it
showed up as a wrong total in a 500-record batch.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.diagnosis_engine import ActionCode
from app.engine import probability_engine
from app.engine.probability_engine import (
    ANNOYANCE_PENALTY,
    ANNOYANCE_PENALTY_BEYOND,
    ATTEMPT_DECAY,
    BASE_RECOVERY_PROBABILITY,
    COST_BY_ACTION,
    DEFAULT_ACTION_COST,
    DEFAULT_ATTEMPT_DECAY,
    DEFAULT_RECOVERY_PROBABILITY,
    ActionScore,
    annoyance_penalty,
    cost,
    rank_actions,
    recovery_probability,
    score_action,
)
from app.enums import ProbabilitySource, RootCauseCode


def hand_score(
    cause: RootCauseCode, action: ActionCode, attempt: int, amount: Decimal
) -> tuple[float, Decimal, Decimal]:
    """Recompute the Section 6 formula independently of the engine."""
    base = BASE_RECOVERY_PROBABILITY.get((cause, action), DEFAULT_RECOVERY_PROBABILITY)
    decay = ATTEMPT_DECAY.get(attempt, DEFAULT_ATTEMPT_DECAY)
    probability = max(0.0, min(1.0, base * decay))
    expected = (amount * Decimal(str(probability))).quantize(Decimal("0.01"))
    total = (
        expected
        - COST_BY_ACTION.get(action, DEFAULT_ACTION_COST)
        - ANNOYANCE_PENALTY.get(attempt, ANNOYANCE_PENALTY_BEYOND)
    ).quantize(Decimal("0.01"))
    return probability, expected, total


# --------------------------------------------------------------------------- #
# The formula
# --------------------------------------------------------------------------- #


class TestFormulaAgainstHandArithmetic:
    CASES = [
        (RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 1, "2499.00"),
        (RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 2, "2499.00"),
        (RootCauseCode.NETWORK_TIMEOUT, ActionCode.RETRY_PAYMENT, 1, "10000.00"),
        (RootCauseCode.FORGOTTEN, ActionCode.FRIENDLY_REMINDER, 1, "50000.00"),
        (RootCauseCode.CASH_FLOW_DELAY, ActionCode.HUMAN_HANDOFF, 3, "1000.00"),
        (RootCauseCode.INSUFFICIENT_BALANCE, ActionCode.RETRY_SALARY_WINDOW, 2, "7500.00"),
        (RootCauseCode.OTP_TIMEOUT, ActionCode.IN_APP_NUDGE, 1, "899.00"),
    ]

    @pytest.mark.parametrize("cause,action,attempt,amount", CASES)
    def test_probability_matches(self, cause, action, attempt, amount):
        expected, _, _ = hand_score(cause, action, attempt, Decimal(amount))
        assert score_action(cause, action, attempt, Decimal(amount)).probability == expected

    @pytest.mark.parametrize("cause,action,attempt,amount", CASES)
    def test_expected_value_matches(self, cause, action, attempt, amount):
        _, expected, _ = hand_score(cause, action, attempt, Decimal(amount))
        assert score_action(cause, action, attempt, Decimal(amount)).expected_value == expected

    @pytest.mark.parametrize("cause,action,attempt,amount", CASES)
    def test_score_matches(self, cause, action, attempt, amount):
        _, _, expected = hand_score(cause, action, attempt, Decimal(amount))
        assert score_action(cause, action, attempt, Decimal(amount)).score == expected

    def test_a_worked_example_end_to_end(self):
        """Spelled out so the arithmetic is visible in the test, not just derived.

        P = 0.55 x 1.0 = 0.55
        EV = 2499.00 x 0.55 = 1374.45
        score = 1374.45 - 0.10 - 0.00 = 1374.35
        """
        result = score_action(
            RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 1, Decimal("2499.00")
        )
        assert result.probability == 0.55
        assert result.expected_value == Decimal("1374.45")
        assert result.cost == Decimal("0.10")
        assert result.annoyance_penalty == Decimal("0.00")
        assert result.score == Decimal("1374.35")

    def test_the_three_terms_actually_compose(self):
        """score must equal EV - cost - penalty, not merely correlate with it."""
        result = score_action(
            RootCauseCode.FORGOTTEN, ActionCode.REMINDER_WITH_CALL_SCRIPT, 2, Decimal("18000.00")
        )
        assert result.score == result.expected_value - result.cost - result.annoyance_penalty


class TestScoreCanBeNegative:
    def test_an_expensive_action_on_a_small_balance_scores_negative(self):
        """The engine must be able to say an action is not worth taking."""
        result = score_action(
            RootCauseCode.CASH_FLOW_DELAY, ActionCode.HUMAN_HANDOFF, 1, Decimal("500.00")
        )
        assert result.score < 0

    def test_the_same_action_on_a_large_balance_scores_positive(self):
        result = score_action(
            RootCauseCode.CASH_FLOW_DELAY, ActionCode.HUMAN_HANDOFF, 1, Decimal("500000.00")
        )
        assert result.score > 0

    def test_a_zero_amount_leaves_only_costs(self):
        result = score_action(
            RootCauseCode.CARD_EXPIRED, ActionCode.SMS_REMINDER, 1, Decimal("0.00")
        )
        assert result.expected_value == Decimal("0.00")
        assert result.score == -cost(ActionCode.SMS_REMINDER)


# --------------------------------------------------------------------------- #
# P(recovery | ...)
# --------------------------------------------------------------------------- #


class TestRecoveryProbability:
    def test_attempt_decay_reduces_the_probability(self):
        """A second approach about the same debt converts worse than the first."""
        first = recovery_probability(RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 1)
        second = recovery_probability(RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 2)
        third = recovery_probability(RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 3)
        assert first > second > third

    def test_attempt_one_is_undecayed(self):
        assert ATTEMPT_DECAY[1] == 1.00

    def test_an_unlisted_pairing_falls_back_low(self):
        """An unlisted pairing is one nobody reasoned about; it should not win."""
        value = recovery_probability(RootCauseCode.PRICE_SHOCK, ActionCode.FORMAL_NOTICE, 1)
        assert value == DEFAULT_RECOVERY_PROBABILITY

    @pytest.mark.parametrize("attempt", [1, 2, 3, 9, 99])
    def test_every_table_entry_stays_a_probability(self, attempt):
        for cause, action in BASE_RECOVERY_PROBABILITY:
            assert 0.0 <= recovery_probability(cause, action, attempt) <= 1.0

    def test_every_base_rate_is_itself_a_probability(self):
        for key, value in BASE_RECOVERY_PROBABILITY.items():
            assert 0.0 <= value <= 1.0, key

    def test_a_retry_beats_a_message_for_a_transient_failure(self):
        """Documented shape of the table: infrastructure problems clear on
        retry, and messaging the customer about them is pointless."""
        retry = recovery_probability(RootCauseCode.NETWORK_TIMEOUT, ActionCode.RETRY_PAYMENT, 1)
        email = recovery_probability(
            RootCauseCode.NETWORK_TIMEOUT, ActionCode.UPDATE_CARD_EMAIL, 1
        )
        assert retry > email

    def test_an_email_beats_a_retry_for_an_expired_card(self):
        """Nothing recovers an expired card except the customer replacing it."""
        email = recovery_probability(RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 1)
        assert email > recovery_probability(
            RootCauseCode.CARD_EXPIRED, ActionCode.SMS_REMINDER, 1
        )

    def test_salary_timing_beats_an_immediate_retry_for_an_empty_account(self):
        """Section 6 times the day-3 mandate retry to the salary window."""
        timed = recovery_probability(
            RootCauseCode.INSUFFICIENT_BALANCE, ActionCode.RETRY_SALARY_WINDOW, 1
        )
        immediate = recovery_probability(
            RootCauseCode.INSUFFICIENT_BALANCE, ActionCode.RETRY_PAYMENT, 1
        )
        assert timed > immediate


class TestCost:
    def test_every_action_has_a_cost(self):
        for action in ActionCode:
            assert cost(action) >= Decimal("0.00")

    def test_costs_are_decimal_not_float(self):
        """Float money would drift across a 500-record batch."""
        for action in ActionCode:
            assert isinstance(cost(action), Decimal)

    def test_doing_nothing_is_free(self):
        assert cost(ActionCode.NO_ACTION) == Decimal("0.00")
        assert cost(ActionCode.AWAIT_GATEWAY_AUTO_RETRY) == Decimal("0.00")

    def test_a_human_costs_far_more_than_a_message(self):
        assert cost(ActionCode.HUMAN_HANDOFF) > cost(ActionCode.SMS_REMINDER) * 100

    def test_an_in_app_nudge_is_the_cheapest_contact(self):
        contacts = [
            ActionCode.IN_APP_NUDGE,
            ActionCode.UPDATE_CARD_EMAIL,
            ActionCode.SMS_REMINDER,
            ActionCode.FORMAL_NOTICE,
            ActionCode.HUMAN_HANDOFF,
        ]
        assert cost(ActionCode.IN_APP_NUDGE) == min(cost(a) for a in contacts)


class TestAnnoyancePenalty:
    def test_the_first_approach_is_free(self):
        assert annoyance_penalty(1) == Decimal("0.00")

    def test_it_grows_with_each_approach(self):
        assert annoyance_penalty(1) < annoyance_penalty(2) < annoyance_penalty(3)

    def test_it_grows_faster_than_linearly(self):
        """The third message irritates far more than the second."""
        second = annoyance_penalty(2) - annoyance_penalty(1)
        third = annoyance_penalty(3) - annoyance_penalty(2)
        assert third > second

    def test_beyond_the_table_it_is_capped_but_severe(self):
        assert annoyance_penalty(9) == ANNOYANCE_PENALTY_BEYOND
        assert annoyance_penalty(9) > annoyance_penalty(3)

    def test_it_is_decimal(self):
        for attempt in (1, 2, 3, 9):
            assert isinstance(annoyance_penalty(attempt), Decimal)

    def test_it_eventually_stops_pursuit_of_small_balances(self):
        """The reason the penalty exists: without it the formula would chase a
        tiny balance indefinitely."""
        result = score_action(
            RootCauseCode.CARD_EXPIRED, ActionCode.SMS_REMINDER, 3, Decimal("200.00")
        )
        assert result.score < 0


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #


class TestRanking:
    ACTIONS = [
        ActionCode.FRIENDLY_REMINDER,
        ActionCode.REMINDER_WITH_CALL_SCRIPT,
        ActionCode.FORMAL_NOTICE,
        ActionCode.HUMAN_HANDOFF,
    ]

    def test_results_are_ordered_by_score_descending(self):
        ranked = rank_actions(RootCauseCode.FORGOTTEN, self.ACTIONS, 1, Decimal("50000.00"))
        scores = [item.score for item in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_every_candidate_is_scored(self):
        ranked = rank_actions(RootCauseCode.FORGOTTEN, self.ACTIONS, 1, Decimal("50000.00"))
        assert {item.action for item in ranked} == set(self.ACTIONS)

    def test_order_is_stable_under_input_permutation(self):
        """Section 6 picks the highest-scoring action, so the ranking must be a
        total order rather than depending on how candidates were listed."""
        forward = rank_actions(RootCauseCode.FORGOTTEN, self.ACTIONS, 1, Decimal("50000.00"))
        backward = rank_actions(
            RootCauseCode.FORGOTTEN, list(reversed(self.ACTIONS)), 1, Decimal("50000.00")
        )
        assert [item.action for item in forward] == [item.action for item in backward]

    def test_ties_break_deterministically_on_action_name(self):
        """Two unlisted pairings share the default probability, so their scores
        can tie; the tiebreak must not depend on dict ordering."""
        tied = [ActionCode.FORMAL_NOTICE, ActionCode.NO_ACTION]
        first = rank_actions(RootCauseCode.PRICE_SHOCK, tied, 1, Decimal("1000.00"))
        second = rank_actions(
            RootCauseCode.PRICE_SHOCK, list(reversed(tied)), 1, Decimal("1000.00")
        )
        assert [item.action for item in first] == [item.action for item in second]

    def test_an_empty_candidate_list_ranks_to_nothing(self):
        """A hard stop produces no candidates; that must not raise."""
        assert rank_actions(RootCauseCode.ISSUER_DECLINED, [], 1, Decimal("1000.00")) == []

    def test_cost_and_effectiveness_trade_off_at_a_computable_crossover(self):
        """The formula must weigh a better conversion rate against its cost.

        friendly_reminder converts 0.62 at ₹0.10; reminder_with_call_script
        converts 0.70 at ₹45.00. The call is worth its cost only once the extra
        8 percentage points are worth more than ₹44.90:

            0.70A - 45.00 > 0.62A - 0.10   =>   0.08A > 44.90   =>   A > 561.25

        Both sides are asserted. Checking only the cheap-wins side would pass
        against an engine that ignored the probability term entirely, and
        checking only the expensive-wins side would pass against one that
        ignored cost.
        """
        cheap_wins = rank_actions(
            RootCauseCode.FORGOTTEN, self.ACTIONS, 1, Decimal("400.00")
        )
        assert cheap_wins[0].action == ActionCode.FRIENDLY_REMINDER

        effective_wins = rank_actions(
            RootCauseCode.FORGOTTEN, self.ACTIONS, 1, Decimal("5000.00")
        )
        assert effective_wins[0].action != ActionCode.FRIENDLY_REMINDER

    def test_the_crossover_lands_where_the_algebra_says(self):
        """Pins the boundary itself, so a change to either constant is caught
        rather than silently shifting which action gets chosen."""
        below = rank_actions(RootCauseCode.FORGOTTEN, self.ACTIONS, 1, Decimal("560.00"))
        above = rank_actions(RootCauseCode.FORGOTTEN, self.ACTIONS, 1, Decimal("570.00"))
        assert below[0].action == ActionCode.FRIENDLY_REMINDER
        assert above[0].action == ActionCode.REMINDER_WITH_CALL_SCRIPT

    def test_ranking_is_deterministic_across_calls(self):
        first = rank_actions(RootCauseCode.FORGOTTEN, self.ACTIONS, 1, Decimal("50000.00"))
        second = rank_actions(RootCauseCode.FORGOTTEN, self.ACTIONS, 1, Decimal("50000.00"))
        assert [(i.action, i.score) for i in first] == [(i.action, i.score) for i in second]


# --------------------------------------------------------------------------- #
# ActionScore
# --------------------------------------------------------------------------- #


class TestActionScore:
    def test_source_is_deterministic_p0(self):
        """P1 is a later stretch; everything here is the deterministic engine."""
        result = score_action(
            RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 1, Decimal("100.00")
        )
        assert result.source == ProbabilitySource.DETERMINISTIC

    def test_factors_expose_every_term_of_the_formula(self):
        """decision_factors must show the arithmetic so a reviewer can see why
        one action beat another."""
        factors = score_action(
            RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 1, Decimal("2499.00")
        ).as_factors()
        assert set(factors) == {
            "action",
            "probability",
            "amount_at_risk",
            "expected_value",
            "cost",
            "annoyance_penalty",
            "score",
            "attempt_number",
            "probability_source",
        }

    def test_factors_are_json_serialisable(self):
        """They are persisted into a JSON column, so Decimal must be stringified."""
        import json

        factors = score_action(
            RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 1, Decimal("2499.00")
        ).as_factors()
        assert json.loads(json.dumps(factors))["action"] == "update_card_email"

    def test_money_in_factors_survives_as_an_exact_string(self):
        factors = score_action(
            RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 1, Decimal("2499.00")
        ).as_factors()
        assert Decimal(factors["expected_value"]) == Decimal("1374.45")

    def test_monetary_fields_are_all_decimal(self):
        result = score_action(
            RootCauseCode.FORGOTTEN, ActionCode.FRIENDLY_REMINDER, 1, Decimal("50000.00")
        )
        for value in (result.amount_at_risk, result.expected_value, result.cost, result.score):
            assert isinstance(value, Decimal)

    def test_scores_are_quantized_to_paise(self):
        """Money is exact to two places; a third decimal means float crept in."""
        result = score_action(
            RootCauseCode.OTP_TIMEOUT, ActionCode.IN_APP_NUDGE, 1, Decimal("333.33")
        )
        assert result.score == result.score.quantize(Decimal("0.01"))
        assert result.expected_value == result.expected_value.quantize(Decimal("0.01"))

    def test_action_score_is_immutable(self):
        """It is recorded in the audit trail; it must not be edited after scoring."""
        result = score_action(
            RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 1, Decimal("100.00")
        )
        with pytest.raises(Exception):
            result.score = Decimal("999999.00")  # type: ignore[misc]


class TestCustomerLikelihood:
    """Customer history adjusts the probability the engine already uses.

    The properties worth guarding are about restraint. A history signal that
    could swing a probability from 0.2 to 0.9 would make the recorded base rates
    decorative, and a customer with no history must not be treated as a bad one
    — that would penalise every new customer for being new.
    """

    def test_no_history_changes_nothing(self):
        multiplier, reason = probability_engine.customer_likelihood(
            payment_success_rate=None, avg_payment_delay_days=None
        )
        assert multiplier == 1.0
        assert reason is None

    def test_a_reliable_payer_raises_the_odds(self):
        multiplier, _ = probability_engine.customer_likelihood(
            payment_success_rate=0.92, avg_payment_delay_days=1.0
        )
        assert multiplier > 1.0

    def test_an_unreliable_payer_lowers_them(self):
        multiplier, _ = probability_engine.customer_likelihood(
            payment_success_rate=0.15, avg_payment_delay_days=45.0
        )
        assert multiplier < 1.0

    def test_broken_promises_lower_the_odds(self):
        kept, _ = probability_engine.customer_likelihood(
            payment_success_rate=0.5, avg_payment_delay_days=None,
            promises_kept=3, promises_broken=0,
        )
        broken, _ = probability_engine.customer_likelihood(
            payment_success_rate=0.5, avg_payment_delay_days=None,
            promises_kept=0, promises_broken=3,
        )
        assert broken < kept

    def test_the_adjustment_is_bounded_in_both_directions(self):
        """One feature must not be able to take over the decision."""
        best, _ = probability_engine.customer_likelihood(
            payment_success_rate=1.0, avg_payment_delay_days=0.0,
            promises_kept=50, promises_broken=0,
        )
        worst, _ = probability_engine.customer_likelihood(
            payment_success_rate=0.0, avg_payment_delay_days=999.0,
            promises_kept=0, promises_broken=50,
        )
        limit = probability_engine.CUSTOMER_ADJUSTMENT_RANGE
        assert best <= 1.0 + limit + 1e-9
        assert worst >= 1.0 - limit - 1e-9

    def test_a_strong_signal_is_explained_in_plain_words(self):
        _, reason = probability_engine.customer_likelihood(
            payment_success_rate=0.95, avg_payment_delay_days=1.0
        )
        assert reason
        assert "_" not in reason  # merchant language, not an identifier

    def test_a_weak_signal_claims_nothing(self):
        """Only reasons supported by an actual signal."""
        _, reason = probability_engine.customer_likelihood(
            payment_success_rate=0.52, avg_payment_delay_days=2.0
        )
        assert reason is None


class TestProbabilityStaysBounded:
    def test_the_default_multiplier_preserves_existing_behaviour(self):
        """Every existing caller must get exactly the answer it always did."""
        for cause, action in list(probability_engine.BASE_RECOVERY_PROBABILITY)[:12]:
            for attempt in (1, 2, 3):
                assert probability_engine.recovery_probability(
                    cause, action, attempt
                ) == probability_engine.recovery_probability(
                    cause, action, attempt, customer_multiplier=1.0
                )

    def test_probability_never_leaves_zero_to_one(self):
        for cause, action in list(probability_engine.BASE_RECOVERY_PROBABILITY)[:12]:
            for multiplier in (0.0, 0.5, 1.0, 1.5, 99.0):
                value = probability_engine.recovery_probability(
                    cause, action, 1, customer_multiplier=multiplier
                )
                assert 0.0 <= value <= 1.0

    def test_a_hostile_multiplier_cannot_manufacture_certainty(self):
        """No customer signal may push an action to a guaranteed recovery."""
        cause, action = next(iter(probability_engine.BASE_RECOVERY_PROBABILITY))
        assert (
            probability_engine.recovery_probability(
                cause, action, 1, customer_multiplier=1000.0
            )
            <= 1.0
        )
