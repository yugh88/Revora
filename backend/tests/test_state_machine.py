"""Tests for engine/state_machine.py. BUILD_SPEC Sections 8 and 4.

Run from the backend/ directory so that ``app`` is importable:

    cd backend && python -m pytest -v

Fixtures live in this module rather than a conftest.py because conftest.py is
not yet in the Section 12 manifest. Sessions 4+ add three more test files that
will want the same fixtures; that is the point to propose adding a conftest.py
to the manifest rather than copy-pasting these.
"""

from __future__ import annotations

from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, utcnow
from app.enums import (
    AuditActor,
    AuditStage,
    Channel,
    EventStatus,
    EventType,
    GatewayUsed,
)
from app.engine.state_machine import (
    ACTION_INVALID_TRANSITION,
    ACTION_STATE_TRANSITION,
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransition,
    TerminalStateViolation,
    allowed_next_states,
    can_transition,
    is_terminal,
    reachable_states,
    transition,
    transition_many,
    validate_transition,
)
from app.models import AuditLog, CustomerProfile, Merchant, RiskEvent

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db_session():
    """A fresh in-memory SQLite database per test."""
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
def merchant(db_session: Session) -> Merchant:
    row = Merchant(id="mer_test", name="Test Merchant Pvt Ltd")
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture()
def customer(db_session: Session) -> CustomerProfile:
    row = CustomerProfile(
        customer_id="cust_test",
        payment_success_rate=0.82,
        payment_failure_rate=0.18,
        lifetime_value=Decimal("45000.00"),
        avg_payment_delay_days=3.5,
        preferred_channel=Channel.SMS,
        do_not_contact=False,
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture()
def make_event(db_session: Session, merchant: Merchant, customer: CustomerProfile):
    """Factory for RiskEvents at an arbitrary starting status."""

    counter = {"n": 0}

    def _make(status: EventStatus = EventStatus.OPEN, **overrides) -> RiskEvent:
        counter["n"] += 1
        defaults = dict(
            id=f"evt_test_{counter['n']}",
            type=EventType.PAYMENT_DEGRADED,
            merchant_id=merchant.id,
            customer_id=customer.customer_id,
            amount=Decimal("2499.00"),
            currency="INR",
            source_ref=f"pay_TEST{counter['n']}",
            detected_at=utcnow(),
            raw_signal={"gateway_error_code": "BAD_REQUEST_CARD_EXPIRED"},
            status=status,
            gateway_used=GatewayUsed.LOCAL_SIMULATION,
            correlation_id=f"corr_test_{counter['n']}",
        )
        defaults.update(overrides)
        row = RiskEvent(**defaults)
        db_session.add(row)
        db_session.flush()
        return row

    return _make


def audit_rows(session: Session, event_id: str) -> list[AuditLog]:
    return list(
        session.execute(
            select(AuditLog).where(AuditLog.event_id == event_id).order_by(AuditLog.id)
        ).scalars()
    )


# --------------------------------------------------------------------------- #
# Graph shape — pure, no database
# --------------------------------------------------------------------------- #


class TestTransitionGraph:
    def test_every_status_has_an_entry(self):
        """A status with no declared edges is a bug, not an empty set by accident."""
        assert set(ALLOWED_TRANSITIONS) == set(EventStatus)

    def test_terminal_states_are_exactly_the_two_named_in_spec(self):
        assert TERMINAL_STATES == {EventStatus.RECOVERED, EventStatus.UNRECOVERABLE}

    @pytest.mark.parametrize("status", sorted(TERMINAL_STATES, key=lambda s: s.value))
    def test_terminal_states_have_no_outbound_edges(self, status: EventStatus):
        assert ALLOWED_TRANSITIONS[status] == frozenset()
        assert is_terminal(status) is True

    @pytest.mark.parametrize(
        "status",
        sorted(set(EventStatus) - TERMINAL_STATES, key=lambda s: s.value),
    )
    def test_non_terminal_states_are_not_terminal(self, status: EventStatus):
        assert is_terminal(status) is False
        assert ALLOWED_TRANSITIONS[status], f"{status.value} is a dead end but not terminal"

    def test_every_status_is_reachable_from_open(self):
        """No orphaned states hiding in the schema."""
        assert reachable_states(EventStatus.OPEN) == set(EventStatus)

    def test_spec_happy_path_is_walkable(self):
        """Section 8's stated path, edge by edge."""
        assert can_transition(EventStatus.OPEN, EventStatus.DIAGNOSING)
        assert can_transition(EventStatus.DIAGNOSING, EventStatus.INTERVENING)
        for terminal in (
            EventStatus.RECOVERED,
            EventStatus.ESCALATED,
            EventStatus.UNRECOVERABLE,
            EventStatus.STOPPED,
        ):
            assert can_transition(EventStatus.INTERVENING, terminal)

    def test_second_attempt_keeps_event_intervening(self):
        """Section 6 defines attempt 1 and attempt 2 without leaving intervening."""
        assert can_transition(EventStatus.INTERVENING, EventStatus.INTERVENING)

    def test_open_cannot_self_transition(self):
        assert can_transition(EventStatus.OPEN, EventStatus.OPEN) is False

    def test_no_status_can_return_to_open(self):
        """Detection happens once; nothing re-opens an event (a broken PTP raises
        a NEW event instead — Section 4)."""
        for status, targets in ALLOWED_TRANSITIONS.items():
            assert EventStatus.OPEN not in targets, f"{status.value} -> open must not exist"

    def test_cannot_skip_diagnosis_and_intervene(self):
        assert can_transition(EventStatus.OPEN, EventStatus.INTERVENING) is False

    def test_escalated_and_stopped_are_not_terminal(self):
        """Section 8 names only two terminal states; a human outcome after
        escalation, and Section 9's recovered_externally after a stop, both need
        to be recordable."""
        assert is_terminal(EventStatus.ESCALATED) is False
        assert is_terminal(EventStatus.STOPPED) is False
        assert can_transition(EventStatus.ESCALATED, EventStatus.RECOVERED)
        assert can_transition(EventStatus.STOPPED, EventStatus.RECOVERED)

    def test_allowed_next_states_accepts_wire_values(self):
        assert allowed_next_states("open") == ALLOWED_TRANSITIONS[EventStatus.OPEN]

    def test_unknown_status_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown event status"):
            can_transition("open", "vibing")


class TestValidateTransition:
    def test_valid_edge_returns_none(self):
        assert validate_transition(EventStatus.OPEN, EventStatus.DIAGNOSING) is None

    def test_invalid_edge_raises_invalid_transition(self):
        with pytest.raises(InvalidTransition) as excinfo:
            validate_transition(EventStatus.OPEN, EventStatus.INTERVENING)
        assert excinfo.value.from_status == EventStatus.OPEN
        assert excinfo.value.to_status == EventStatus.INTERVENING

    @pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES, key=lambda s: s.value))
    def test_leaving_terminal_raises_the_specific_subclass(self, terminal: EventStatus):
        with pytest.raises(TerminalStateViolation):
            validate_transition(terminal, EventStatus.INTERVENING)

    def test_terminal_violation_is_an_invalid_transition(self):
        """Callers that only care about 'illegal' can catch the base class."""
        assert issubclass(TerminalStateViolation, InvalidTransition)

    def test_error_message_carries_event_id(self):
        with pytest.raises(InvalidTransition, match="evt_123"):
            validate_transition(
                EventStatus.RECOVERED, EventStatus.INTERVENING, event_id="evt_123"
            )


