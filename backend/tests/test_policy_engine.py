"""Tests for engine/policy_engine.py. BUILD_SPEC Sections 4 and 6.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

Two properties dominate here.

The gate must actually block. Section 2's bar asks for "stopping rules that
actually stop things", so every rule is tested both ways: a case it refuses and
a matching case it allows. A gate that only ever says yes would pass a
one-sided test suite and fail the product.

The structured result must keep its exact shape. Section 4 fixes
``policy_result`` at five keys, and /exceptions renders them as a concrete
comparison. Tests assert the shape as well as the verdict.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.engine import policy_engine
from app.engine.diagnosis_engine import ActionCode, DiagnosisResult
from app.engine.policy_engine import (
    DEFAULT_POLICY_VERSION,
    RULE_AMOUNT_THRESHOLD,
    RULE_CONTACT_LIMIT,
    RULE_COOLDOWN,
    RULE_DO_NOT_CONTACT,
    RULE_ESCALATION_CEILING,
    RULE_HARD_STOP,
    RULE_MAX_ATTEMPTS,
    RULE_NO_ELIGIBLE_ACTION,
    RULE_PROBABILITY_THRESHOLD,
    PolicyResult,
    evaluate,
    resolve_policy,
)
from app.enums import EventType, PolicyResultStatus, RootCauseCode
from app.models import CustomerProfile, Decision, Merchant, Policy, RiskEvent, StoppingRuleState

T0 = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)

SOFT_DIAGNOSIS = DiagnosisResult(RootCauseCode.CARD_EXPIRED, 0.95, ["gateway code"])
HARD_DIAGNOSIS = DiagnosisResult(RootCauseCode.ISSUER_DECLINED, 0.95, ["gateway code"])


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def setup(db_session: Session):
    db_session.add(Merchant(id="mer_p", name="Policy Merchant"))
    db_session.add(
        CustomerProfile(customer_id="cust_p", payment_success_rate=0.8, do_not_contact=False)
    )
    db_session.flush()
    return db_session


@pytest.fixture()
def make_event(setup: Session):
    counter = {"n": 0}

    def _make(amount: str = "2499.00", **overrides) -> RiskEvent:
        counter["n"] += 1
        defaults = dict(
            id=f"evt_p{counter['n']}",
            type=EventType.PAYMENT_DEGRADED,
            merchant_id="mer_p",
            customer_id="cust_p",
            amount=Decimal(amount),
            source_ref=f"pay_P{counter['n']}",
            detected_at=T0,
            raw_signal={"gateway_error_code": "BAD_REQUEST_CARD_EXPIRED"},
            correlation_id=f"corr_p{counter['n']}",
        )
        defaults.update(overrides)
        row = RiskEvent(**defaults)
        setup.add(row)
        setup.flush()
        return row

    return _make


def make_policy(session: Session, **overrides) -> Policy:
    defaults = dict(
        policy_version=1,
        merchant_id="mer_p",
        event_type=EventType.PAYMENT_DEGRADED,
        max_attempts=3,
        cooldown_hours=24,
        amount_threshold=Decimal("25000.00"),
        recovery_probability_threshold=0.05,
        contact_limit_per_channel=2,
        escalation_ceiling=2,
    )
    defaults.update(overrides)
    row = Policy(**defaults)
    session.add(row)
    session.flush()
    return row


def run(
    session: Session,
    event: RiskEvent,
    policy: Policy,
    *,
    action: ActionCode = ActionCode.UPDATE_CARD_EMAIL,
    diagnosis: DiagnosisResult = SOFT_DIAGNOSIS,
    probability: float = 0.55,
    attempt_number: int = 1,
    now: datetime = T0,
) -> PolicyResult:
    return evaluate(
        session,
        event,
        action,
        policy=policy,
        diagnosis=diagnosis,
        probability=probability,
        attempt_number=attempt_number,
        now=now,
    )


# --------------------------------------------------------------------------- #
# The structured result — Section 4
# --------------------------------------------------------------------------- #


class TestStructuredPolicyResult:
    def test_result_has_exactly_the_five_specified_keys(self, setup, make_event):
        """Section 4 fixes the shape; extra keys would break the contract."""
        policy = make_policy(setup)
        result = run(setup, make_event(), policy)
        assert set(result.as_dict()) == {
            "status",
            "rule_triggered",
            "threshold_checked",
            "actual_value",
            "threshold_value",
        }

    def test_allowed_result_serialises_cleanly(self, setup, make_event):
        policy = make_policy(setup)
        payload = run(setup, make_event(), policy).as_dict()
        assert payload["status"] == "allowed"
        assert payload["rule_triggered"] is None

    def test_blocked_result_names_the_rule_and_both_values(self, setup, make_event):
        """/exceptions renders this as a concrete comparison, so all three of
        rule, actual and threshold must be populated."""
        policy = make_policy(setup, max_attempts=1)
        event = make_event()
        setup.add(
            StoppingRuleState(event_id=event.id, attempts_used=1, max_attempts_for_type=1)
        )
        setup.flush()
        payload = run(setup, event, policy).as_dict()
        assert payload["status"] == "blocked"
        assert payload["rule_triggered"] == RULE_MAX_ATTEMPTS
        assert payload["threshold_checked"] == "policy.max_attempts"
        assert payload["actual_value"] == 1
        assert payload["threshold_value"] == 1

    def test_status_is_the_enum_value_not_the_name(self, setup, make_event):
        policy = make_policy(setup)
        assert run(setup, make_event(), policy).as_dict()["status"] in ("allowed", "blocked")

    def test_allowed_property_matches_status(self, setup, make_event):
        policy = make_policy(setup)
        result = run(setup, make_event(), policy)
        assert result.allowed is (result.status == PolicyResultStatus.ALLOWED)

    def test_result_is_json_serialisable(self, setup, make_event):
        """It is persisted into a JSON column."""
        import json

        policy = make_policy(setup, amount_threshold=Decimal("100.00"))
        payload = run(setup, make_event("50000.00"), policy).as_dict()
        assert json.loads(json.dumps(payload))["status"] == "blocked"


# --------------------------------------------------------------------------- #
# Each rule, blocking AND allowing
# --------------------------------------------------------------------------- #


class TestDoNotContact:
    def test_blocks_a_contacting_action(self, setup, make_event):
        setup.get(CustomerProfile, "cust_p").do_not_contact = True
        setup.flush()
        result = run(setup, make_event(), make_policy(setup))
        assert result.status == PolicyResultStatus.BLOCKED
        assert result.rule_triggered == RULE_DO_NOT_CONTACT

    def test_allows_when_the_customer_is_contactable(self, setup, make_event):
        result = run(setup, make_event(), make_policy(setup))
        assert result.allowed

    def test_outranks_a_large_amount(self, setup, make_event):
        """Absolute: no amount of money justifies contacting an opted-out customer."""
        setup.get(CustomerProfile, "cust_p").do_not_contact = True
        setup.flush()
        policy = make_policy(setup, amount_threshold=Decimal("100.00"))
        result = run(setup, make_event("500000.00"), policy)
        assert result.rule_triggered == RULE_DO_NOT_CONTACT

    def test_does_not_block_a_non_contacting_action(self, setup, make_event):
        """Waiting for the gateway's own retry contacts nobody."""
        setup.get(CustomerProfile, "cust_p").do_not_contact = True
        setup.flush()
        result = run(
            setup,
            make_event(),
            make_policy(setup),
            action=ActionCode.AWAIT_GATEWAY_AUTO_RETRY,
        )
        assert result.allowed


