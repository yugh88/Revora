"""POST /batch. BUILD_SPEC Sections 9, 10 and 11.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

The property that matters most here is that the metrics CANNOT BE FABRICATED.
Section 2's bar is "real numbers from ledger state, not invented", so the tests
recompute the money independently from the ORM and demand an exact match, and
they check internal consistency: recovered + lost + pending must equal the
amount at risk to the paisa, and recovered can never exceed at-risk.

That independent recomputation is not decoration. An earlier version of the
metrics code divided by 100 twice — ``func.sum`` on a Money column already
applies the paise-to-rupees conversion — and understated every amount by 100x
while leaving ``recovery_rate`` perfectly correct, because both sides of the
ratio scaled together. Only a test comparing absolute amounts against the ORM
catches that.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.enums import EventStatus, EventType, GatewayUsed, OutcomeResolution
from app.models import (
    Decision,
    MLDiagnosisPrediction,
    Outcome,
    PaymentAttempt,
    RiskEvent,
    StoppingRuleState,
)
from app.routers.batch import run_batch
from app.schemas.batch import DEFAULT_BATCH_SIZE, MAX_BATCH_SIZE, BatchRequest


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


@pytest.fixture(autouse=True)
def quiet_logs():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture(scope="module")
def _shared():
    """One 50-record run reused by the read-only assertions.

    Running the full pipeline 40 times would dominate the suite runtime and tell
    us nothing extra — these tests all inspect the same completed run.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    session = factory()
    logging.disable(logging.CRITICAL)
    response = run_batch(session, BatchRequest(count=50), load_ml=False)
    logging.disable(logging.NOTSET)
    yield session, response
    session.close()
    engine.dispose()


def orm_money(session: Session):
    """Recompute the money independently of the metrics code."""
    events = {e.id: e for e in session.execute(select(RiskEvent)).scalars()}
    outcomes = list(session.execute(select(Outcome)).scalars())
    at_risk = sum((e.amount for e in events.values()), Decimal("0.00"))
    recovered = sum((o.amount_recovered for o in outcomes), Decimal("0.00"))
    lost = sum(
        (events[o.event_id].amount for o in outcomes if o.resolved == OutcomeResolution.LOST),
        Decimal("0.00"),
    )
    pending = sum(
        (
            events[o.event_id].amount
            for o in outcomes
            if o.resolved == OutcomeResolution.PENDING
        ),
        Decimal("0.00"),
    )
    return at_risk, recovered, lost, pending


# --------------------------------------------------------------------------- #
# Request handling
# --------------------------------------------------------------------------- #


class TestBatchSizing:
    def test_the_default_is_fifty(self):
        """Section 10: "default 50, supports 500"."""
        assert DEFAULT_BATCH_SIZE == 50
        assert BatchRequest().count == 50

    def test_five_hundred_is_supported(self):
        assert MAX_BATCH_SIZE == 500
        assert BatchRequest(count=500).count == 500

    def test_a_custom_size_is_honoured(self, db_session):
        response = run_batch(db_session, BatchRequest(count=17), load_ml=False)
        assert response.total_records == 17
        assert (
            response.processed + response.isolated_failures + response.skipped_duplicates
            == 17
        )

    def test_an_oversized_batch_is_rejected(self):
        with pytest.raises(Exception):
            BatchRequest(count=5000)

    def test_a_zero_batch_is_rejected(self):
        with pytest.raises(Exception):
            BatchRequest(count=0)

    def test_the_default_gateway_is_the_simulator(self):
        """The path that must never fail during judging."""
        assert BatchRequest().gateway == GatewayUsed.LOCAL_SIMULATION

    def test_the_seed_is_fixed_at_42(self):
        """Section 11."""
        assert BatchRequest().seed == 42


class TestGatewaySelection:
    def test_razorpay_without_credentials_is_refused_not_downgraded(self, db_session):
        """Session 5 implemented the gateway, so the 501 became a 400.

        The property under test is unchanged and is the one that matters:
        selecting the sandbox without credentials must FAIL, never silently fall
        back to the simulator. A fallback would let the response claim sandbox
        numbers that never came from the sandbox.
        """
        from fastapi import HTTPException

        from app.config import Settings
        from app.routers import batch as batch_module

        with pytest.raises(HTTPException) as excinfo:
            batch_module.build_gateway(GatewayUsed.RAZORPAY_TEST)

        assert excinfo.value.status_code == 400
        assert "local_simulation" in str(excinfo.value.detail)

    def test_razorpay_selection_never_returns_the_simulator(self, db_session):
        """The failure mode this guards against is a silent downgrade."""
        from fastapi import HTTPException

        from app.gateways.local_simulation import LocalSimulationGateway
        from app.routers import batch as batch_module

        try:
            gateway = batch_module.build_gateway(GatewayUsed.RAZORPAY_TEST)
        except HTTPException:
            return  # refused, which is correct without credentials
        assert not isinstance(gateway, LocalSimulationGateway)

    def test_the_gateway_is_recorded_on_the_response(self, _shared):
        _, response = _shared
        assert response.gateway == GatewayUsed.LOCAL_SIMULATION


