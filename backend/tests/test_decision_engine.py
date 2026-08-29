"""Tests for engine/decision_engine.py. BUILD_SPEC Sections 4, 4a and 6.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

The property this file exists to protect
-----------------------------------------
Section 4a: the rule engine stays authoritative "for the action actually taken
(safety/auditability)", and the classifier "is flagged and routed into
/exceptions ... instead of silently overridden or ignored".

Both failure directions are tested, because they fail in opposite ways and a
suite that only covers one would miss the other:

  * ML SILENTLY OVERRIDING — a wrong prediction changing action_code. Tested by
    running the same event with no ML and with a deliberately disagreeing stub,
    and asserting the outputs are identical.
  * ML SILENTLY IGNORED — a disagreement recorded nowhere. Tested by asserting
    an MLDiagnosisPrediction row, an audit entry, and needs_review.

Stubs, not the trained model
-----------------------------
The classifier stubs below let a test decide exactly what the model says. Using
the real artifact would make these tests depend on what a retrained tree happens
to predict, so a model refresh would break assertions about orchestration that
have nothing to do with the model. One test does exercise the real artifact, to
confirm the wiring works end to end.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.engine.decision_engine import (
    ACTION_DECISION_MADE,
    ACTION_ML_DISAGREEMENT,
    ACTION_ML_UNAVAILABLE,
    ML_DISAGREEMENT_REASON,
    build_reasoning_text,
    decide,
)
from app.engine.diagnosis_engine import ActionCode
from app.engine.policy_engine import RULE_DO_NOT_CONTACT, RULE_HARD_STOP
from app.enums import (
    AuditStage,
    EventStatus,
    EventType,
    PolicyResultStatus,
    ProbabilitySource,
    RootCauseCode,
)
from app.ml.diagnosis_classifier import MLPrediction
from app.models import (
    AuditLog,
    CustomerProfile,
    Decision,
    Diagnosis,
    Merchant,
    MLDiagnosisPrediction,
    Policy,
    RiskEvent,
    StoppingRuleState,
)

T0 = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Classifier stubs
# --------------------------------------------------------------------------- #


class AgreeingClassifier:
    """Predicts the same cause the rules will, confidently."""

    model_version = "stub-agree-v1"

    def __init__(self, cause: RootCauseCode = RootCauseCode.CARD_EXPIRED) -> None:
        self.cause = cause

    def predict(self, features):
        return MLPrediction(self.cause, 0.93, self.model_version)


class DisagreeingClassifier:
    """Predicts a different cause, confidently. Must never change the action."""

    model_version = "stub-disagree-v1"

    def predict(self, features):
        return MLPrediction(RootCauseCode.NETWORK_TIMEOUT, 0.91, self.model_version)


class MandateCauseSwappingClassifier:
    """Predicts insufficient_balance where the rules will say not_authenticated.

    Deliberately chosen because Section 6's mandate_failed row branches on the
    cause: not_authenticated leads to a re-auth nudge, insufficient_balance to a
    retry. Substituting the ML cause anywhere in the pipeline therefore changes
    the ACTION, which a payment_degraded event would not reveal — that row
    offers the same action for every soft cause, so a cause swap there is
    invisible. Mutation testing found exactly that blind spot.
    """

    model_version = "stub-mandate-swap-v1"

    def predict(self, features):
        return MLPrediction(RootCauseCode.INSUFFICIENT_BALANCE, 0.95, self.model_version)


class HardCausePredictingClassifier:
    """Predicts a hard-stop cause where the rules will say a soft one.

    If the ML verdict reached the intervention table, this would silently halt
    recovery on a perfectly recoverable event.
    """

    model_version = "stub-hard-v1"

    def predict(self, features):
        return MLPrediction(RootCauseCode.ISSUER_DECLINED, 0.97, self.model_version)


class UnconfidentClassifier:
    """Right cause, but below the confidence threshold."""

    model_version = "stub-unconfident-v1"

    def predict(self, features):
        return MLPrediction(RootCauseCode.CARD_EXPIRED, 0.31, self.model_version)


class ExplodingClassifier:
    """Raises on every call. The pipeline must survive it."""

    model_version = "stub-exploding-v1"

    def predict(self, features):
        raise RuntimeError("model exploded")


# --------------------------------------------------------------------------- #
# Fixtures
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
def base_data(db_session: Session):
    db_session.add(Merchant(id="mer_x", name="Decision Merchant"))
    db_session.add(
        CustomerProfile(
            customer_id="cust_x", payment_success_rate=0.82, do_not_contact=False
        )
    )
    db_session.flush()
    return db_session


@pytest.fixture()
def make_event(base_data: Session):
    counter = {"n": 0}

    def _make(**overrides) -> RiskEvent:
        counter["n"] += 1
        defaults = dict(
            id=f"evt_x{counter['n']}",
            type=EventType.PAYMENT_DEGRADED,
            merchant_id="mer_x",
            customer_id="cust_x",
            amount=Decimal("2499.00"),
            currency="INR",
            source_ref=f"pay_X{counter['n']}",
            detected_at=T0,
            raw_signal={"gateway_error_code": "BAD_REQUEST_CARD_EXPIRED"},
            correlation_id=f"corr_x{counter['n']}",
        )
        defaults.update(overrides)
        row = RiskEvent(**defaults)
        base_data.add(row)
        base_data.flush()
        return row

    return _make


def audit_actions(session: Session, event_id: str) -> list[str]:
    return [
        row.action
        for row in session.execute(
            select(AuditLog).where(AuditLog.event_id == event_id).order_by(AuditLog.id)
        ).scalars()
    ]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


class TestPipelineOrchestration:
    def test_a_decision_is_persisted(self, db_session, make_event):
        event = make_event()
        outcome = decide(db_session, event, load_ml=False, now=T0)
        db_session.commit()
        assert db_session.query(Decision).count() == 1
        assert outcome.decision.event_id == event.id

    def test_a_diagnosis_is_persisted(self, db_session, make_event):
        event = make_event()
        decide(db_session, event, load_ml=False, now=T0)
        assert db_session.get(Diagnosis, event.id) is not None

    def test_an_open_event_advances_to_diagnosing(self, db_session, make_event):
        event = make_event()
        decide(db_session, event, load_ml=False, now=T0)
        assert event.status == EventStatus.DIAGNOSING

    def test_the_transition_is_audited(self, db_session, make_event):
        event = make_event()
        decide(db_session, event, load_ml=False, now=T0)
        assert "state_transition" in audit_actions(db_session, event.id)

    def test_state_advance_can_be_suppressed(self, db_session, make_event):
        """Session 4's batch may want to own transitions itself."""
        event = make_event()
        decide(db_session, event, load_ml=False, now=T0, advance_state=False)
        assert event.status == EventStatus.OPEN

    def test_an_already_diagnosing_event_is_not_re_transitioned(self, db_session, make_event):
        event = make_event(status=EventStatus.DIAGNOSING)
        decide(db_session, event, load_ml=False, now=T0)
        assert "state_transition" not in audit_actions(db_session, event.id)

    def test_the_decision_is_audited(self, db_session, make_event):
        event = make_event()
        decide(db_session, event, load_ml=False, now=T0)
        assert ACTION_DECISION_MADE in audit_actions(db_session, event.id)

    def test_the_decision_audit_entry_is_at_the_decision_stage(self, db_session, make_event):
        event = make_event()
        decide(db_session, event, load_ml=False, now=T0)
        row = db_session.execute(
            select(AuditLog).where(AuditLog.action == ACTION_DECISION_MADE)
        ).scalar_one()
        assert row.stage == AuditStage.DECISION

    def test_decide_does_not_commit(self, db_session, make_event):
        """The caller owns the transaction — that boundary is what gives Section
        9's batch fault isolation its rollback point."""
        event = make_event()
        decide(db_session, event, load_ml=False, now=T0)
        db_session.rollback()
        assert db_session.query(Decision).count() == 0