class TestHardStopCauses:
    def test_hard_cause_blocks_every_action(self, setup, make_event):
        """Section 6: issuer_declined -> no retry, immediate stop."""
        result = run(setup, make_event(), make_policy(setup), diagnosis=HARD_DIAGNOSIS)
        assert result.rule_triggered == RULE_HARD_STOP

    def test_soft_cause_of_the_same_event_type_is_allowed(self, setup, make_event):
        result = run(setup, make_event(), make_policy(setup), diagnosis=SOFT_DIAGNOSIS)
        assert result.allowed

    def test_bank_rejected_is_hard_too(self, setup, make_event):
        """Section 6: "same logic as issuer_declined"."""
        diagnosis = DiagnosisResult(RootCauseCode.BANK_REJECTED, 0.95, [])
        result = run(setup, make_event(), make_policy(setup), diagnosis=diagnosis)
        assert result.rule_triggered == RULE_HARD_STOP


class TestCooldown:
    def test_blocks_while_the_window_is_open(self, setup, make_event):
        event = make_event()
        setup.add(
            StoppingRuleState(
                event_id=event.id,
                attempts_used=0,
                max_attempts_for_type=3,
                cooldown_until=T0 + timedelta(hours=6),
            )
        )
        setup.flush()
        result = run(setup, event, make_policy(setup))
        assert result.rule_triggered == RULE_COOLDOWN

    def test_allows_once_the_window_has_passed(self, setup, make_event):
        event = make_event()
        setup.add(
            StoppingRuleState(
                event_id=event.id,
                attempts_used=0,
                max_attempts_for_type=3,
                cooldown_until=T0 + timedelta(hours=6),
            )
        )
        setup.flush()
        result = run(setup, event, make_policy(setup), now=T0 + timedelta(hours=7))
        assert result.allowed