# --------------------------------------------------------------------------- #
# Metrics come from the ledger
# --------------------------------------------------------------------------- #


class TestMetricsAreDerivedFromLedgerState:
    def test_amount_at_risk_matches_the_events(self, _shared):
        session, response = _shared
        at_risk, _, _, _ = orm_money(session)
        assert Decimal(response.money.amount_at_risk) == at_risk

    def test_amount_recovered_matches_the_ledger(self, _shared):
        session, response = _shared
        _, recovered, _, _ = orm_money(session)
        assert Decimal(response.money.amount_recovered) == recovered

    def test_amount_lost_matches_the_ledger(self, _shared):
        session, response = _shared
        _, _, lost, _ = orm_money(session)
        assert Decimal(response.money.amount_lost) == lost

    def test_amount_pending_matches_the_ledger(self, _shared):
        session, response = _shared
        _, _, _, pending = orm_money(session)
        assert Decimal(response.money.amount_pending) == pending

    def test_the_money_balances_exactly(self, _shared):
        """recovered + lost + pending == at risk, to the paisa. A gap means an
        event settled without a ledger row."""
        session, response = _shared
        at_risk, recovered, lost, pending = orm_money(session)
        assert recovered + lost + pending == at_risk

    def test_recovered_never_exceeds_at_risk(self, _shared):
        _, response = _shared
        assert Decimal(response.money.amount_recovered) <= Decimal(
            response.money.amount_at_risk
        )

    def test_recovery_rate_is_the_ratio_of_those_two(self, _shared):
        _, response = _shared
        at_risk = Decimal(response.money.amount_at_risk)
        recovered = Decimal(response.money.amount_recovered)
        expected = float(recovered / at_risk) if at_risk > 0 else 0.0
        assert response.recovery_rate == pytest.approx(expected, abs=1e-4)

    def test_rates_are_within_range(self, _shared):
        _, response = _shared
        assert 0.0 <= response.recovery_rate <= 1.0
        assert 0.0 <= response.resolution_rate <= 1.0

    def test_money_is_serialised_as_strings(self, _shared):
        """Decimal money as a JSON number would become a float in the frontend."""
        _, response = _shared
        for field in ("amount_at_risk", "amount_recovered", "amount_lost"):
            assert isinstance(getattr(response.money, field), str)
            Decimal(getattr(response.money, field))

    def test_every_processed_event_has_a_ledger_row(self, _shared):
        """Without this the money could balance simply by omitting events."""
        session, response = _shared
        events = {e.id for e in session.execute(select(RiskEvent)).scalars()}
        ledger = {o.event_id for o in session.execute(select(Outcome)).scalars()}
        assert events == ledger

    def test_amount_attempted_covers_only_events_with_an_attempt(self, _shared):
        session, response = _shared
        attempted_ids = {
            a.event_id for a in session.execute(select(PaymentAttempt)).scalars()
        }
        events = {e.id: e for e in session.execute(select(RiskEvent)).scalars()}
        expected = sum((events[i].amount for i in attempted_ids), Decimal("0.00"))
        assert Decimal(response.money.amount_attempted) == expected

    def test_counts_match_the_stored_rows(self, _shared):
        session, response = _shared
        assert response.processed == len(
            list(session.execute(select(RiskEvent)).scalars())
        )
        assert response.audit_entries > 0


class TestNotEverythingResolves:
    def test_the_resolution_rate_is_not_one_hundred_percent(self, _shared):
        """Section 11: "a 100% resolution rate on the batch is a red flag, not a
        win"."""
        _, response = _shared
        assert response.resolution_rate < 1.0

    def test_the_recovery_rate_is_not_one_hundred_percent(self, _shared):
        _, response = _shared
        assert response.recovery_rate < 1.0

    def test_some_money_is_actually_recovered(self, _shared):
        """The opposite failure: a pipeline that recovers nothing is broken too."""
        _, response = _shared
        assert Decimal(response.money.amount_recovered) > 0

    def test_outcomes_are_mixed(self, _shared):
        _, response = _shared
        assert len(response.outcome_breakdown) >= 2

    def test_several_statuses_are_reached(self, _shared):
        _, response = _shared
        assert len(response.status_breakdown) >= 3