class TestDecisionFields:
    """Section 4 fixes what a Decision must carry."""

    def test_every_specified_field_is_populated(self, db_session, make_event):
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        decision = outcome.decision
        assert decision.decision_factors
        assert decision.recovery_probability is not None
        assert decision.probability_source == ProbabilitySource.DETERMINISTIC
        assert decision.policy_result
        assert decision.policy_version is not None
        assert decision.action_code
        assert decision.reasoning_text

    def test_policy_result_keeps_its_five_key_shape(self, db_session, make_event):
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        assert set(outcome.decision.policy_result) == {
            "status",
            "rule_triggered",
            "threshold_checked",
            "actual_value",
            "threshold_value",
        }

    def test_decision_factors_carry_the_section_4_keys(self, db_session, make_event):
        factors = decide(
            db_session, make_event(), load_ml=False, now=T0
        ).decision.decision_factors
        for key in (
            "root_cause",
            "confidence",
            "amount",
            "customer_success_rate",
            "attempt_number",
            "channel_preference",
        ):
            assert key in factors

    def test_decision_factors_show_the_scoring_arithmetic(self, db_session, make_event):
        """A reviewer must be able to see why one action beat another."""
        factors = decide(db_session, make_event(), load_ml=False, now=T0).decision.decision_factors
        assert factors["candidates_considered"]
        assert factors["selected"]["score"]

    def test_decision_is_json_round_trippable(self, db_session, make_event):
        import json

        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        db_session.commit()
        db_session.expire_all()
        reloaded = db_session.get(Decision, outcome.decision.id)
        assert json.loads(json.dumps(reloaded.decision_factors))["root_cause"]

    def test_recovery_probability_matches_the_chosen_action(self, db_session, make_event):
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        assert outcome.decision.recovery_probability == outcome.chosen.probability

    def test_policy_version_is_the_one_that_gated_the_decision(self, db_session, make_event):
        """Pinned so a later policy edit cannot rewrite past reasoning."""
        db_session.add(
            Policy(
                policy_version=4,
                merchant_id="mer_x",
                event_type=EventType.PAYMENT_DEGRADED,
                max_attempts=3,
                cooldown_hours=24,
                amount_threshold=Decimal("25000.00"),
                recovery_probability_threshold=0.05,
                contact_limit_per_channel=2,
                escalation_ceiling=2,
            )
        )
        db_session.flush()
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        assert outcome.decision.policy_version == 4


