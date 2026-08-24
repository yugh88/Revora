"""Tests for engine/diagnosis_engine.py. BUILD_SPEC Section 6.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

Two things this file is built around.

The engine is AUTHORITATIVE (Section 4a), so its output has to be correct on its
own terms, not merely consistent with the classifier. Every root cause it can
return is checked against ``ROOT_CAUSES_BY_EVENT_TYPE`` — a mandate error code
must never yield a card cause, whatever the signal says.

Section 6's table is a specification, not a suggestion. The intervention tests
walk each row's full attempt sequence including the point where it STOPS,
because a table implemented without its stop condition would pass any test that
only checked the happy attempts.

Most tests construct RiskEvent objects without a database. The classifier is
pure, so a session is only needed for the persistence tests at the end.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.engine.diagnosis_engine import (
    AMBIGUOUS_ERROR_CODES,
    CAUSE_BY_ERROR_CODE,
    CHANNEL_BY_ACTION,
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_DEFINITIVE,
    CONFIDENCE_STRONG,
    FALLBACK_CAUSE,
    HARD_STOP_CAUSES,
    LOW_CONFIDENCE_THRESHOLD,
    ActionCode,
    DiagnosisResult,
    candidate_actions,
    classify,
    diagnose,
    escalation_level_for,
)
from app.enums import ROOT_CAUSES_BY_EVENT_TYPE, EventType, RootCauseCode
from app.models import CustomerProfile, Diagnosis, Merchant, RiskEvent

T0 = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)

SOFT = DiagnosisResult(RootCauseCode.CARD_EXPIRED, 0.95, [])
HARD = DiagnosisResult(RootCauseCode.ISSUER_DECLINED, 0.95, [])

#: Above every default amount_threshold used in these tests.
BIG = Decimal("500000.00")
SMALL = Decimal("500.00")


def event(
    event_type: EventType = EventType.PAYMENT_DEGRADED,
    raw: dict | None = None,
    amount: str = "2499.00",
    **overrides,
) -> RiskEvent:
    """Build an unsaved RiskEvent. The classifier never touches the database."""
    defaults = dict(
        id="evt_d1",
        type=event_type,
        merchant_id="mer_d",
        customer_id="cust_d",
        amount=Decimal(amount),
        currency="INR",
        source_ref="ref_d1",
        detected_at=T0,
        raw_signal=raw if raw is not None else {},
        correlation_id="corr_d1",
    )
    defaults.update(overrides)
    return RiskEvent(**defaults)


# --------------------------------------------------------------------------- #
# Vocabulary integrity
# --------------------------------------------------------------------------- #


class TestVocabulary:
    def test_every_mapped_cause_is_a_real_root_cause(self):
        for code, cause in CAUSE_BY_ERROR_CODE.items():
            assert isinstance(cause, RootCauseCode), code

    def test_hard_stop_causes_include_the_two_the_spec_names(self):
        """Section 6 marks issuer_declined and bank_rejected as hard."""
        assert RootCauseCode.ISSUER_DECLINED in HARD_STOP_CAUSES
        assert RootCauseCode.BANK_REJECTED in HARD_STOP_CAUSES

    def test_every_action_has_a_channel(self):
        """The policy engine's per-channel cap looks every action up here."""
        for action in ActionCode:
            assert action in CHANNEL_BY_ACTION, action

    def test_every_event_type_has_a_fallback_cause(self):
        for event_type in EventType:
            assert event_type in FALLBACK_CAUSE

    def test_each_fallback_is_legal_for_its_event_type(self):
        for event_type, cause in FALLBACK_CAUSE.items():
            assert cause in ROOT_CAUSES_BY_EVENT_TYPE[event_type]

    def test_escalating_actions_are_within_the_section_6_ceiling(self):
        """Section 6 caps auto-escalation at L2."""
        for action in ActionCode:
            assert 0 <= escalation_level_for(action) <= 2

    def test_human_handoff_is_the_top_escalation(self):
        assert escalation_level_for(ActionCode.HUMAN_HANDOFF) == 2

    def test_a_plain_email_does_not_escalate(self):
        assert escalation_level_for(ActionCode.UPDATE_CARD_EMAIL) == 0


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