# --------------------------------------------------------------------------- #
# Section 10 breakdowns
# --------------------------------------------------------------------------- #


class TestSectionTenBreakdowns:
    def test_stopping_rule_triggers_match_persisted_state(self, _shared):
        """The counts must come from StoppingRuleState, not a loop counter."""
        session, response = _shared
        reasons: dict[str, int] = {}
        for state in session.execute(select(StoppingRuleState)).scalars():
            if state.hard_stop_reason:
                reasons[state.hard_stop_reason] = reasons.get(state.hard_stop_reason, 0) + 1
        triggers = response.stopping_rule_triggers
        assert triggers.total == sum(reasons.values())

    def test_the_four_named_reasons_exist_as_fields(self, _shared):
        """Section 10 names cooldown, do_not_contact, max_attempts, hard_decline."""
        _, response = _shared
        triggers = response.stopping_rule_triggers
        for field in ("cooldown", "do_not_contact", "max_attempts", "hard_decline"):
            assert isinstance(getattr(triggers, field), int)

    def test_stopping_rules_actually_fire(self, _shared):
        """Section 2's bar: "stopping rules that actually stop things"."""
        _, response = _shared
        assert response.stopping_rule_triggers.total > 0

    def test_hard_declines_are_stopped(self, _shared):
        """Section 11 injects ~10% hard declines and Section 6 forbids retrying
        them."""
        _, response = _shared
        assert response.stopping_rule_triggers.hard_decline > 0

    def test_a_hard_declined_event_is_never_retried(self, _shared):
        """The enforcement, not just the count: no gateway execution may exist
        for an event stopped on a hard cause."""
        session, _ = _shared
        hard_ids = {
            s.event_id
            for s in session.execute(select(StoppingRuleState)).scalars()
            if s.hard_stop_reason == "hard_stop_cause"
        }
        for event_id in hard_ids:
            attempts = list(
                session.execute(
                    select(PaymentAttempt).where(PaymentAttempt.event_id == event_id)
                ).scalars()
            )
            assert attempts == []

    def test_escalation_ceiling_hits_are_counted(self, _shared):
        _, response = _shared
        assert response.escalation_ceiling_hits >= 0

    def test_no_event_escalates_past_l2(self, _shared):
        """Section 6: "never auto-escalate past L2"."""
        session, _ = _shared
        for state in session.execute(select(StoppingRuleState)).scalars():
            assert state.escalation_level <= 2

    def test_promise_counts_come_from_the_table(self, _shared):
        """Currently zero: promise_tracker is a later session. Reporting a real
        zero is correct; inventing a number would not be."""
        session, response = _shared
        from app.models import PromiseToPay

        actual = len(list(session.execute(select(PromiseToPay)).scalars()))
        assert response.promises_made == actual
        assert response.promises_kept <= response.promises_made
        assert response.promises_broken <= response.promises_made

    def test_event_type_breakdown_covers_only_the_five_types(self, _shared):
        _, response = _shared
        valid = {t.value for t in EventType}
        assert set(response.event_type_breakdown) <= valid

    def test_action_breakdown_matches_the_decisions(self, _shared):
        session, response = _shared
        actual: dict[str, int] = {}
        for decision in session.execute(select(Decision)).scalars():
            actual[decision.action_code] = actual.get(decision.action_code, 0) + 1
        assert response.action_breakdown == actual


# --------------------------------------------------------------------------- #
# ML through the batch — Section 4a
# --------------------------------------------------------------------------- #