# --------------------------------------------------------------------------- #
# Audited transitions — with database
# --------------------------------------------------------------------------- #


class TestAuditedTransition:
    def test_successful_transition_updates_status(self, db_session, make_event):
        event = make_event(EventStatus.OPEN)
        transition(db_session, event, EventStatus.DIAGNOSING, reasoning="starting diagnosis")
        assert event.status == EventStatus.DIAGNOSING

    def test_successful_transition_writes_one_audit_row(self, db_session, make_event):
        event = make_event(EventStatus.OPEN)
        transition(db_session, event, EventStatus.DIAGNOSING, reasoning="starting diagnosis")

        rows = audit_rows(db_session, event.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.action == ACTION_STATE_TRANSITION
        assert row.before_state == "open"
        assert row.after_state == "diagnosing"
        assert row.stage == AuditStage.DIAGNOSIS
        assert row.actor == AuditActor.SYSTEM
        assert row.reasoning == "starting diagnosis"

    def test_audit_row_inherits_event_correlation_id(self, db_session, make_event):
        event = make_event(EventStatus.OPEN, correlation_id="corr_abc123")
        transition(db_session, event, EventStatus.DIAGNOSING, reasoning="x")
        assert audit_rows(db_session, event.id)[0].correlation_id == "corr_abc123"

    def test_correlation_id_can_be_overridden(self, db_session, make_event):
        event = make_event(EventStatus.OPEN, correlation_id="corr_event")
        transition(
            db_session, event, EventStatus.DIAGNOSING, reasoning="x", correlation_id="corr_batch"
        )
        assert audit_rows(db_session, event.id)[0].correlation_id == "corr_batch"

    def test_stage_defaults_map_to_pipeline_stages(self, db_session, make_event):
        cases = [
            (EventStatus.OPEN, EventStatus.DIAGNOSING, AuditStage.DIAGNOSIS),
            (EventStatus.DIAGNOSING, EventStatus.INTERVENING, AuditStage.EXECUTION),
            (EventStatus.INTERVENING, EventStatus.RECOVERED, AuditStage.RECOVERY),
            (EventStatus.INTERVENING, EventStatus.ESCALATED, AuditStage.ESCALATION),
            (EventStatus.INTERVENING, EventStatus.STOPPED, AuditStage.POLICY),
            (EventStatus.INTERVENING, EventStatus.UNRECOVERABLE, AuditStage.VERIFICATION),
        ]
        for start, target, expected_stage in cases:
            event = make_event(start)
            transition(db_session, event, target, reasoning="stage check")
            assert audit_rows(db_session, event.id)[0].stage == expected_stage

    def test_stage_can_be_overridden(self, db_session, make_event):
        event = make_event(EventStatus.DIAGNOSING)
        transition(
            db_session,
            event,
            EventStatus.STOPPED,
            reasoning="hard decline",
            stage=AuditStage.DIAGNOSIS,
        )
        assert audit_rows(db_session, event.id)[0].stage == AuditStage.DIAGNOSIS

    def test_human_actor_is_recorded(self, db_session, make_event):
        event = make_event(EventStatus.ESCALATED)
        transition(
            db_session,
            event,
            EventStatus.RECOVERED,
            reasoning="collections agent confirmed NEFT receipt",
            actor=AuditActor.HUMAN,
        )
        assert audit_rows(db_session, event.id)[0].actor == AuditActor.HUMAN

    def test_full_happy_path_leaves_an_ordered_trail(self, db_session, make_event):
        event = make_event(EventStatus.OPEN)
        transition(db_session, event, EventStatus.DIAGNOSING, reasoning="diagnose")
        transition(db_session, event, EventStatus.INTERVENING, reasoning="attempt 1")
        transition(db_session, event, EventStatus.INTERVENING, reasoning="attempt 2")
        transition(db_session, event, EventStatus.RECOVERED, reasoning="payment captured")

        trail = [(r.before_state, r.after_state) for r in audit_rows(db_session, event.id)]
        assert trail == [
            ("open", "diagnosing"),
            ("diagnosing", "intervening"),
            ("intervening", "intervening"),
            ("intervening", "recovered"),
        ]
        assert event.status == EventStatus.RECOVERED

    def test_accepts_wire_value_target(self, db_session, make_event):
        event = make_event(EventStatus.OPEN)
        transition(db_session, event, "diagnosing", reasoning="string target")
        assert event.status == EventStatus.DIAGNOSING


class TestRejectedTransitionsAreLoggedAnomalies:
    """Section 8: rejected AND logged. Both halves are required."""

    def test_invalid_transition_raises(self, db_session, make_event):
        event = make_event(EventStatus.OPEN)
        with pytest.raises(InvalidTransition):
            transition(db_session, event, EventStatus.INTERVENING, reasoning="skip diagnosis")

    def test_invalid_transition_leaves_status_unchanged(self, db_session, make_event):
        event = make_event(EventStatus.OPEN)
        with pytest.raises(InvalidTransition):
            transition(db_session, event, EventStatus.INTERVENING, reasoning="skip diagnosis")
        assert event.status == EventStatus.OPEN

    def test_invalid_transition_writes_anomaly_row(self, db_session, make_event):
        event = make_event(EventStatus.OPEN)
        with pytest.raises(InvalidTransition):
            transition(db_session, event, EventStatus.INTERVENING, reasoning="skip diagnosis")

        rows = audit_rows(db_session, event.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.action == ACTION_INVALID_TRANSITION
        assert row.before_state == "open"
        assert row.after_state == "open", "after_state must show the status did NOT change"
        assert "ANOMALY" in (row.reasoning or "")
        assert "intervening" in (row.reasoning or "")

    @pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES, key=lambda s: s.value))
    def test_cannot_transition_out_of_terminal_state(self, db_session, make_event, terminal):
        event = make_event(terminal)
        with pytest.raises(TerminalStateViolation):
            transition(db_session, event, EventStatus.INTERVENING, reasoning="reopen it")
        assert event.status == terminal

    @pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES, key=lambda s: s.value))
    def test_terminal_violation_is_audited(self, db_session, make_event, terminal):
        event = make_event(terminal)
        with pytest.raises(TerminalStateViolation):
            transition(db_session, event, EventStatus.INTERVENING, reasoning="reopen it")

        rows = audit_rows(db_session, event.id)
        assert len(rows) == 1
        assert rows[0].action == ACTION_INVALID_TRANSITION
        assert "terminal state" in (rows[0].reasoning or "")

    def test_recovered_cannot_be_marked_unrecoverable(self, db_session, make_event):
        """The specific contradiction that would corrupt the recovery ledger."""
        event = make_event(EventStatus.RECOVERED)
        with pytest.raises(TerminalStateViolation):
            transition(db_session, event, EventStatus.UNRECOVERABLE, reasoning="late reversal")
        assert event.status == EventStatus.RECOVERED

    def test_anomaly_survives_commit(self, db_session, make_event):
        """Committing in the exception handler persists the anomaly (see module
        docstring on transaction boundaries)."""
        event = make_event(EventStatus.RECOVERED)
        try:
            transition(db_session, event, EventStatus.INTERVENING, reasoning="reopen")
        except TerminalStateViolation:
            db_session.commit()

        db_session.expire_all()
        assert len(audit_rows(db_session, event.id)) == 1


