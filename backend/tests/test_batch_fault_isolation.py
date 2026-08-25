"""Batch fault isolation. BUILD_SPEC Section 9.

    "per-record try/except in /batch; one bad row -> caught, logged, batch
     continues (target: e.g. 499 processed + 1 isolated exception, never a batch
     crash)"

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

The discipline here is that a failure must be INJECTED, not merely hoped for.
Asserting that a normal batch happens to succeed proves nothing about isolation.
Each test below breaks something specific — a poisoned record, an engine that
raises on one event, a gateway that throws — and then asserts three things
together:

  1. the batch still returned,
  2. the other records still processed,
  3. the failure was REPORTED rather than swallowed.

The third is the one most easily lost. A bare ``except: pass`` would satisfy the
first two.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.enums import EventStatus, GatewayUsed
from app.models import AuditLog, Decision, Outcome, RiskEvent
from app.routers import batch as batch_module
from app.routers.batch import MalformedRecordError, run_batch, validate_record
from app.schemas.batch import BatchRequest


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
def quiet_logs():
    """Silence batch logging for tests that are not about logging."""
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def run(session: Session, count: int = 30, **kwargs):
    return run_batch(session, BatchRequest(count=count), load_ml=False, **kwargs)


# --------------------------------------------------------------------------- #
# The malformed records the generator already injects
# --------------------------------------------------------------------------- #


class TestMalformedRecordsAreIsolated:
    def test_a_batch_with_malformed_records_still_completes(
        self, db_session, quiet_logs
    ):
        """Section 11 injects ~8% broken records into every batch."""
        response = run(db_session, 50)
        assert response.processed > 0

    def test_malformed_records_are_reported_not_hidden(self, db_session, quiet_logs):
        response = run(db_session, 50)
        assert response.isolated_failures > 0
        assert len(response.failures) == response.isolated_failures

    def test_every_failure_explains_itself(self, db_session, quiet_logs):
        """A failure with no error message would be indistinguishable from a
        swallowed exception."""
        response = run(db_session, 50)
        for failure in response.failures:
            assert failure.error_type
            assert failure.error_message
            assert failure.stage
            assert failure.record_index >= 0

    def test_failures_carry_a_correlation_id(self, db_session, quiet_logs):
        """A failure that cannot be traced back to its record is not much use."""
        response = run(db_session, 50)
        for failure in response.failures:
            assert failure.correlation_id

    def test_every_record_is_accounted_for(self, db_session, quiet_logs):
        """processed + isolated + duplicates must equal the batch size, or
        records are vanishing silently."""
        response = run(db_session, 50)
        assert (
            response.processed
            + response.isolated_failures
            + response.skipped_duplicates
            == response.total_records
        )

    def test_a_failed_record_leaves_no_partial_event(self, db_session, quiet_logs):
        """The per-record transaction must roll back, or a half-created event
        would poison later queries."""
        response = run(db_session, 50)
        failed_ids = {f.event_id for f in response.failures if f.event_id}
        stored = {
            row.id
            for row in db_session.execute(
                select(RiskEvent).where(RiskEvent.id.in_(failed_ids or {"__none__"}))
            ).scalars()
        }
        assert stored == set()


class TestValidation:
    """The specific malformations Section 11 produces."""

    BASE = {
        "id": "evt_v1",
        "type": "payment_degraded",
        "merchant_id": "mer_v",
        "customer_id": "cust_v",
        "amount": Decimal("100.00"),
        "currency": "INR",
        "source_ref": "pay_V1",
        "detected_at": "2026-08-23T10:00:00+05:30",
        "raw_signal": {},
        "correlation_id": "corr_v1",
    }

    def test_a_valid_record_passes(self):
        assert validate_record(dict(self.BASE))

    @pytest.mark.parametrize("field", ["amount", "customer_id", "raw_signal", "id"])
    def test_a_missing_required_field_is_rejected(self, field):
        payload = dict(self.BASE)
        payload.pop(field)
        with pytest.raises(MalformedRecordError, match="missing required field"):
            validate_record(payload)

    def test_a_null_source_ref_is_rejected(self):
        with pytest.raises(MalformedRecordError, match="source_ref"):
            validate_record({**self.BASE, "source_ref": None})

    def test_a_negative_amount_is_rejected(self):
        with pytest.raises(MalformedRecordError, match="positive"):
            validate_record({**self.BASE, "amount": Decimal("-5.00")})

    def test_a_non_numeric_amount_is_rejected(self):
        with pytest.raises(MalformedRecordError, match="not a decimal"):
            validate_record({**self.BASE, "amount": "not-a-number"})

    def test_a_string_raw_signal_is_rejected(self):
        with pytest.raises(MalformedRecordError, match="raw_signal must be an object"):
            validate_record({**self.BASE, "raw_signal": "payment failed"})

    def test_an_unsupported_currency_is_rejected(self):
        with pytest.raises(MalformedRecordError, match="unsupported currency"):
            validate_record({**self.BASE, "currency": "XYZ"})

    def test_an_unknown_event_type_is_rejected(self):
        """No new event types, ever."""
        with pytest.raises(MalformedRecordError, match="unknown event type"):
            validate_record({**self.BASE, "type": "crypto_rugpull"})


# --------------------------------------------------------------------------- #
# Injected failures — the tests that can actually fail
# --------------------------------------------------------------------------- #


class TestInjectedEngineFailure:
    def test_one_exploding_record_does_not_stop_the_batch(
        self, db_session, quiet_logs, monkeypatch
    ):
        """The Section 9 headline: one bad row, batch continues.

        The decision engine is made to raise on the third event it sees. That is
        a mid-pipeline failure after the event has already been written, so it
        also proves the per-record rollback works.
        """
        real_decide = batch_module.decide
        state = {"calls": 0}

        def exploding_decide(session, event, **kwargs):
            state["calls"] += 1
            if state["calls"] == 3:
                raise RuntimeError("engine exploded on this record")
            return real_decide(session, event, **kwargs)

        monkeypatch.setattr(batch_module, "decide", exploding_decide)
        response = run(db_session, 30)

        assert response.processed > 0
        injected = [
            f for f in response.failures if "engine exploded" in f.error_message
        ]
        assert len(injected) == 1
        assert injected[0].error_type == "RuntimeError"
        assert injected[0].stage == "decision"

    def test_the_batch_continues_after_the_explosion(
        self, db_session, quiet_logs, monkeypatch
    ):
        """Records AFTER the failing one must still process — otherwise the
        loop merely stopped politely instead of isolating."""
        real_decide = batch_module.decide
        state = {"calls": 0, "after": 0}

        def exploding_decide(session, event, **kwargs):
            state["calls"] += 1
            if state["calls"] == 3:
                raise RuntimeError("boom")
            if state["calls"] > 3:
                state["after"] += 1
            return real_decide(session, event, **kwargs)

        monkeypatch.setattr(batch_module, "decide", exploding_decide)
        run(db_session, 30)
        assert state["after"] > 0

    def test_an_exploding_gateway_is_isolated(self, db_session, quiet_logs):
        """Section 9 names "gateway failure" as a scenario to handle."""

        class ExplodingGateway:
            name = GatewayUsed.LOCAL_SIMULATION

            def __init__(self):
                self.calls = 0

            def seed_upstream_state(self, mapping):
                pass

            def check_status(self, source_ref, event_type, *, now=None):
                self.calls += 1
                if self.calls == 2:
                    raise ConnectionError("gateway unreachable")
                from app.gateways.base import GatewayStatusResult, UpstreamStatus

                return GatewayStatusResult(status=UpstreamStatus.PENDING)

            def initiate_retry(self, request, *, now=None):
                from app.gateways.base import GatewayResponse
                from app.enums import PaymentAttemptStatus

                return GatewayResponse(status=PaymentAttemptStatus.FAILED)

            def cancel(self, source_ref, event_type, *, reason=None, now=None):
                from app.gateways.base import GatewayStatusResult, UpstreamStatus

                return GatewayStatusResult(status=UpstreamStatus.CANCELLED)

        response = run(db_session, 20, gateway=ExplodingGateway())
        assert response.processed > 0
        assert any(f.error_type == "ConnectionError" for f in response.failures)

    def test_a_failure_does_not_corrupt_the_ledger(
        self, db_session, quiet_logs, monkeypatch
    ):
        """After an injected failure the money must still balance — a rolled
        back record must leave neither an event nor an orphan outcome."""
        real_decide = batch_module.decide
        state = {"calls": 0}

        def exploding_decide(session, event, **kwargs):
            state["calls"] += 1
            if state["calls"] in (2, 5):
                raise RuntimeError("boom")
            return real_decide(session, event, **kwargs)

        monkeypatch.setattr(batch_module, "decide", exploding_decide)
        run(db_session, 30)

        events = {e.id: e for e in db_session.execute(select(RiskEvent)).scalars()}
        outcomes = list(db_session.execute(select(Outcome)).scalars())
        assert {o.event_id for o in outcomes} <= set(events)

    def test_failures_are_logged_with_a_traceback(self, db_session, monkeypatch, caplog):
        """Section 9: caught and LOGGED. Silence would hide a real defect."""
        real_decide = batch_module.decide
        state = {"calls": 0}

        def exploding_decide(session, event, **kwargs):
            state["calls"] += 1
            if state["calls"] == 2:
                raise RuntimeError("diagnostic marker 8813")
            return real_decide(session, event, **kwargs)

        monkeypatch.setattr(batch_module, "decide", exploding_decide)
        with caplog.at_level(logging.ERROR, logger="revora.batch"):
            run(db_session, 20)

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors
        assert any(r.exc_info for r in errors), "no traceback captured"
        assert any(
            getattr(r, "action", None) == "isolate_failure" for r in errors
        ), "failure was not logged as an isolated failure"


class TestBatchNeverCrashes:
    def test_a_batch_where_every_record_fails_still_returns(
        self, db_session, quiet_logs, monkeypatch
    ):
        """The pathological case: total failure must still be a report, not a
        stack trace escaping to the caller."""

        def always_explode(session, event, **kwargs):
            raise RuntimeError("everything is broken")

        monkeypatch.setattr(batch_module, "decide", always_explode)
        response = run(db_session, 20)

        assert response.processed == 0
        assert response.isolated_failures > 0
        assert response.money.amount_at_risk == "0.00"
        assert response.recovery_rate == 0.0

    def test_metrics_are_still_coherent_when_everything_fails(
        self, db_session, quiet_logs, monkeypatch
    ):
        def always_explode(session, event, **kwargs):
            raise RuntimeError("broken")

        monkeypatch.setattr(batch_module, "decide", always_explode)
        response = run(db_session, 20)
        assert response.recovery_rate == 0.0
        assert response.resolution_rate == 0.0
        assert response.ml_agreement_rate is None
        assert response.exceptions_raised == 0


class TestDuplicatesAreSkippedNotFailed:
    def test_replayed_records_are_counted_separately(self, db_session, quiet_logs):
        """Section 11 replays ~10% of records. A replay is not an error, and
        counting it as one would overstate the failure rate."""
        response = run(db_session, 50)
        assert response.skipped_duplicates > 0

    def test_a_duplicate_does_not_create_a_second_event(self, db_session, quiet_logs):
        response = run(db_session, 50)
        event_ids = [
            row.id for row in db_session.execute(select(RiskEvent)).scalars()
        ]
        assert len(event_ids) == len(set(event_ids))
        assert len(event_ids) == response.processed

    def test_a_duplicate_does_not_create_a_second_decision(
        self, db_session, quiet_logs
    ):
        """Double-deciding a replayed event would double-count it in every
        action metric."""
        run(db_session, 50)
        decisions = list(db_session.execute(select(Decision)).scalars())
        per_event: dict[str, int] = {}
        for decision in decisions:
            per_event[decision.event_id] = per_event.get(decision.event_id, 0) + 1
        assert all(count == 1 for count in per_event.values())

    def test_audit_entries_exist_only_for_real_events(self, db_session, quiet_logs):
        run(db_session, 50)
        event_ids = {
            row.id for row in db_session.execute(select(RiskEvent)).scalars()
        }
        audit_event_ids = {
            row.event_id
            for row in db_session.execute(select(AuditLog)).scalars()
            if row.event_id is not None
        }
        assert audit_event_ids <= event_ids