class TestClassificationFromErrorCodes:
    @pytest.mark.parametrize(
        "code,expected",
        [
            ("BAD_REQUEST_CARD_EXPIRED", RootCauseCode.CARD_EXPIRED),
            ("BAD_REQUEST_PAYMENT_INSUFFICIENT_FUNDS", RootCauseCode.INSUFFICIENT_FUNDS),
            ("GATEWAY_ERROR_ISSUER_DECLINED", RootCauseCode.ISSUER_DECLINED),
            ("GATEWAY_ERROR_TIMEOUT", RootCauseCode.NETWORK_TIMEOUT),
            ("GATEWAY_ERROR_ISSUER_DOWN", RootCauseCode.BANK_SERVER_DOWN),
            ("BAD_REQUEST_3DS_AUTHENTICATION_FAILED", RootCauseCode.THREE_DS_FAILED),
            ("BAD_REQUEST_RISK_THRESHOLD_EXCEEDED", RootCauseCode.RISK_ENGINE_BLOCKED),
        ],
    )
    def test_payment_codes_map_to_their_cause(self, code, expected):
        result = classify(event(raw={"gateway_error_code": code}))
        assert result.root_cause == expected
        assert result.confidence == CONFIDENCE_DEFINITIVE

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("BAD_REQUEST_MANDATE_NOT_AUTHENTICATED", RootCauseCode.NOT_AUTHENTICATED),
            ("BAD_REQUEST_MANDATE_INSUFFICIENT_BALANCE", RootCauseCode.INSUFFICIENT_BALANCE),
            ("BAD_REQUEST_MANDATE_BANK_REJECTED", RootCauseCode.BANK_REJECTED),
            ("BAD_REQUEST_MANDATE_EXPIRED", RootCauseCode.EXPIRED),
        ],
    )
    def test_mandate_codes_map_to_their_cause(self, code, expected):
        result = classify(event(EventType.MANDATE_FAILED, {"gateway_error_code": code}))
        assert result.root_cause == expected

    def test_evidence_records_the_code_that_drove_the_verdict(self):
        """The drill-down UI renders this; a verdict without evidence is not
        explainable."""
        result = classify(event(raw={"gateway_error_code": "BAD_REQUEST_CARD_EXPIRED"}))
        assert any("BAD_REQUEST_CARD_EXPIRED" in item for item in result.evidence)
        assert any("card_expired" in item for item in result.evidence)

    def test_evidence_is_never_empty(self):
        for event_type in EventType:
            assert classify(event(event_type)).evidence

    def test_an_unrecognised_code_falls_back_weakly(self):
        result = classify(event(raw={"gateway_error_code": "TOTALLY_MADE_UP_CODE"}))
        assert result.root_cause == FALLBACK_CAUSE[EventType.PAYMENT_DEGRADED]
        assert result.is_low_confidence
        assert any("unrecognised" in item for item in result.evidence)

    def test_a_code_illegal_for_the_event_type_is_surfaced_not_coerced(self):
        """A mandate code on a card event is a real inconsistency. Silently
        returning the mandate cause would produce an illegal diagnosis."""
        result = classify(
            event(raw={"gateway_error_code": "BAD_REQUEST_MANDATE_BANK_REJECTED"})
        )
        assert result.root_cause in ROOT_CAUSES_BY_EVENT_TYPE[EventType.PAYMENT_DEGRADED]
        assert result.is_low_confidence
        assert any("inconsistent" in item for item in result.evidence)