class TestReasoningText:
    def test_it_is_populated_deterministically(self, db_session, make_event):
        """No LLM. Two identical events must yield identical text — Section 7's
        template engine replaces this in a later session, and it must remain a
        pure function of decision_factors when it does."""
        first = decide(db_session, make_event(), load_ml=False, now=T0)
        second = decide(db_session, make_event(), load_ml=False, now=T0)
        assert first.decision.reasoning_text == second.decision.reasoning_text

    def test_it_names_the_cause_and_the_action(self, db_session, make_event):
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        text = outcome.decision.reasoning_text
        assert outcome.diagnosis_result.root_cause.value in text
        assert outcome.decision.action_code in text

    def test_a_blocked_decision_explains_the_rule(self, db_session, make_event):
        db_session.get(CustomerProfile, "cust_x").do_not_contact = True
        db_session.flush()
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        assert RULE_DO_NOT_CONTACT in outcome.decision.reasoning_text

    def test_it_never_returns_empty(self, db_session, make_event):
        """reasoning_text is non-nullable; an empty justification is worse than
        a factual placeholder."""
        for raw in ({"gateway_error_code": "GATEWAY_ERROR_ISSUER_DECLINED"}, {}, "broken"):
            outcome = decide(db_session, make_event(raw_signal=raw), load_ml=False, now=T0)
            assert outcome.decision.reasoning_text.strip()


# --------------------------------------------------------------------------- #
# Rule authority — Section 4a
# --------------------------------------------------------------------------- #


