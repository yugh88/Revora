"""Tests for engine/idempotency.py and engine/locks.py. BUILD_SPEC Sections 9 and 4.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

Why locks live in this file
---------------------------
Section 12's manifest has one test file in this area, and idempotency keys and
action locks are the two mechanisms that stop the same event being acted on
twice: the lock prevents two workers racing on it now, the idempotency key
prevents the same work being repeated later. Testing them together also lets
the interaction be covered — a worker that reclaims an expired lock must still
be blocked by the idempotency key from re-executing what the dead worker
already did, which is the case most likely to double-charge a customer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.engine import idempotency
from app.engine.locks import (
    ACTION_LOCK_RECLAIMED,
    DEFAULT_LOCK_TTL,
    LockUnavailable,
    acquire,
    event_lock,
    is_locked,
    release,
)
from app.enums import EventType, GatewayUsed, PaymentAttemptStatus
from app.models import ActionLock, AuditLog, CustomerProfile, Merchant, PaymentAttempt, RiskEvent

T0 = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)


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
def event(db_session: Session) -> RiskEvent:
    db_session.add(Merchant(id="mer_t", name="Test Merchant"))
    db_session.add(CustomerProfile(customer_id="cust_t", payment_success_rate=0.8))
    db_session.flush()
    row = RiskEvent(
        id="evt_t1",
        type=EventType.PAYMENT_DEGRADED,
        merchant_id="mer_t",
        customer_id="cust_t",
        amount=Decimal("2499.00"),
        source_ref="pay_T1",
        detected_at=T0,
        raw_signal={"gateway_error_code": "BAD_REQUEST_CARD_EXPIRED"},
        correlation_id="corr_t1",
    )
    db_session.add(row)
    db_session.flush()
    return row


# --------------------------------------------------------------------------- #
# Idempotency keys — Section 9
# --------------------------------------------------------------------------- #


class TestKeyGeneration:
    def test_key_is_deterministic(self):
        """A replayed batch must compute the identical key, or the existing-row
        check can never find anything."""
        assert idempotency.build_key("evt_1", 1, "sms_reminder") == idempotency.build_key(
            "evt_1", 1, "sms_reminder"
        )

    def test_key_is_a_sha256_hex_digest(self):
        key = idempotency.build_key("evt_1", 1, "sms_reminder")
        assert len(key) == 64
        assert all(char in "0123456789abcdef" for char in key)

    def test_different_events_give_different_keys(self):
        assert idempotency.build_key("evt_1", 1, "a") != idempotency.build_key("evt_2", 1, "a")

    def test_different_attempts_give_different_keys(self):
        """Section 9: the key is a hash of event AND attempt."""
        assert idempotency.build_key("evt_1", 1, "a") != idempotency.build_key("evt_1", 2, "a")

    def test_different_actions_give_different_keys(self):
        """Section 6 permits different actions at the same attempt number; they
        must not collide onto one key."""
        assert idempotency.build_key("evt_1", 1, "sms_reminder") != idempotency.build_key(
            "evt_1", 1, "update_card_email"
        )

    def test_field_boundaries_cannot_be_confused(self):
        """Without a separator, ("ev1", 11) and ("ev11", 1) would collide."""
        assert idempotency.build_key("ev", 11, "x") != idempotency.build_key("ev1", 1, "x")

    def test_attempt_number_must_be_positive(self):
        with pytest.raises(ValueError, match="attempt_number must be >= 1"):
            idempotency.build_key("evt_1", 0, "a")


# --------------------------------------------------------------------------- #
# Duplicate execution prevention — Section 9
# --------------------------------------------------------------------------- #


class TestDuplicateExecutionPrevention:
    def test_first_record_creates_an_attempt(self, db_session, event):
        attempt, created = idempotency.record_attempt(
            db_session, event_id=event.id, attempt_number=1, action_code="sms_reminder"
        )
        assert created is True
        assert attempt.attempt_number == 1

    def test_second_record_returns_the_existing_row(self, db_session, event):
        """Section 9: return existing result if found, never re-execute."""
        first, _ = idempotency.record_attempt(
            db_session, event_id=event.id, attempt_number=1, action_code="sms_reminder"
        )
        second, created = idempotency.record_attempt(
            db_session, event_id=event.id, attempt_number=1, action_code="sms_reminder"
        )
        assert created is False
        assert second.id == first.id

    def test_duplicate_does_not_insert_a_second_row(self, db_session, event):
        for _ in range(4):
            idempotency.record_attempt(
                db_session, event_id=event.id, attempt_number=1, action_code="sms_reminder"
            )
        db_session.commit()
        count = len(
            db_session.execute(
                select(PaymentAttempt).where(PaymentAttempt.event_id == event.id)
            ).scalars().all()
        )
        assert count == 1

    def test_existing_result_is_preserved_not_overwritten(self, db_session, event):
        """A replay must return the ORIGINAL outcome, not reset it to pending."""
        idempotency.record_attempt(
            db_session,
            event_id=event.id,
            attempt_number=1,
            action_code="sms_reminder",
            status=PaymentAttemptStatus.SUCCESS,
            provider_ref="pay_sim_abc",
        )
        replay, created = idempotency.record_attempt(
            db_session,
            event_id=event.id,
            attempt_number=1,
            action_code="sms_reminder",
            status=PaymentAttemptStatus.PENDING,
        )
        assert created is False
        assert replay.status == PaymentAttemptStatus.SUCCESS
        assert replay.provider_ref == "pay_sim_abc"

    def test_a_different_attempt_is_allowed_to_execute(self, db_session, event):
        idempotency.record_attempt(
            db_session, event_id=event.id, attempt_number=1, action_code="sms_reminder"
        )
        _, created = idempotency.record_attempt(
            db_session, event_id=event.id, attempt_number=2, action_code="sms_reminder"
        )
        assert created is True

    def test_find_existing_attempt_locates_the_row(self, db_session, event):
        idempotency.record_attempt(
            db_session, event_id=event.id, attempt_number=1, action_code="sms_reminder"
        )
        key = idempotency.build_key(event.id, 1, "sms_reminder")
        assert idempotency.find_existing_attempt(db_session, key) is not None

    def test_find_existing_attempt_returns_none_when_absent(self, db_session):
        assert idempotency.find_existing_attempt(db_session, "0" * 64) is None

    def test_has_executed_reflects_reality(self, db_session, event):
        assert idempotency.has_executed(db_session, event.id, 1, "sms_reminder") is False
        idempotency.record_attempt(
            db_session, event_id=event.id, attempt_number=1, action_code="sms_reminder"
        )
        assert idempotency.has_executed(db_session, event.id, 1, "sms_reminder") is True

    def test_database_constraint_is_the_backstop(self, db_session, event):
        """Even bypassing the helper, the UNIQUE constraint must refuse."""
        from sqlalchemy.exc import IntegrityError

        key = idempotency.build_key(event.id, 1, "sms_reminder")
        for attempt_number in (1, 2):
            db_session.add(
                PaymentAttempt(
                    event_id=event.id,
                    attempt_number=attempt_number,
                    idempotency_key=key,
                    gateway_used=GatewayUsed.LOCAL_SIMULATION,
                    initiated_at=T0,
                )
            )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()


class TestNextAttemptNumber:
    def test_starts_at_one(self, db_session, event):
        assert idempotency.next_attempt_number(db_session, event.id) == 1

    def test_increments_past_the_highest_recorded(self, db_session, event):
        idempotency.record_attempt(
            db_session, event_id=event.id, attempt_number=1, action_code="a"
        )
        assert idempotency.next_attempt_number(db_session, event.id) == 2
        idempotency.record_attempt(
            db_session, event_id=event.id, attempt_number=2, action_code="b"
        )
        assert idempotency.next_attempt_number(db_session, event.id) == 3

    def test_is_scoped_per_event(self, db_session, event):
        idempotency.record_attempt(
            db_session, event_id=event.id, attempt_number=1, action_code="a"
        )
        db_session.add(
            RiskEvent(
                id="evt_other",
                type=EventType.PAYMENT_DEGRADED,
                merchant_id=event.merchant_id,
                customer_id=event.customer_id,
                amount=Decimal("100.00"),
                detected_at=T0,
                raw_signal={},
                correlation_id="corr_other",
            )
        )
        db_session.flush()
        assert idempotency.next_attempt_number(db_session, "evt_other") == 1


# --------------------------------------------------------------------------- #
# Action locks — Section 4
# --------------------------------------------------------------------------- #


class TestLockAcquisition:
    def test_acquiring_a_free_event_succeeds(self, db_session, event):
        held = acquire(db_session, event, "worker_a", now=T0)
        assert held.reclaimed is False
        assert held.lock.locked_by == "worker_a"

    def test_ttl_is_applied(self, db_session, event):
        held = acquire(db_session, event, "worker_a", now=T0)
        assert held.lock.expires_at == T0 + DEFAULT_LOCK_TTL

    def test_a_second_worker_is_refused(self, db_session, event):
        """The whole point of the lock."""
        acquire(db_session, event, "worker_a", now=T0)
        with pytest.raises(LockUnavailable) as excinfo:
            acquire(db_session, event, "worker_b", now=T0 + timedelta(seconds=30))
        assert excinfo.value.held_by == "worker_a"

    def test_the_holder_is_reported_in_the_error(self, db_session, event):
        acquire(db_session, event, "batch_run_7", now=T0)
        with pytest.raises(LockUnavailable, match="batch_run_7"):
            acquire(db_session, event, "worker_b", now=T0)

    def test_is_locked_tracks_state(self, db_session, event):
        assert is_locked(db_session, event.id, now=T0) is False
        acquire(db_session, event, "worker_a", now=T0)
        assert is_locked(db_session, event.id, now=T0) is True

    def test_lock_is_not_live_once_expired(self, db_session, event):
        acquire(db_session, event, "worker_a", now=T0)
        assert is_locked(db_session, event.id, now=T0 + DEFAULT_LOCK_TTL) is False


class TestExpiredLockReclaim:
    """Section 4: "Expired locks are reclaimable (handles crashed jobs)"."""

    def test_expired_lock_can_be_taken_by_another_worker(self, db_session, event):
        acquire(db_session, event, "crashed_worker", now=T0)
        held = acquire(
            db_session, event, "worker_b", now=T0 + DEFAULT_LOCK_TTL + timedelta(seconds=1)
        )
        assert held.reclaimed is True
        assert held.previous_holder == "crashed_worker"
        assert held.lock.locked_by == "worker_b"

    def test_reclaim_extends_the_ttl(self, db_session, event):
        acquire(db_session, event, "crashed_worker", now=T0)
        later = T0 + DEFAULT_LOCK_TTL + timedelta(seconds=1)
        held = acquire(db_session, event, "worker_b", now=later)
        assert held.lock.expires_at == later + DEFAULT_LOCK_TTL

    def test_reclaim_is_audited(self, db_session, event):
        """A crashed job is exactly what the audit trail should show."""
        acquire(db_session, event, "crashed_worker", now=T0)
        acquire(db_session, event, "worker_b", now=T0 + DEFAULT_LOCK_TTL + timedelta(seconds=1))

        rows = db_session.execute(
            select(AuditLog).where(AuditLog.action == ACTION_LOCK_RECLAIMED)
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].before_state == "crashed_worker"
        assert rows[0].after_state == "worker_b"
        assert "presumed crashed" in (rows[0].reasoning or "")

    def test_a_lock_exactly_at_its_expiry_is_reclaimable(self, db_session, event):
        acquire(db_session, event, "worker_a", now=T0)
        held = acquire(db_session, event, "worker_b", now=T0 + DEFAULT_LOCK_TTL)
        assert held.reclaimed is True

    def test_normal_acquisition_is_not_audited(self, db_session, event):
        """Auditing every routine lock would drown the trail in noise."""
        acquire(db_session, event, "worker_a", now=T0)
        assert db_session.execute(select(AuditLog)).scalars().all() == []


class TestLockRelease:
    def test_holder_can_release(self, db_session, event):
        acquire(db_session, event, "worker_a", now=T0)
        assert release(db_session, event, "worker_a", now=T0) is True
        assert db_session.get(ActionLock, event.id) is None

    def test_releasing_an_unlocked_event_is_a_no_op(self, db_session, event):
        assert release(db_session, event, "worker_a", now=T0) is False

    def test_a_non_holder_cannot_release(self, db_session, event):
        """After a reclaim, the dead worker must not delete the new holder's lock."""
        acquire(db_session, event, "crashed_worker", now=T0)
        acquire(db_session, event, "worker_b", now=T0 + DEFAULT_LOCK_TTL + timedelta(seconds=1))

        assert release(db_session, event, "crashed_worker", now=T0) is False
        surviving = db_session.get(ActionLock, event.id)
        assert surviving is not None
        assert surviving.locked_by == "worker_b"

    def test_released_event_can_be_locked_again(self, db_session, event):
        acquire(db_session, event, "worker_a", now=T0)
        release(db_session, event, "worker_a", now=T0)
        held = acquire(db_session, event, "worker_b", now=T0)
        assert held.reclaimed is False