class TestAmbiguousSignals:
    def test_a_generic_code_produces_low_confidence(self):
        """Section 11 requires ~5% genuinely ambiguous events to land in the
        low-confidence bucket rather than be force-classified."""
        for code in AMBIGUOUS_ERROR_CODES:
            result = classify(event(raw={"gateway_error_code": code}))
            assert result.confidence == CONFIDENCE_AMBIGUOUS
            assert result.is_low_confidence

    def test_a_specific_code_is_not_low_confidence(self):
        result = classify(event(raw={"gateway_error_code": "BAD_REQUEST_CARD_EXPIRED"}))
        assert result.is_low_confidence is False

    def test_ambiguity_is_stated_in_the_evidence(self):
        result = classify(event(raw={"gateway_error_code": "BAD_REQUEST_PAYMENT_FAILED"}))
        assert any("no discriminating signal" in item for item in result.evidence)

    def test_checkout_ambiguity_resolves_to_unknown(self):
        """checkout_abandoned has an `unknown` cause for exactly this case."""
        result = classify(
            event(EventType.CHECKOUT_ABANDONED, {"gateway_error_code": "BAD_REQUEST_PAYMENT_FAILED"})
        )
        assert result.root_cause == RootCauseCode.UNKNOWN


class TestInvoiceDiagnosis:
    def test_explicit_dispute_marker_wins(self):
        result = classify(
            event(EventType.INVOICE_OVERDUE, {"dispute_raised": True, "days_overdue": 40})
        )
        assert result.root_cause == RootCauseCode.DISPUTED_AMOUNT
        assert result.confidence == CONFIDENCE_STRONG

    def test_explicit_approval_marker_wins(self):
        result = classify(
            event(EventType.INVOICE_OVERDUE, {"approval_pending": True, "days_overdue": 3})
        )
        assert result.root_cause == RootCauseCode.AWAITING_APPROVAL

    def test_explicit_delivery_marker_wins(self):
        result = classify(event(EventType.INVOICE_OVERDUE, {"delivery_failed": True}))
        assert result.root_cause == RootCauseCode.DELIVERY_FAILURE

    def test_a_promise_watcher_event_is_diagnosed_definitively(self):
        """Section 4: a broken promise raises a NEW event with this cause."""
        result = classify(
            event(EventType.INVOICE_OVERDUE, {"broken_promise_of_event_id": "evt_prior"})
        )
        assert result.root_cause == RootCauseCode.BROKEN_PTP
        assert result.confidence == CONFIDENCE_DEFINITIVE

    def test_recently_due_reads_as_forgotten(self):
        result = classify(event(EventType.INVOICE_OVERDUE, {"days_overdue": 3}))
        assert result.root_cause == RootCauseCode.FORGOTTEN

    def test_long_overdue_reads_as_cash_flow_but_admits_uncertainty(self):
        """A due date alone cannot separate these causes, and the engine says so
        rather than inflating confidence."""
        result = classify(event(EventType.INVOICE_OVERDUE, {"days_overdue": 60}))
        assert result.root_cause == RootCauseCode.CASH_FLOW_DELAY
        assert result.is_low_confidence
        assert any("cannot separate" in item for item in result.evidence)

    def test_b2b_in_the_middle_window_reads_as_awaiting_approval(self):
        result = classify(
            event(EventType.INVOICE_OVERDUE, {"days_overdue": 15, "channel": "b2b"})
        )
        assert result.root_cause == RootCauseCode.AWAITING_APPROVAL

    def test_retail_in_the_middle_window_does_not(self):
        result = classify(event(EventType.INVOICE_OVERDUE, {"days_overdue": 15}))
        assert result.root_cause == RootCauseCode.FORGOTTEN

    def test_a_missing_due_date_is_undetermined(self):
        result = classify(event(EventType.INVOICE_OVERDUE, {}))
        assert result.is_low_confidence