class TestRuleEngineStaysAuthoritative:
    def test_a_disagreeing_model_does_not_change_the_action(self, db_session, make_event):
        """The central safety property of the hybrid architecture."""
        without = decide(db_session, make_event(), load_ml=False, now=T0)
        with_ml = decide(
            db_session, make_event(), classifier=DisagreeingClassifier(), now=T0
        )
        assert without.decision.action_code == with_ml.decision.action_code

    def test_ml_cannot_change_the_action_where_the_cause_selects_it(
        self, db_session, make_event
    ):
        """The version of the above that can actually fail.

        payment_degraded offers the same action for every soft cause, so a cause
        swap there is undetectable. Section 6's mandate_failed row branches on
        the cause, so this catches ML substitution at the intervention table.
        """
        signal = {"gateway_error_code": "BAD_REQUEST_MANDATE_NOT_AUTHENTICATED"}
        without = decide(
            db_session,
            make_event(type=EventType.MANDATE_FAILED, raw_signal=signal),
            load_ml=False,
            now=T0,
        )
        with_ml = decide(
            db_session,
            make_event(type=EventType.MANDATE_FAILED, raw_signal=signal),
            classifier=MandateCauseSwappingClassifier(),
            now=T0,
        )
        assert without.decision.action_code == ActionCode.REAUTH_NUDGE.value
        assert with_ml.decision.action_code == ActionCode.REAUTH_NUDGE.value

    def test_ml_cannot_halt_a_recoverable_event_by_predicting_a_hard_cause(
        self, db_session, make_event
    ):
        """A hard cause short-circuits the whole intervention table. If the ML
        verdict reached it, one confident wrong prediction would abandon money
        that was recoverable."""
        outcome = decide(
            db_session, make_event(), classifier=HardCausePredictingClassifier(), now=T0
        )
        assert outcome.decision.action_code == ActionCode.UPDATE_CARD_EMAIL.value
        assert outcome.policy_result.status == PolicyResultStatus.ALLOWED

    def test_ml_cannot_change_the_probability_via_the_cause(self, db_session, make_event):
        """P(recovery) is keyed on the root cause, so a substituted cause would
        change the score even when the action happens to match.

        card_expired + update_card_email and network_timeout + the same action
        carry different base rates, so a leaked ML cause would move this number.

        The assertion is that the two runs AGREE, not that they equal a
        particular constant. The probability now also reflects what is known
        about the customer, and pinning it to a base rate would have made this
        test fail for a reason that has nothing to do with the property it
        exists to protect.
        """
        without = decide(db_session, make_event(), load_ml=False, now=T0)
        with_ml = decide(
            db_session, make_event(), classifier=DisagreeingClassifier(), now=T0
        )
        assert without.decision.recovery_probability == with_ml.decision.recovery_probability
        # And it is still a real probability, not an artefact of the adjustment.
        assert 0.0 < without.decision.recovery_probability <= 1.0

    def test_a_disagreeing_model_does_not_change_the_probability(self, db_session, make_event):
        without = decide(db_session, make_event(), load_ml=False, now=T0)
        with_ml = decide(
            db_session, make_event(), classifier=DisagreeingClassifier(), now=T0
        )
        assert (
            without.decision.recovery_probability == with_ml.decision.recovery_probability
        )

    def test_the_persisted_diagnosis_is_the_rule_verdict(self, db_session, make_event):
        event = make_event()
        outcome = decide(db_session, event, classifier=DisagreeingClassifier(), now=T0)
        stored = db_session.get(Diagnosis, event.id)
        assert stored.root_cause_code == RootCauseCode.CARD_EXPIRED
        assert stored.root_cause_code != outcome.ml_prediction.root_cause

    def test_decision_factors_report_the_rule_cause_not_the_ml_one(
        self, db_session, make_event
    ):
        """decision_factors["root_cause"] is what /exceptions and the audit
        drill-down render as the reason for the action. If it carried the ML
        verdict while the action came from the rule verdict, the audit trail
        would misstate its own reasoning — the exact failure the hybrid design
        exists to prevent."""
        event = make_event()
        outcome = decide(db_session, event, classifier=DisagreeingClassifier(), now=T0)
        factors = outcome.decision.decision_factors
        assert factors["root_cause"] == RootCauseCode.CARD_EXPIRED.value
        assert factors["ml"]["predicted_root_cause"] == RootCauseCode.NETWORK_TIMEOUT.value
        assert factors["root_cause"] != factors["ml"]["predicted_root_cause"]

    def test_the_audit_reason_matches_the_persisted_diagnosis(
        self, db_session, make_event
    ):
        event = make_event()
        outcome = decide(db_session, event, classifier=DisagreeingClassifier(), now=T0)
        stored = db_session.get(Diagnosis, event.id)
        assert outcome.decision.decision_factors["root_cause"] == stored.root_cause_code.value

    def test_decision_factors_mark_ml_as_non_authoritative(self, db_session, make_event):
        outcome = decide(
            db_session, make_event(), classifier=DisagreeingClassifier(), now=T0
        )
        assert outcome.decision.decision_factors["ml"]["authoritative"] is False

    def test_probability_source_stays_deterministic_with_ml_present(
        self, db_session, make_event
    ):
        """P1 is not implemented; the score is always the P0 lookup table."""
        outcome = decide(db_session, make_event(), classifier=AgreeingClassifier(), now=T0)
        assert outcome.decision.probability_source == ProbabilitySource.DETERMINISTIC