class TestMaxAttempts:
    def test_blocks_at_the_cap(self, setup, make_event):
        event = make_event()
        setup.add(
            StoppingRuleState(event_id=event.id, attempts_used=2, max_attempts_for_type=2)
        )
        setup.flush()
        result = run(setup, event, make_policy(setup, max_attempts=2))
        assert result.rule_triggered == RULE_MAX_ATTEMPTS

    def test_allows_below_the_cap(self, setup, make_event):
        event = make_event()
        setup.add(
            StoppingRuleState(event_id=event.id, attempts_used=1, max_attempts_for_type=2)
        )
        setup.flush()
        assert run(setup, event, make_policy(setup, max_attempts=2)).allowed

    def test_merchant_policy_is_what_binds(self, setup, make_event):
        """Merchant-configurable: a tighter policy must actually tighten."""
        event = make_event()
        setup.add(
            StoppingRuleState(event_id=event.id, attempts_used=1, max_attempts_for_type=5)
        )
        setup.flush()
        assert run(setup, event, make_policy(setup, max_attempts=1)).rule_triggered == (
            RULE_MAX_ATTEMPTS
        )


class TestContactLimitPerChannel:
    def test_blocks_once_the_channel_cap_is_reached(self, setup, make_event):
        event = make_event()
        for index in range(2):
            setup.add(
                Decision(
                    event_id=event.id,
                    decision_factors={},
                    recovery_probability=0.5,
                    policy_result={},
                    policy_version=1,
                    action_code=ActionCode.UPDATE_CARD_EMAIL.value,
                    reasoning_text="prior",
                    decided_at=T0 - timedelta(hours=index + 1),
                )
            )
        setup.flush()
        result = run(setup, event, make_policy(setup, contact_limit_per_channel=2))
        assert result.rule_triggered == RULE_CONTACT_LIMIT

    def test_allows_below_the_cap(self, setup, make_event):
        event = make_event()
        setup.add(
            Decision(
                event_id=event.id,
                decision_factors={},
                recovery_probability=0.5,
                policy_result={},
                policy_version=1,
                action_code=ActionCode.UPDATE_CARD_EMAIL.value,
                reasoning_text="prior",
                decided_at=T0 - timedelta(hours=1),
            )
        )
        setup.flush()
        assert run(setup, event, make_policy(setup, contact_limit_per_channel=2)).allowed

    def test_a_different_channel_is_counted_separately(self, setup, make_event):
        """Two emails must not exhaust the SMS allowance."""
        event = make_event()
        for index in range(2):
            setup.add(
                Decision(
                    event_id=event.id,
                    decision_factors={},
                    recovery_probability=0.5,
                    policy_result={},
                    policy_version=1,
                    action_code=ActionCode.UPDATE_CARD_EMAIL.value,
                    reasoning_text="prior",
                    decided_at=T0 - timedelta(hours=index + 1),
                )
            )
        setup.flush()
        result = run(
            setup,
            event,
            make_policy(setup, contact_limit_per_channel=2),
            action=ActionCode.SMS_REMINDER,
        )
        assert result.allowed


class TestEscalationCeiling:
    def test_blocks_above_the_ceiling(self, setup, make_event):
        """Section 6: never auto-escalate past L2."""
        result = run(
            setup,
            make_event(),
            make_policy(setup, escalation_ceiling=1),
            action=ActionCode.HUMAN_HANDOFF,
        )
        assert result.rule_triggered == RULE_ESCALATION_CEILING
        assert result.actual_value == 2
        assert result.threshold_value == 1

    def test_allows_at_the_ceiling(self, setup, make_event):
        result = run(
            setup,
            make_event(),
            make_policy(setup, escalation_ceiling=2, amount_threshold=Decimal("100.00")),
            action=ActionCode.HUMAN_HANDOFF,
        )
        assert result.allowed

    def test_a_non_escalating_action_is_unaffected(self, setup, make_event):
        assert run(setup, make_event(), make_policy(setup, escalation_ceiling=0)).allowed