class TestMLThroughBatch:
    def test_ml_unavailable_does_not_break_the_batch(self, db_session):
        """Section 4a: never a dependency the core loop can be broken by."""
        response = run_batch(db_session, BatchRequest(count=25), load_ml=False)
        assert response.processed > 0
        assert response.ml_agreement_rate is None
        assert response.ml_predictions == 0

    def test_an_absent_opinion_is_not_counted_as_disagreement(self, db_session):
        """A rate of 0.0 would say the model was wrong every time. None says it
        had no opinion, which is the truth."""
        response = run_batch(db_session, BatchRequest(count=25), load_ml=False)
        assert response.ml_agreement_rate is not None or response.ml_predictions == 0
        assert response.ml_unavailable == response.processed

    def test_an_exploding_classifier_does_not_break_the_batch(self, db_session):
        class Exploding:
            model_version = "boom"

            def predict(self, features):
                raise RuntimeError("model exploded")

        response = run_batch(
            db_session, BatchRequest(count=25), classifier=Exploding()
        )
        assert response.processed > 0
        assert response.ml_predictions == 0

    def test_the_agreement_rate_matches_the_stored_predictions(self, db_session):
        from app.enums import RootCauseCode
        from app.ml.diagnosis_classifier import MLPrediction

        class Agreeing:
            model_version = "stub-v1"

            def predict(self, features):
                return MLPrediction(RootCauseCode.CARD_EXPIRED, 0.95, "stub-v1")

        response = run_batch(db_session, BatchRequest(count=30), classifier=Agreeing())
        rows = list(db_session.execute(select(MLDiagnosisPrediction)).scalars())
        agreements = sum(1 for r in rows if r.agrees_with_rule_engine)
        assert response.ml_predictions == len(rows)
        assert response.ml_agreements == agreements
        if rows:
            assert response.ml_agreement_rate == pytest.approx(
                agreements / len(rows), abs=1e-4
            )

    def test_ml_does_not_change_the_actions_taken(self, db_session):
        """Rule authority, verified through the batch path rather than only in
        the engine unit tests."""
        from app.enums import RootCauseCode
        from app.ml.diagnosis_classifier import MLPrediction

        class Disagreeing:
            model_version = "stub-dis"

            def predict(self, features):
                return MLPrediction(RootCauseCode.NETWORK_TIMEOUT, 0.99, "stub-dis")

        without = run_batch(db_session, BatchRequest(count=30), load_ml=False)

        engine2 = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, future=True
        )
        Base.metadata.create_all(bind=engine2)
        session2 = sessionmaker(bind=engine2, expire_on_commit=False, future=True)()
        with_ml = run_batch(
            session2, BatchRequest(count=30), classifier=Disagreeing()
        )
        session2.close()
        engine2.dispose()

        assert without.action_breakdown == with_ml.action_breakdown
        assert without.money.amount_recovered == with_ml.money.amount_recovered


# --------------------------------------------------------------------------- #
# Idempotency and locking through the batch
# --------------------------------------------------------------------------- #


class TestIdempotencyThroughBatch:
    def test_each_event_has_at_most_one_attempt_per_action(self, _shared):
        session, _ = _shared
        seen: set[tuple[str, int]] = set()
        for attempt in session.execute(select(PaymentAttempt)).scalars():
            key = (attempt.event_id, attempt.attempt_number)
            assert key not in seen
            seen.add(key)

    def test_idempotency_keys_are_unique(self, _shared):
        session, _ = _shared
        keys = [
            a.idempotency_key for a in session.execute(select(PaymentAttempt)).scalars()
        ]
        assert len(keys) == len(set(keys))

    def test_a_colliding_attempt_number_does_not_execute_twice(self, db_session, monkeypatch):
        """Section 9: "return existing result if found, never re-execute".

        In a sequential batch next_attempt_number always advances, so the guard
        never fires and mutation testing showed it could be deleted unnoticed.
        The real scenario is two workers computing the SAME attempt number
        concurrently — one wins the UNIQUE constraint on idempotency_key and the
        other must return the winner's result. Pinning the number reproduces
        exactly that collision.
        """
        from app.engine import idempotency
        from app.engine.decision_engine import decide
        from app.models import AuditLog, CustomerProfile, Merchant
        from app.routers import batch as batch_module

        db_session.add(Merchant(id="mer_i", name="Idem"))
        db_session.add(CustomerProfile(customer_id="cust_i", payment_success_rate=0.8))
        db_session.flush()
        event = RiskEvent(
            id="evt_idem",
            type=EventType.PAYMENT_DEGRADED,
            merchant_id="mer_i",
            customer_id="cust_i",
            amount=Decimal("2499.00"),
            source_ref="pay_idem",
            raw_signal={"gateway_error_code": "BAD_REQUEST_CARD_EXPIRED"},
            correlation_id="corr_idem",
        )
        db_session.add(event)
        db_session.flush()

        gateway = batch_module.build_gateway(GatewayUsed.LOCAL_SIMULATION)
        from app.database import utcnow

        now = utcnow()

        outcome = decide(db_session, event, load_ml=False, now=now)
        batch_module.execute_decision(
            db_session, event, outcome, gateway=gateway, seed=42, now=now
        )
        first = list(
            db_session.execute(
                select(PaymentAttempt).where(PaymentAttempt.event_id == event.id)
            ).scalars()
        )
        assert len(first) == 1

        # Second worker computes the SAME attempt number.
        monkeypatch.setattr(idempotency, "next_attempt_number", lambda s, e: 1)
        monkeypatch.setattr(batch_module.idempotency, "next_attempt_number", lambda s, e: 1)
        batch_module.execute_decision(
            db_session, event, outcome, gateway=gateway, seed=42, now=now
        )

        after = list(
            db_session.execute(
                select(PaymentAttempt).where(PaymentAttempt.event_id == event.id)
            ).scalars()
        )
        assert len(after) == 1, "a second execution was recorded for the same key"
        assert after[0].id == first[0].id

        skipped = [
            row
            for row in db_session.execute(select(AuditLog)).scalars()
            if row.action == "execution_skipped_idempotent"
        ]
        assert skipped, "the skipped replay was not audited"

    def test_locks_are_released_after_the_batch(self, _shared):
        """A leaked lock would block the event on the next run."""
        from app.models import ActionLock

        session, _ = _shared
        assert list(session.execute(select(ActionLock)).scalars()) == []

    def test_rerunning_the_same_seed_creates_no_duplicate_events(self, db_session):
        """The generator is deterministic, so a second run produces the same
        event ids — every one of which must be recognised as a duplicate."""
        first = run_batch(db_session, BatchRequest(count=30), load_ml=False)
        second = run_batch(db_session, BatchRequest(count=30), load_ml=False)
        assert second.processed == 0
        assert second.skipped_duplicates >= first.processed