class TestCheckoutDiagnosis:
    def test_a_changed_cart_reads_as_price_shock(self):
        result = classify(event(EventType.CHECKOUT_ABANDONED, {"cart_value_changed": True}))
        assert result.root_cause == RootCauseCode.PRICE_SHOCK
        assert result.confidence == CONFIDENCE_STRONG

    def test_no_available_methods_reads_as_no_preferred_method(self):
        result = classify(event(EventType.CHECKOUT_ABANDONED, {"available_methods": 0}))
        assert result.root_cause == RootCauseCode.NO_PREFERRED_METHOD

    def test_no_signal_is_unknown_and_uncertain(self):
        result = classify(event(EventType.CHECKOUT_ABANDONED, {}))
        assert result.root_cause == RootCauseCode.UNKNOWN
        assert result.is_low_confidence


class TestCauseIsAlwaysLegalForTheEventType:
    """The invariant that keeps the rest of the pipeline sound."""

    @pytest.mark.parametrize("event_type", list(EventType))
    @pytest.mark.parametrize(
        "code",
        [
            "BAD_REQUEST_CARD_EXPIRED",
            "GATEWAY_ERROR_ISSUER_DECLINED",
            "BAD_REQUEST_MANDATE_BANK_REJECTED",
            "BAD_REQUEST_PAYMENT_FAILED",
            "UNRECOGNISED_CODE_XYZ",
            None,
        ],
    )
    def test_no_combination_yields_an_illegal_cause(self, event_type, code):
        raw = {"gateway_error_code": code} if code else {"days_overdue": 10}
        result = classify(event(event_type, raw))
        assert result.root_cause in ROOT_CAUSES_BY_EVENT_TYPE[event_type]

    def test_confidence_is_always_a_probability(self):
        for event_type in EventType:
            result = classify(event(event_type))
            assert 0.0 <= result.confidence <= 1.0

    def test_classification_is_deterministic(self):
        """Same signal, same verdict — the basis of a replayable audit trail."""
        raw = {"gateway_error_code": "BAD_REQUEST_CARD_EXPIRED"}
        first = classify(event(raw=raw))
        second = classify(event(raw=raw))
        assert (first.root_cause, first.confidence, first.evidence) == (
            second.root_cause,
            second.confidence,
            second.evidence,
        )

    def test_a_non_dict_raw_signal_does_not_crash(self):
        """Malformed records reach here; Section 9 requires they be handled."""
        result = classify(event(raw="payment failed"))
        assert result.root_cause in ROOT_CAUSES_BY_EVENT_TYPE[EventType.PAYMENT_DEGRADED]


class TestDiagnosisResultHelpers:
    def test_low_confidence_boundary(self):
        assert DiagnosisResult(RootCauseCode.FORGOTTEN, LOW_CONFIDENCE_THRESHOLD, []).is_low_confidence is False
        assert DiagnosisResult(
            RootCauseCode.FORGOTTEN, LOW_CONFIDENCE_THRESHOLD - 0.01, []
        ).is_low_confidence is True

    @pytest.mark.parametrize("cause", sorted(HARD_STOP_CAUSES, key=lambda c: c.value))
    def test_hard_stop_causes_report_themselves(self, cause):
        assert DiagnosisResult(cause, 0.95, []).is_hard_stop is True

    def test_a_soft_cause_is_not_a_hard_stop(self):
        assert SOFT.is_hard_stop is False


# --------------------------------------------------------------------------- #
# Section 6 intervention table
# --------------------------------------------------------------------------- #


class TestHardStopsShortCircuitTheTable:
    @pytest.mark.parametrize("cause", sorted(HARD_STOP_CAUSES, key=lambda c: c.value))
    @pytest.mark.parametrize("attempt", [1, 2, 3])
    def test_no_action_is_ever_offered(self, cause, attempt):
        """Section 6: "no retry, immediate stop"."""
        diagnosis = DiagnosisResult(cause, 0.95, [])
        for event_type in EventType:
            assert candidate_actions(event(event_type), diagnosis, attempt, BIG) == []