class TestAmountThreshold:
    def test_large_amount_blocks_an_automated_action(self, setup, make_event):
        """Section 6: human handoff if amount > threshold."""
        policy = make_policy(setup, amount_threshold=Decimal("10000.00"))
        result = run(setup, make_event("50000.00"), policy)
        assert result.rule_triggered == RULE_AMOUNT_THRESHOLD

    def test_human_handoff_is_permitted_at_the_same_amount(self, setup, make_event):
        """The rule redirects to a human; it does not abandon the money."""
        policy = make_policy(setup, amount_threshold=Decimal("10000.00"))
        result = run(setup, make_event("50000.00"), policy, action=ActionCode.HUMAN_HANDOFF)
        assert result.allowed

    def test_small_amount_is_allowed_automatically(self, setup, make_event):
        policy = make_policy(setup, amount_threshold=Decimal("10000.00"))
        assert run(setup, make_event("500.00"), policy).allowed


class TestProbabilityThreshold:
    def test_blocks_a_hopeless_action(self, setup, make_event):
        policy = make_policy(setup, recovery_probability_threshold=0.30)
        result = run(setup, make_event(), policy, probability=0.05)
        assert result.rule_triggered == RULE_PROBABILITY_THRESHOLD
        assert result.threshold_value == 0.30

    def test_allows_a_promising_action(self, setup, make_event):
        policy = make_policy(setup, recovery_probability_threshold=0.30)
        assert run(setup, make_event(), policy, probability=0.55).allowed


class TestRuleOrdering:
    def test_the_first_and_most_absolute_rule_is_reported(self, setup, make_event):
        """When several rules would fire, the binding constraint is the one
        reported — otherwise /exceptions misdescribes why nothing happened."""
        setup.get(CustomerProfile, "cust_p").do_not_contact = True
        setup.flush()
        event = make_event("500000.00")
        setup.add(
            StoppingRuleState(
                event_id=event.id,
                attempts_used=9,
                max_attempts_for_type=1,
                cooldown_until=T0 + timedelta(hours=5),
            )
        )
        setup.flush()
        policy = make_policy(setup, max_attempts=1, amount_threshold=Decimal("10.00"))
        result = run(setup, event, policy, probability=0.0)
        assert result.rule_triggered == RULE_DO_NOT_CONTACT


class TestNoEligibleAction:
    def test_reports_an_empty_intervention_table(self, setup):
        result = policy_engine.blocked_because_no_action_permitted(SOFT_DIAGNOSIS)
        assert result.rule_triggered == RULE_NO_ELIGIBLE_ACTION
        assert result.status == PolicyResultStatus.BLOCKED

    def test_a_hard_cause_is_reported_as_a_hard_stop_instead(self, setup):
        """"Policy said no" and "there was nothing to say yes to" are different
        answers and /exceptions should be able to tell them apart."""
        result = policy_engine.blocked_because_no_action_permitted(HARD_DIAGNOSIS)
        assert result.rule_triggered == RULE_HARD_STOP


# --------------------------------------------------------------------------- #
# Policy resolution and versioning
# --------------------------------------------------------------------------- #


class TestPolicyResolution:
    def test_returns_the_merchant_policy_when_one_exists(self, setup, make_event):
        make_policy(setup, max_attempts=7)
        resolved = resolve_policy(setup, make_event())
        assert resolved.max_attempts == 7
        assert resolved.policy_version == 1

    def test_returns_the_highest_version(self, setup, make_event):
        """Policies are versioned, never mutated."""
        make_policy(setup, policy_version=1, max_attempts=1)
        make_policy(setup, policy_version=2, max_attempts=9)
        assert resolve_policy(setup, make_event()).max_attempts == 9

    def test_falls_back_to_a_default_when_unconfigured(self, setup, make_event):
        resolved = resolve_policy(setup, make_event())
        assert resolved.policy_version == DEFAULT_POLICY_VERSION

    def test_the_default_is_not_persisted(self, setup, make_event):
        """Persisting it would invent configuration the merchant never chose."""
        resolve_policy(setup, make_event())
        setup.flush()
        assert setup.query(Policy).count() == 0

    def test_policies_are_scoped_per_event_type(self, setup, make_event):
        make_policy(setup, event_type=EventType.INVOICE_OVERDUE, max_attempts=9)
        resolved = resolve_policy(setup, make_event())
        assert resolved.policy_version == DEFAULT_POLICY_VERSION