class TestRecoveredExternally:
    """Section 9's race-condition re-check, end to end.

    Section 11 marks ~10% of records as already settled upstream. The engine
    must discover that by re-checking BEFORE acting, record the money as
    recovered, and take no action.

    This path exposed a real defect during Session 4: the re-check runs after
    diagnosis, so the event is in `diagnosing`, and the Session 1 state machine
    had no `diagnosing -> recovered` edge. Every externally-settled event raised
    InvalidTransition and was isolated as a failure instead of settling. The
    edge was added; these tests stop it regressing.
    """

    def test_externally_settled_events_are_recovered_without_acting(self, _shared):
        session, _ = _shared
        from app.models import AuditLog

        external = [
            row
            for row in session.execute(select(AuditLog)).scalars()
            if row.action == "recovered_externally"
        ]
        assert external, "no externally-resolved events in this batch"

        for entry in external:
            event = session.get(RiskEvent, entry.event_id)
            assert event.status == EventStatus.RECOVERED
            attempts = list(
                session.execute(
                    select(PaymentAttempt).where(PaymentAttempt.event_id == event.id)
                ).scalars()
            )
            assert attempts == [], "the engine acted on an already-settled event"

    def test_the_external_recovery_is_credited_to_the_external_channel(self, _shared):
        """Money recovered by the customer paying on their own must not be
        presented as money the engine recovered."""
        from app.enums import Channel
        from app.models import AuditLog

        session, _ = _shared
        external_ids = {
            row.event_id
            for row in session.execute(select(AuditLog)).scalars()
            if row.action == "recovered_externally"
        }
        for event_id in external_ids:
            outcome = session.get(Outcome, event_id)
            assert outcome.resolved == OutcomeResolution.RECOVERED
            assert outcome.resolution_channel == Channel.EXTERNAL

    def test_the_state_machine_permits_the_section_9_path(self):
        """The specific edge the defect was missing."""
        from app.engine.state_machine import can_transition

        assert can_transition(EventStatus.DIAGNOSING, EventStatus.RECOVERED)

    def test_externally_settled_events_are_not_isolated_failures(self, _shared):
        """The symptom of the original defect."""
        _, response = _shared
        assert not any(
            "diagnosing -> recovered" in f.error_message for f in response.failures
        )


class TestReproducibility:
    def test_two_runs_of_the_same_seed_agree(self):
        """Section 11's fixed seed has to survive the whole pipeline, not just
        the generator."""
        results = []
        for _ in range(2):
            engine = create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, future=True
            )
            Base.metadata.create_all(bind=engine)
            session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
            response = run_batch(session, BatchRequest(count=40), load_ml=False)
            results.append(
                (
                    response.processed,
                    response.money.amount_at_risk,
                    response.money.amount_recovered,
                    response.action_breakdown,
                    response.status_breakdown,
                )
            )
            session.close()
            engine.dispose()
        assert results[0] == results[1]

    def test_a_different_seed_produces_different_data(self):
        outputs = []
        for seed in (42, 99):
            engine = create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, future=True
            )
            Base.metadata.create_all(bind=engine)
            session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
            outputs.append(
                run_batch(
                    session, BatchRequest(count=40, seed=seed), load_ml=False
                ).money.amount_at_risk
            )
            session.close()
            engine.dispose()
        assert outputs[0] != outputs[1]