class TestPaymentDegradedRow:
    def test_attempt_one_offers_the_update_card_email(self):
        assert candidate_actions(event(), SOFT, 1) == [ActionCode.UPDATE_CARD_EMAIL]

    def test_attempt_two_offers_the_sms_reminder(self):
        assert candidate_actions(event(), SOFT, 2) == [ActionCode.SMS_REMINDER]

    def test_it_stops_after_two_attempts(self):
        """Section 6: "else after 2 attempts"."""
        assert candidate_actions(event(), SOFT, 3) == []
        assert candidate_actions(event(), SOFT, 9) == []

    def test_a_large_amount_adds_the_human_handoff(self):
        """Section 6: "Human handoff if amount > threshold"."""
        actions = candidate_actions(event(amount="500000.00"), SOFT, 1, Decimal("25000.00"))
        assert ActionCode.HUMAN_HANDOFF in actions

    def test_a_small_amount_does_not(self):
        """Without the condition a human would be offered on every event, and
        because a human converts better the scorer would escalate cases the
        table never intended to escalate."""
        actions = candidate_actions(event(amount="500.00"), SOFT, 1, Decimal("25000.00"))
        assert ActionCode.HUMAN_HANDOFF not in actions

    def test_no_threshold_given_means_no_handoff(self):
        assert ActionCode.HUMAN_HANDOFF not in candidate_actions(event(amount="500000.00"), SOFT, 1)


class TestCheckoutAbandonedRow:
    def test_attempt_one_is_the_in_app_nudge(self):
        diagnosis = DiagnosisResult(RootCauseCode.OTP_TIMEOUT, 0.9, [])
        assert candidate_actions(event(EventType.CHECKOUT_ABANDONED), diagnosis, 1) == [
            ActionCode.IN_APP_NUDGE
        ]

    def test_attempt_two_is_a_single_email(self):
        diagnosis = DiagnosisResult(RootCauseCode.OTP_TIMEOUT, 0.9, [])
        assert candidate_actions(event(EventType.CHECKOUT_ABANDONED), diagnosis, 2) == [
            ActionCode.EMAIL_SAVED_CART
        ]

    def test_it_stops_after_one_email(self):
        """Section 6: "after 1 email"."""
        diagnosis = DiagnosisResult(RootCauseCode.OTP_TIMEOUT, 0.9, [])
        assert candidate_actions(event(EventType.CHECKOUT_ABANDONED), diagnosis, 3) == []

    def test_it_never_escalates_even_for_a_huge_cart(self):
        """The escalation column for this row is "—"."""
        diagnosis = DiagnosisResult(RootCauseCode.OTP_TIMEOUT, 0.9, [])
        actions = candidate_actions(
            event(EventType.CHECKOUT_ABANDONED, amount="500000.00"),
            diagnosis,
            1,
            Decimal("1000.00"),
        )
        assert ActionCode.HUMAN_HANDOFF not in actions


class TestSubscriptionFailedRow:
    @pytest.mark.parametrize("attempt", [1, 2, 3, 10])
    def test_the_only_move_is_to_wait_for_the_gateway(self, attempt):
        """Section 6: "React to Razorpay's own auto-retry/webhook state — do not
        force extra retries"."""
        diagnosis = DiagnosisResult(RootCauseCode.INSUFFICIENT_FUNDS, 0.9, [])
        assert candidate_actions(
            event(EventType.SUBSCRIPTION_FAILED), diagnosis, attempt, BIG
        ) == [ActionCode.AWAIT_GATEWAY_AUTO_RETRY]

    def test_it_never_offers_a_forced_retry(self):
        diagnosis = DiagnosisResult(RootCauseCode.INSUFFICIENT_FUNDS, 0.9, [])
        for attempt in (1, 2, 3):
            actions = candidate_actions(
                event(EventType.SUBSCRIPTION_FAILED), diagnosis, attempt, BIG
            )
            assert ActionCode.RETRY_PAYMENT not in actions
            assert ActionCode.FINAL_RETRY not in actions

    def test_halted_is_a_hard_stop(self):
        """Section 6: hard stop "on `halted`"."""
        diagnosis = DiagnosisResult(RootCauseCode.HALTED_AFTER_MAX_RETRIES, 0.95, [])
        assert candidate_actions(event(EventType.SUBSCRIPTION_FAILED), diagnosis, 1, BIG) == []