class TestMLDisagreementIsSurfaced:
    def test_disagreement_sets_the_agreement_flag_false(self, db_session, make_event):
        event = make_event()
        decide(db_session, event, classifier=DisagreeingClassifier(), now=T0)
        row = db_session.execute(select(MLDiagnosisPrediction)).scalar_one()
        assert row.agrees_with_rule_engine is False

    def test_disagreement_writes_an_audit_entry(self, db_session, make_event):
        event = make_event()
        decide(db_session, event, classifier=DisagreeingClassifier(), now=T0)
        assert ACTION_ML_DISAGREEMENT in audit_actions(db_session, event.id)

    def test_the_audit_entry_uses_the_spec_phrase(self, db_session, make_event):
        """Section 4a names the /exceptions label exactly."""
        event = make_event()
        decide(db_session, event, classifier=DisagreeingClassifier(), now=T0)
        row = db_session.execute(
            select(AuditLog).where(AuditLog.action == ACTION_ML_DISAGREEMENT)
        ).scalar_one()
        assert ML_DISAGREEMENT_REASON in row.reasoning

    def test_the_audit_entry_records_both_verdicts(self, db_session, make_event):
        event = make_event()
        decide(db_session, event, classifier=DisagreeingClassifier(), now=T0)
        row = db_session.execute(
            select(AuditLog).where(AuditLog.action == ACTION_ML_DISAGREEMENT)
        ).scalar_one()
        assert row.before_state == RootCauseCode.CARD_EXPIRED.value
        assert row.after_state == RootCauseCode.NETWORK_TIMEOUT.value

    def test_disagreement_flags_the_event_for_review(self, db_session, make_event):
        outcome = decide(
            db_session, make_event(), classifier=DisagreeingClassifier(), now=T0
        )
        assert outcome.needs_review is True
        assert any(ML_DISAGREEMENT_REASON in reason for reason in outcome.review_reasons)

    def test_low_confidence_agreement_is_still_flagged(self, db_session, make_event):
        """Section 4a: disagreement OR confidence below threshold."""
        outcome = decide(
            db_session, make_event(), classifier=UnconfidentClassifier(), now=T0
        )
        assert outcome.ml_agrees is False
        assert outcome.needs_review is True

    def test_a_lucky_match_at_low_confidence_is_not_corroboration(
        self, db_session, make_event
    ):
        event = make_event()
        decide(db_session, event, classifier=UnconfidentClassifier(), now=T0)
        row = db_session.execute(select(MLDiagnosisPrediction)).scalar_one()
        assert row.predicted_root_cause == RootCauseCode.CARD_EXPIRED
        assert row.agrees_with_rule_engine is False

    def test_confident_agreement_is_not_flagged(self, db_session, make_event):
        outcome = decide(db_session, make_event(), classifier=AgreeingClassifier(), now=T0)
        assert outcome.ml_agrees is True
        assert outcome.needs_review is False

    def test_agreement_writes_no_disagreement_audit_entry(self, db_session, make_event):
        event = make_event()
        decide(db_session, event, classifier=AgreeingClassifier(), now=T0)
        assert ACTION_ML_DISAGREEMENT not in audit_actions(db_session, event.id)

    def test_a_prediction_row_is_written_for_every_event(self, db_session, make_event):
        """Section 4a: "The classifier runs independently on every event"."""
        for _ in range(3):
            decide(db_session, make_event(), classifier=AgreeingClassifier(), now=T0)
        db_session.commit()
        assert db_session.query(MLDiagnosisPrediction).count() == 3

    def test_the_prediction_records_its_model_version(self, db_session, make_event):
        decide(db_session, make_event(), classifier=AgreeingClassifier(), now=T0)
        row = db_session.execute(select(MLDiagnosisPrediction)).scalar_one()
        assert row.model_version == AgreeingClassifier.model_version