class TestLockContextManager:
    def test_lock_is_held_inside_and_released_after(self, db_session, event):
        with event_lock(db_session, event, "worker_a", now=T0):
            assert is_locked(db_session, event.id, now=T0) is True
        assert is_locked(db_session, event.id, now=T0) is False

    def test_lock_is_released_even_when_the_body_raises(self, db_session, event):
        """Otherwise one exception would block the event until the TTL lapsed."""
        with pytest.raises(RuntimeError):
            with event_lock(db_session, event, "worker_a", now=T0):
                raise RuntimeError("action blew up")
        assert is_locked(db_session, event.id, now=T0) is False

    def test_context_manager_refuses_a_live_lock(self, db_session, event):
        acquire(db_session, event, "worker_a", now=T0)
        with pytest.raises(LockUnavailable):
            with event_lock(db_session, event, "worker_b", now=T0):
                pass


class TestLockAndIdempotencyTogether:
    def test_reclaiming_a_lock_does_not_permit_re_execution(self, db_session, event):
        """The case most likely to double-charge someone.

        A worker crashes after executing but before releasing. Another worker
        reclaims the expired lock — and must still be stopped by the
        idempotency key from repeating the work.
        """
        acquire(db_session, event, "crashed_worker", now=T0)
        idempotency.record_attempt(
            db_session,
            event_id=event.id,
            attempt_number=1,
            action_code="sms_reminder",
            status=PaymentAttemptStatus.SUCCESS,
            provider_ref="pay_sim_original",
        )

        held = acquire(
            db_session, event, "worker_b", now=T0 + DEFAULT_LOCK_TTL + timedelta(seconds=1)
        )
        assert held.reclaimed is True

        attempt, created = idempotency.record_attempt(
            db_session, event_id=event.id, attempt_number=1, action_code="sms_reminder"
        )
        assert created is False
        assert attempt.provider_ref == "pay_sim_original"