class TestInvoiceOverdueRow:
    def test_under_seven_days_is_a_friendly_reminder(self):
        diagnosis = DiagnosisResult(RootCauseCode.FORGOTTEN, 0.9, [])
        assert candidate_actions(
            event(EventType.INVOICE_OVERDUE, {"days_overdue": 3}), diagnosis, 1, BIG
        ) == [ActionCode.FRIENDLY_REMINDER]

    def test_the_middle_window_adds_the_call_script(self):
        diagnosis = DiagnosisResult(RootCauseCode.FORGOTTEN, 0.9, [])
        actions = candidate_actions(
            event(EventType.INVOICE_OVERDUE, {"days_overdue": 15}), diagnosis, 1
        )
        assert actions == [ActionCode.REMINDER_WITH_CALL_SCRIPT]

    def test_the_call_script_is_escalation_l1(self):
        assert escalation_level_for(ActionCode.REMINDER_WITH_CALL_SCRIPT) == 1

    def test_past_thirty_days_offers_the_formal_notice_and_a_human(self):
        """Section 6 names human handoff outright in this row, so it is offered
        regardless of amount."""
        diagnosis = DiagnosisResult(RootCauseCode.CASH_FLOW_DELAY, 0.9, [])
        actions = candidate_actions(
            event(EventType.INVOICE_OVERDUE, {"days_overdue": 45}, amount="500.00"),
            diagnosis,
            1,
        )
        assert actions == [ActionCode.FORMAL_NOTICE, ActionCode.HUMAN_HANDOFF]

    def test_the_formal_notice_is_escalation_l2(self):
        """L2 is the ceiling: "never auto-escalate past L2"."""
        assert escalation_level_for(ActionCode.FORMAL_NOTICE) == 2

    def test_boundaries_are_inclusive_where_the_table_says_so(self):
        diagnosis = DiagnosisResult(RootCauseCode.FORGOTTEN, 0.9, [])
        at_seven = candidate_actions(
            event(EventType.INVOICE_OVERDUE, {"days_overdue": 7}), diagnosis, 1
        )
        at_thirty = candidate_actions(
            event(EventType.INVOICE_OVERDUE, {"days_overdue": 30}), diagnosis, 1
        )
        at_thirty_one = candidate_actions(
            event(EventType.INVOICE_OVERDUE, {"days_overdue": 31}), diagnosis, 1
        )
        assert ActionCode.REMINDER_WITH_CALL_SCRIPT in at_seven
        assert ActionCode.REMINDER_WITH_CALL_SCRIPT in at_thirty
        assert ActionCode.FORMAL_NOTICE in at_thirty_one