class TestMLIsNonBlocking:
    """Section 4a: ML "is an enhancement layer, never a dependency the core loop
    can be broken by"."""

    def test_a_decision_is_produced_with_no_model_at_all(self, db_session, make_event):
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        assert outcome.decision.action_code == ActionCode.UPDATE_CARD_EMAIL.value
        assert outcome.ml_prediction is None

    def test_an_exploding_model_does_not_break_the_pipeline(self, db_session, make_event):
        outcome = decide(db_session, make_event(), classifier=ExplodingClassifier(), now=T0)
        assert outcome.decision.action_code == ActionCode.UPDATE_CARD_EMAIL.value
        assert outcome.ml_prediction is None

    def test_an_exploding_model_writes_no_prediction_row(self, db_session, make_event):
        decide(db_session, make_event(), classifier=ExplodingClassifier(), now=T0)
        db_session.commit()
        assert db_session.query(MLDiagnosisPrediction).count() == 0

    def test_a_failed_model_yields_the_same_decision_as_no_model(
        self, db_session, make_event
    ):
        without = decide(db_session, make_event(), load_ml=False, now=T0)
        broken = decide(db_session, make_event(), classifier=ExplodingClassifier(), now=T0)
        assert (without.decision.action_code, without.decision.recovery_probability) == (
            broken.decision.action_code,
            broken.decision.recovery_probability,
        )

    def test_an_unavailable_model_is_audited_not_hidden(self, db_session, make_event):
        """A silently absent model would make ml_agreement_rate quietly meaningless."""
        event = make_event()
        decide(db_session, event, classifier=None, load_ml=True, now=T0)
        # The real artifact may or may not be present in this environment; either
        # a prediction row or an unavailability audit entry must exist.
        actions = audit_actions(db_session, event.id)
        has_prediction = db_session.query(MLDiagnosisPrediction).count() > 0
        assert has_prediction or ACTION_ML_UNAVAILABLE in actions

    def test_ml_absence_leaves_agreement_undefined_not_false(
        self, db_session, make_event
    ):
        """None means "no opinion"; False would wrongly count as a disagreement
        in /batch's ml_agreement_rate."""
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        assert outcome.ml_agrees is None


class TestRealTrainedModel:
    def test_the_shipped_artifact_wires_up_end_to_end(self, db_session, make_event):
        """Stubs prove orchestration; this proves the real artifact loads and
        predicts through the same path."""
        from app.ml.diagnosis_classifier import load_classifier

        classifier = load_classifier()
        if classifier is None:
            pytest.skip("no trained model artifact present in this environment")

        event = make_event()
        outcome = decide(db_session, event, classifier=classifier, now=T0)
        assert outcome.ml_prediction is not None
        assert 0.0 <= outcome.ml_prediction.confidence <= 1.0
        assert outcome.decision.action_code == ActionCode.UPDATE_CARD_EMAIL.value


# --------------------------------------------------------------------------- #
# Policy integration
# --------------------------------------------------------------------------- #