class TestTransitionMany:
    def test_one_bad_record_does_not_stop_the_others(self, db_session, make_event):
        """Section 9 fault isolation, at the state-machine level."""
        good_a = make_event(EventStatus.DIAGNOSING)
        bad = make_event(EventStatus.RECOVERED)
        good_b = make_event(EventStatus.DIAGNOSING)

        succeeded, failures = transition_many(
            db_session, [good_a, bad, good_b], EventStatus.INTERVENING, reasoning="batch attempt"
        )

        assert [e.id for e in succeeded] == [good_a.id, good_b.id]
        assert len(failures) == 1
        failed_event, error = failures[0]
        assert failed_event.id == bad.id
        assert isinstance(error, TerminalStateViolation)

        assert good_a.status == EventStatus.INTERVENING
        assert good_b.status == EventStatus.INTERVENING
        assert bad.status == EventStatus.RECOVERED

    def test_failures_are_audited_too(self, db_session, make_event):
        bad = make_event(EventStatus.UNRECOVERABLE)
        transition_many(db_session, [bad], EventStatus.RECOVERED, reasoning="batch attempt")
        assert audit_rows(db_session, bad.id)[0].action == ACTION_INVALID_TRANSITION


# --------------------------------------------------------------------------- #
# Supporting model guarantees the state machine depends on
# --------------------------------------------------------------------------- #