class TestMandateFailedRow:
    """Section 6: "Real sequence, not generic 2-attempt"."""

    def test_unauthenticated_starts_with_a_reauth_nudge(self):
        diagnosis = DiagnosisResult(RootCauseCode.NOT_AUTHENTICATED, 0.95, [])
        assert candidate_actions(event(EventType.MANDATE_FAILED), diagnosis, 1) == [
            ActionCode.REAUTH_NUDGE
        ]

    def test_insufficient_balance_starts_with_a_retry(self):
        diagnosis = DiagnosisResult(RootCauseCode.INSUFFICIENT_BALANCE, 0.95, [])
        assert candidate_actions(event(EventType.MANDATE_FAILED), diagnosis, 1) == [
            ActionCode.RETRY_PAYMENT
        ]

    def test_day_three_targets_the_salary_window_for_balance_problems(self):
        """The row calls for a retry "timed to likely salary-credit window"."""
        diagnosis = DiagnosisResult(RootCauseCode.INSUFFICIENT_BALANCE, 0.95, [])
        assert candidate_actions(event(EventType.MANDATE_FAILED), diagnosis, 2) == [
            ActionCode.RETRY_SALARY_WINDOW
        ]

    def test_day_three_is_a_plain_retry_for_other_causes(self):
        diagnosis = DiagnosisResult(RootCauseCode.NOT_AUTHENTICATED, 0.95, [])
        assert candidate_actions(event(EventType.MANDATE_FAILED), diagnosis, 2) == [
            ActionCode.RETRY_PAYMENT
        ]

    def test_day_seven_is_the_final_retry(self):
        diagnosis = DiagnosisResult(RootCauseCode.NOT_AUTHENTICATED, 0.95, [])
        assert candidate_actions(event(EventType.MANDATE_FAILED), diagnosis, 3) == [
            ActionCode.FINAL_RETRY
        ]

    def test_it_hard_stops_after_the_day_seven_attempt(self):
        diagnosis = DiagnosisResult(RootCauseCode.NOT_AUTHENTICATED, 0.95, [])
        assert candidate_actions(event(EventType.MANDATE_FAILED), diagnosis, 4, BIG) == []

    def test_the_sequence_differs_by_cause_not_just_by_attempt(self):
        """A generic 2-attempt implementation would produce identical sequences
        for both causes, which is exactly what the table forbids."""
        unauth = DiagnosisResult(RootCauseCode.NOT_AUTHENTICATED, 0.95, [])
        balance = DiagnosisResult(RootCauseCode.INSUFFICIENT_BALANCE, 0.95, [])
        unauth_seq = [candidate_actions(event(EventType.MANDATE_FAILED), unauth, n) for n in (1, 2)]
        balance_seq = [
            candidate_actions(event(EventType.MANDATE_FAILED), balance, n) for n in (1, 2)
        ]
        assert unauth_seq != balance_seq

    def test_bank_rejected_stops_immediately(self):
        """Section 6: "same logic as issuer_declined"."""
        diagnosis = DiagnosisResult(RootCauseCode.BANK_REJECTED, 0.95, [])
        assert candidate_actions(event(EventType.MANDATE_FAILED), diagnosis, 1, BIG) == []


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


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
def saved_event(db_session: Session) -> RiskEvent:
    db_session.add(Merchant(id="mer_d", name="Diagnosis Merchant"))
    db_session.add(CustomerProfile(customer_id="cust_d", payment_success_rate=0.8))
    db_session.flush()
    row = event(raw={"gateway_error_code": "BAD_REQUEST_CARD_EXPIRED"})
    db_session.add(row)
    db_session.flush()
    return row


class TestPersistence:
    def test_diagnose_writes_a_row(self, db_session, saved_event):
        row = diagnose(db_session, saved_event, now=T0)
        assert row.event_id == saved_event.id
        assert row.root_cause_code == RootCauseCode.CARD_EXPIRED
        assert row.confidence == CONFIDENCE_DEFINITIVE

    def test_evidence_is_persisted(self, db_session, saved_event):
        row = diagnose(db_session, saved_event, now=T0)
        db_session.commit()
        db_session.expire_all()
        reloaded = db_session.get(Diagnosis, saved_event.id)
        assert reloaded.evidence
        assert isinstance(reloaded.evidence, list)

    def test_diagnosing_twice_does_not_create_a_second_row(self, db_session, saved_event):
        """An event is diagnosed once; a replayed batch must not rewrite it."""
        diagnose(db_session, saved_event, now=T0)
        diagnose(db_session, saved_event, now=T0)
        db_session.commit()
        assert db_session.query(Diagnosis).count() == 1

    def test_an_existing_diagnosis_is_returned_unchanged(self, db_session, saved_event):
        first = diagnose(db_session, saved_event, now=T0)
        first_cause = first.root_cause_code
        saved_event.raw_signal = {"gateway_error_code": "GATEWAY_ERROR_TIMEOUT"}
        db_session.flush()
        second = diagnose(db_session, saved_event, now=T0)
        assert second.root_cause_code == first_cause