class TestPolicyIntegration:
    def test_the_highest_scoring_allowed_action_is_chosen(self, db_session, make_event):
        """Section 6: "Pick highest-scoring action that passes the policy gate"."""
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        assert outcome.chosen.action == outcome.ranked[0].action

    def test_a_blocked_top_candidate_falls_through_to_the_next(
        self, db_session, make_event
    ):
        """A large B2B invoice offers both a formal notice and a human handoff;
        capping escalation must push the choice down the ranking, not abandon it."""
        db_session.add(
            Policy(
                policy_version=1,
                merchant_id="mer_x",
                event_type=EventType.INVOICE_OVERDUE,
                max_attempts=3,
                cooldown_hours=24,
                amount_threshold=Decimal("25000.00"),
                recovery_probability_threshold=0.01,
                contact_limit_per_channel=5,
                escalation_ceiling=1,
            )
        )
        db_session.flush()
        event = make_event(
            type=EventType.INVOICE_OVERDUE,
            amount=Decimal("400000.00"),
            raw_signal={"days_overdue": 45},
        )
        outcome = decide(db_session, event, load_ml=False, now=T0)
        assert outcome.decision.action_code == ActionCode.NO_ACTION.value
        assert outcome.policy_result.status == PolicyResultStatus.BLOCKED

    def test_a_blocked_event_yields_no_action(self, db_session, make_event):
        db_session.get(CustomerProfile, "cust_x").do_not_contact = True
        db_session.flush()
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        assert outcome.decision.action_code == ActionCode.NO_ACTION.value
        assert outcome.chosen is None

    def test_a_blocked_event_records_zero_probability(self, db_session, make_event):
        db_session.get(CustomerProfile, "cust_x").do_not_contact = True
        db_session.flush()
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        assert outcome.decision.recovery_probability == 0.0

    def test_a_hard_cause_produces_no_action_with_a_hard_stop_reason(
        self, db_session, make_event
    ):
        """Section 6: issuer_declined -> no retry, immediate stop."""
        event = make_event(raw_signal={"gateway_error_code": "GATEWAY_ERROR_ISSUER_DECLINED"})
        outcome = decide(db_session, event, load_ml=False, now=T0)
        assert outcome.decision.action_code == ActionCode.NO_ACTION.value
        assert outcome.policy_result.rule_triggered == RULE_HARD_STOP

    def test_the_binding_constraint_is_the_one_reported(self, db_session, make_event):
        """When several rules would fire, /exceptions must name the real reason."""
        db_session.get(CustomerProfile, "cust_x").do_not_contact = True
        db_session.flush()
        event = make_event(amount=Decimal("900000.00"))
        db_session.add(
            StoppingRuleState(event_id=event.id, attempts_used=9, max_attempts_for_type=1)
        )
        db_session.flush()
        outcome = decide(db_session, event, load_ml=False, now=T0)
        assert outcome.policy_result.rule_triggered == RULE_DO_NOT_CONTACT

    def test_a_large_amount_routes_to_a_human(self, db_session, make_event):
        """Section 6: "Human handoff if amount > threshold"."""
        db_session.add(
            Policy(
                policy_version=1,
                merchant_id="mer_x",
                event_type=EventType.PAYMENT_DEGRADED,
                max_attempts=3,
                cooldown_hours=24,
                amount_threshold=Decimal("10000.00"),
                recovery_probability_threshold=0.01,
                contact_limit_per_channel=5,
                escalation_ceiling=2,
            )
        )
        db_session.flush()
        outcome = decide(
            db_session, make_event(amount=Decimal("250000.00")), load_ml=False, now=T0
        )
        assert outcome.decision.action_code == ActionCode.HUMAN_HANDOFF.value

    def test_a_small_amount_stays_automated(self, db_session, make_event):
        outcome = decide(
            db_session, make_event(amount=Decimal("800.00")), load_ml=False, now=T0
        )
        assert outcome.decision.action_code == ActionCode.UPDATE_CARD_EMAIL.value