class TestAuditLogIsAppendOnly:
    """Section 4 calls the audit log immutable; the state machine's anomaly
    record is worthless if it can be edited afterwards."""

    def test_existing_entry_cannot_be_modified(self, db_session, make_event):
        from app.models import ImmutableAuditLogError

        event = make_event(EventStatus.OPEN)
        transition(db_session, event, EventStatus.DIAGNOSING, reasoning="original")
        db_session.commit()

        row = audit_rows(db_session, event.id)[0]
        row.reasoning = "rewritten history"
        with pytest.raises(ImmutableAuditLogError):
            db_session.flush()
        db_session.rollback()

    def test_existing_entry_cannot_be_deleted(self, db_session, make_event):
        from app.models import ImmutableAuditLogError

        event = make_event(EventStatus.OPEN)
        transition(db_session, event, EventStatus.DIAGNOSING, reasoning="original")
        db_session.commit()

        db_session.delete(audit_rows(db_session, event.id)[0])
        with pytest.raises(ImmutableAuditLogError):
            db_session.flush()
        db_session.rollback()


class TestStorageInvariants:
    """Guarantees from database.py that the rest of the build will lean on."""

    def test_status_is_stored_as_wire_value_not_member_name(self, db_session, make_event):
        from sqlalchemy import text

        event = make_event(EventStatus.OPEN)
        transition(db_session, event, EventStatus.DIAGNOSING, reasoning="x")
        db_session.commit()

        stored = db_session.execute(
            text("SELECT status FROM risk_events WHERE id = :id"), {"id": event.id}
        ).scalar_one()
        assert stored == "diagnosing"

    def test_database_rejects_a_status_outside_the_vocabulary(self, db_session, make_event):
        """The enum CHECK constraint must actually exist.

        Regression test: SQLAlchemy's Enum has defaulted create_constraint=False
        since 1.4, so this silently passed until sa_enum set it explicitly. A raw
        UPDATE must not be able to park an event in a status the state machine
        has no edges for.
        """
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        event = make_event(EventStatus.OPEN)
        db_session.commit()

        with pytest.raises(IntegrityError):
            db_session.execute(
                text("UPDATE risk_events SET status = 'vibing' WHERE id = :id"),
                {"id": event.id},
            )
            db_session.commit()
        db_session.rollback()

    def test_money_survives_a_round_trip_exactly(self, db_session, make_event):
        event = make_event(EventStatus.OPEN, amount=Decimal("19999.99"))
        db_session.commit()
        db_session.expire_all()
        reloaded = db_session.get(RiskEvent, event.id)
        assert reloaded.amount == Decimal("19999.99")

    def test_datetimes_come_back_timezone_aware(self, db_session, make_event):
        event = make_event(EventStatus.OPEN)
        db_session.commit()
        db_session.expire_all()
        reloaded = db_session.get(RiskEvent, event.id)
        assert reloaded.detected_at.tzinfo is not None
        assert reloaded.detected_at.utcoffset() == timezone.utc.utcoffset(None)

    def test_naive_datetime_is_rejected_loudly(self, db_session, make_event):
        """A naive datetime must never reach storage.

        SQLAlchemy wraps a bind-parameter failure in StatementError, so that is
        what surfaces to callers — the underlying ValueError is chained onto it.
        """
        from datetime import datetime

        from sqlalchemy.exc import StatementError

        with pytest.raises(StatementError, match="Naive datetime rejected"):
            make_event(EventStatus.OPEN, detected_at=datetime(2026, 3, 1, 10, 30))
        db_session.rollback()

    def test_b2b_flag_reads_off_raw_signal(self, db_session, make_event):
        b2b = make_event(
            EventStatus.OPEN,
            type=EventType.INVOICE_OVERDUE,
            raw_signal={"channel": "b2b", "due_date": "2026-02-01"},
        )
        retail = make_event(EventStatus.OPEN, type=EventType.INVOICE_OVERDUE, raw_signal={})
        assert b2b.is_b2b is True
        assert retail.is_b2b is False