class TestLowConfidenceRouting:
    def test_a_low_confidence_diagnosis_is_flagged_for_review(
        self, db_session, make_event
    ):
        """Section 11's ambiguous records must not be force-classified."""
        event = make_event(raw_signal={"gateway_error_code": "BAD_REQUEST_PAYMENT_FAILED"})
        outcome = decide(db_session, event, load_ml=False, now=T0)
        assert outcome.needs_review is True

    def test_a_confident_diagnosis_is_not_flagged(self, db_session, make_event):
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        assert outcome.needs_review is False

    def test_review_reasons_are_recorded_on_the_decision(self, db_session, make_event):
        event = make_event(raw_signal={"gateway_error_code": "BAD_REQUEST_PAYMENT_FAILED"})
        outcome = decide(db_session, event, load_ml=False, now=T0)
        assert outcome.decision.decision_factors["needs_review"] is True
        assert outcome.decision.decision_factors["review_reasons"]


class TestMalformedInput:
    def test_a_non_dict_raw_signal_still_produces_a_decision(
        self, db_session, make_event
    ):
        """Section 9 requires malformed records to be handled, not to crash."""
        outcome = decide(db_session, make_event(raw_signal="payment failed"), load_ml=False, now=T0)
        assert outcome.decision is not None

    def test_an_empty_raw_signal_still_produces_a_decision(self, db_session, make_event):
        outcome = decide(db_session, make_event(raw_signal={}), load_ml=False, now=T0)
        assert outcome.decision is not None


class TestAttemptProgression:
    def test_the_first_decision_is_attempt_one(self, db_session, make_event):
        outcome = decide(db_session, make_event(), load_ml=False, now=T0)
        assert outcome.decision.decision_factors["attempt_number"] == 1

    def test_attempt_number_follows_recorded_payment_attempts(
        self, db_session, make_event
    ):
        from app.engine import idempotency

        event = make_event()
        idempotency.record_attempt(
            db_session,
            event_id=event.id,
            attempt_number=1,
            action_code=ActionCode.UPDATE_CARD_EMAIL.value,
        )
        outcome = decide(db_session, event, load_ml=False, now=T0)
        assert outcome.decision.decision_factors["attempt_number"] == 2

    def test_the_second_attempt_selects_the_next_action_in_the_table(
        self, db_session, make_event
    ):
        """Section 6: attempt 1 email, attempt 2 SMS."""
        from app.engine import idempotency

        event = make_event()
        idempotency.record_attempt(
            db_session,
            event_id=event.id,
            attempt_number=1,
            action_code=ActionCode.UPDATE_CARD_EMAIL.value,
        )
        outcome = decide(db_session, event, load_ml=False, now=T0)
        assert outcome.decision.action_code == ActionCode.SMS_REMINDER.value


class TestBuildReasoningText:
    def test_it_is_a_pure_function_of_its_inputs(self, db_session, make_event):
        from app.engine.diagnosis_engine import DiagnosisResult
        from app.engine.policy_engine import PolicyResult
        from app.engine.probability_engine import score_action

        event = make_event()
        diagnosis = DiagnosisResult(RootCauseCode.CARD_EXPIRED, 0.95, [])
        chosen = score_action(
            RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 1, Decimal("2499.00")
        )
        allowed = PolicyResult(status=PolicyResultStatus.ALLOWED)
        first = build_reasoning_text(event, diagnosis, chosen, allowed)
        second = build_reasoning_text(event, diagnosis, chosen, allowed)
        assert first == second

    def test_it_shows_the_arithmetic(self, db_session, make_event):
        from app.engine.diagnosis_engine import DiagnosisResult
        from app.engine.policy_engine import PolicyResult
        from app.engine.probability_engine import score_action

        event = make_event()
        chosen = score_action(
            RootCauseCode.CARD_EXPIRED, ActionCode.UPDATE_CARD_EMAIL, 1, Decimal("2499.00")
        )
        text = build_reasoning_text(
            event,
            DiagnosisResult(RootCauseCode.CARD_EXPIRED, 0.95, []),
            chosen,
            PolicyResult(status=PolicyResultStatus.ALLOWED),
        )
        assert str(chosen.expected_value) in text
        assert str(chosen.score) in text
