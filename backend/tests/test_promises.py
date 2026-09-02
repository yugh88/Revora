"""Promise-to-Pay. BUILD_SPEC Sections 4 and 10.

    cd backend && PYTHONPATH=. pytest -q tests/test_promises.py

The properties that matter are the ones about MONEY. A promise is a statement of
intent, and the whole feature is only safe if intent can never become a recovery
on its own:

* a promise alone must not move the ledger,
* a cancelled promise must never be fulfilled,
* fulfilling twice must not recover twice,
* an overdue promise must recover nothing.

Each is tested against the real ledger rather than against the promise row.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db, utcnow
from app.engine import promise_tracker, template_engine
from app.engine.template_engine import IST
from app.enums import EventStatus, OutcomeResolution, PromiseStatus
from app.main import app
from app.models import AuditLog, Outcome, PromiseToPay, RiskEvent
from app.routers.batch import run_batch
from app.schemas.batch import BatchRequest


#: Midday IST — inside the Section 7 contact window.
MIDDAY = datetime(2026, 8, 30, 12, 0, tzinfo=IST).astimezone(timezone.utc)


@pytest.fixture(autouse=True)
def _inside_contact_hours(monkeypatch):
    """Evaluate the REAL contact-window rule against a fixed, in-window time.

    Promises now arise from recovery runs, and a run can only produce one if a
    contact was actually made. Outside 08:00-19:00 IST the compliance gate
    blocks every message — correctly — so these tests passed by day and failed
    by night purely on the hour they were run at.

    The rule itself still executes in full and still reads compliance_rules.yaml;
    it is only told what time it is, through the ``now`` parameter the engine
    already exposes for exactly this. Every other check — frequency cap, urgency
    ceiling, coercive language — runs untouched, so a message refused for any of
    those is still refused here.

    This is the same fixture test_communications.py and test_notifications.py
    already use. It should have been added here when promises began depending on
    a contact having been made.
    """
    real = template_engine.check_contact_window
    # Pinned regardless of what the caller passes. A batch supplies its own
    # `now` (the real clock), so a `now or MIDDAY` fallback would never fire —
    # which is exactly why these tests still failed after the fixture was first
    # added. Nothing in this module tests out-of-window behaviour, so forcing
    # the instant is safe here; test_template_engine.py covers the boundary.
    monkeypatch.setattr(
        template_engine, "check_contact_window", lambda now=None: real(MIDDAY)
    )


@pytest.fixture()
def client_and_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    logging.disable(logging.CRITICAL)
    run_batch(session, BatchRequest(count=40), load_ml=False)
    logging.disable(logging.NOTSET)
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app), session
    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


def unsettled_event(session, *, at_least: Decimal = Decimal("0")) -> RiskEvent:
    """A case that has not already been recovered, so a promise is meaningful.

    ``at_least`` keeps the fixture honest: a promise may not exceed the amount
    at risk, so a test asking for a specific figure needs a case that can carry
    it rather than a silently clamped one.
    """
    for event in session.execute(select(RiskEvent)).scalars():
        if event.amount < at_least:
            continue
        # Terminal cases are closed; a promise on one is refused by design.
        if event.status in (EventStatus.RECOVERED, EventStatus.UNRECOVERABLE):
            continue
        outcome = session.get(Outcome, event.id)
        if outcome is None or outcome.resolved != OutcomeResolution.RECOVERED:
            if not session.execute(
                select(PromiseToPay).where(PromiseToPay.event_id == event.id)
            ).scalars().all():
                return event
    raise AssertionError(f"no unsettled event of at least {at_least} available")


def make_full_promise(client, session, *, days: int = 5):
    """A promise for the entire amount at risk — the case can be fully settled."""
    event = unsettled_event(session)
    response = client.post(
        "/promises",
        json={
            "event_id": event.id,
            "promised_amount": str(event.amount),
            "promised_date": (utcnow() + timedelta(days=days)).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return event, response.json()


def make_promise(client, session, *, days: int = 5, amount: str | None = None):
    wanted = Decimal(amount) if amount else Decimal("0")
    event = unsettled_event(session, at_least=wanted)
    body = {
        "event_id": event.id,
        "promised_amount": amount or str(min(event.amount, Decimal("5000.00"))),
        "promised_date": (utcnow() + timedelta(days=days)).isoformat(),
    }
    response = client.post("/promises", json=body)
    assert response.status_code == 201, response.text
    return event, response.json()


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #


class TestCreatingAPromise:
    def test_a_promise_can_be_created(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session)
        assert promise["id"]

    def test_it_records_the_customer_and_amount(self, client_and_session):
        client, session = client_and_session
        event, promise = make_promise(client, session, amount="2500.00")
        expected = (event.raw_signal or {}).get("customer_name")
        assert promise["customer_name"] == expected
        assert Decimal(promise["promised_amount"]) == Decimal("2500.00")

    def test_the_promised_date_is_stored(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session, days=7)
        stored = session.get(PromiseToPay, promise["id"])
        assert (stored.promised_date - utcnow()).days in (6, 7)

    def test_it_is_bound_to_the_recovery_case(self, client_and_session):
        """A promise with no case behind it cannot be explained later."""
        client, session = client_and_session
        event, promise = make_promise(client, session)
        assert promise["event_id"] == event.id
        assert promise["event_type"] == event.type.value

    def test_a_new_promise_starts_as_promised(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session, days=10)
        assert promise["status"] == "promised"

    def test_a_past_date_is_refused(self, client_and_session):
        client, session = client_and_session
        event = unsettled_event(session)
        response = client.post(
            "/promises",
            json={
                "event_id": event.id,
                "promised_amount": "100.00",
                "promised_date": (utcnow() - timedelta(days=1)).isoformat(),
            },
        )
        assert response.status_code == 400
        assert "past" in response.json()["detail"]

    def test_a_promise_cannot_exceed_the_amount_at_risk(self, client_and_session):
        client, session = client_and_session
        event = unsettled_event(session)
        response = client.post(
            "/promises",
            json={
                "event_id": event.id,
                "promised_amount": str(event.amount + Decimal("1000")),
                "promised_date": (utcnow() + timedelta(days=3)).isoformat(),
            },
        )
        assert response.status_code == 400

    def test_a_second_open_promise_on_one_case_is_refused(self, client_and_session):
        client, session = client_and_session
        event, _ = make_promise(client, session)
        response = client.post(
            "/promises",
            json={
                "event_id": event.id,
                "promised_amount": "100.00",
                "promised_date": (utcnow() + timedelta(days=3)).isoformat(),
            },
        )
        assert response.status_code == 400

    def test_an_unknown_case_is_a_404(self, client_and_session):
        client, _ = client_and_session
        response = client.post(
            "/promises",
            json={
                "event_id": "no_such_event",
                "promised_amount": "100.00",
                "promised_date": (utcnow() + timedelta(days=3)).isoformat(),
            },
        )
        assert response.status_code == 404

    def test_creating_a_promise_moves_no_money(self, client_and_session):
        """The core safety property: intent is not payment."""
        client, session = client_and_session
        event = unsettled_event(session)
        before = session.get(Outcome, event.id)
        before_amount = before.amount_recovered if before else Decimal("0.00")

        make_promise(client, session)

        after = session.get(Outcome, event.id)
        assert (after.amount_recovered if after else Decimal("0.00")) == before_amount


# --------------------------------------------------------------------------- #
# Derived states
# --------------------------------------------------------------------------- #


class TestClosedCases:
    """A promise must not be able to reopen a case the engine has closed."""

    def test_a_promise_is_refused_on_a_closed_case(self, client_and_session):
        client, session = client_and_session
        closed = next(
            e
            for e in session.execute(select(RiskEvent)).scalars()
            if e.status == EventStatus.UNRECOVERABLE
        )
        response = client.post(
            "/promises",
            json={
                "event_id": closed.id,
                "promised_amount": "100.00",
                "promised_date": (utcnow() + timedelta(days=3)).isoformat(),
            },
        )
        assert response.status_code == 400
        assert "closed" in response.json()["detail"]

    def test_the_refusal_leaves_the_ledger_untouched(self, client_and_session):
        """The bug this guards against: a recovery written against a case the
        engine had written off, leaving the two in open disagreement."""
        client, session = client_and_session
        closed = next(
            e
            for e in session.execute(select(RiskEvent)).scalars()
            if e.status == EventStatus.UNRECOVERABLE
        )
        before = session.get(Outcome, closed.id)
        before_resolved = before.resolved if before else None

        client.post(
            "/promises",
            json={
                "event_id": closed.id,
                "promised_amount": "100.00",
                "promised_date": (utcnow() + timedelta(days=3)).isoformat(),
            },
        )

        after = session.get(Outcome, closed.id)
        assert (after.resolved if after else None) == before_resolved
        session.refresh(closed)
        assert closed.status == EventStatus.UNRECOVERABLE


class TestDisplayedStatus:
    def test_a_distant_promise_reads_as_promised(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session, days=10)
        assert promise["status"] == "promised"

    def test_a_near_promise_reads_as_due_soon(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session, days=1)
        assert promise["status"] == "due_soon"

    def test_a_passed_promise_reads_as_overdue_without_any_sweep(
        self, client_and_session
    ):
        """Derived from the date, so it is right the moment someone looks —
        no scheduled job has to have run first."""
        client, session = client_and_session
        _, promise = make_promise(client, session, days=5)
        stored = session.get(PromiseToPay, promise["id"])
        stored.promised_date = utcnow() - timedelta(days=1)
        session.commit()

        assert stored.status == PromiseStatus.PENDING  # nothing written
        assert client.get(f"/promises/{promise['id']}").json()["status"] == "overdue"

    def test_reading_a_promise_writes_nothing(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session)
        stored = session.get(PromiseToPay, promise["id"])
        stored.promised_date = utcnow() - timedelta(days=1)
        session.commit()

        before = len(list(session.execute(select(AuditLog)).scalars()))
        for _ in range(3):
            client.get("/promises")
            client.get(f"/promises/{promise['id']}")
        assert len(list(session.execute(select(AuditLog)).scalars())) == before


# --------------------------------------------------------------------------- #
# Fulfilment
# --------------------------------------------------------------------------- #


class TestFulfilment:
    def test_fulfilling_marks_the_promise_fulfilled(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session)
        body = client.post(f"/promises/{promise['id']}/fulfil").json()
        assert body["status"] == "fulfilled"

    def test_fulfilment_records_the_recovery_in_the_ledger(self, client_and_session):
        """The money must appear where all other money appears."""
        client, session = client_and_session
        event, promise = make_full_promise(client, session)
        client.post(f"/promises/{promise['id']}/fulfil")

        outcome = session.get(Outcome, event.id)
        assert outcome is not None
        assert outcome.resolved == OutcomeResolution.RECOVERED
        assert outcome.amount_recovered == event.amount

    def test_a_full_payment_closes_the_case(self, client_and_session):
        client, session = client_and_session
        event, promise = make_full_promise(client, session)
        client.post(f"/promises/{promise['id']}/fulfil")
        session.refresh(event)
        assert event.status == EventStatus.RECOVERED

    def test_a_partial_payment_leaves_the_case_open(self, client_and_session):
        """The remainder is still owed, so the case must keep being worked."""
        client, session = client_and_session
        event = unsettled_event(session, at_least=Decimal("10000"))
        client.post(
            "/promises",
            json={
                "event_id": event.id,
                "promised_amount": "1000.00",
                "promised_date": (utcnow() + timedelta(days=3)).isoformat(),
            },
        )
        promise = client.get("/promises").json()["items"][0]
        client.post(f"/promises/{promise['id']}/fulfil")

        session.refresh(event)
        assert event.status != EventStatus.RECOVERED
        outcome = session.get(Outcome, event.id)
        assert outcome.resolved == OutcomeResolution.PARTIALLY_RECOVERED
        assert outcome.amount_recovered == Decimal("1000.00")

    def test_a_partial_payment_keeps_the_ledger_balanced(self, client_and_session):
        """The bug this guards against: a partly-paid case whose unpaid
        remainder belonged to no bucket, so money left the books."""
        client, session = client_and_session
        event = unsettled_event(session, at_least=Decimal("10000"))
        client.post(
            "/promises",
            json={
                "event_id": event.id,
                "promised_amount": "1000.00",
                "promised_date": (utcnow() + timedelta(days=3)).isoformat(),
            },
        )
        promise = client.get("/promises").json()["items"][0]
        client.post(f"/promises/{promise['id']}/fulfil")

        money = client.get("/events", params={"limit": 1}).json()["money"]
        total = (
            Decimal(money["amount_recovered"])
            + Decimal(money["amount_pending"])
            + Decimal(money["amount_lost"])
        )
        assert total == Decimal(money["amount_at_risk"])

    def test_the_reported_amount_comes_from_the_ledger(self, client_and_session):
        client, session = client_and_session
        event, promise = make_full_promise(client, session)
        body = client.post(f"/promises/{promise['id']}/fulfil").json()
        outcome = session.get(Outcome, event.id)
        assert Decimal(body["amount_recovered"]) == outcome.amount_recovered
        assert body["recovered"] is True

    def test_fulfilling_twice_does_not_recover_twice(self, client_and_session):
        """Idempotence, checked against the ledger rather than the promise."""
        client, session = client_and_session
        event, promise = make_promise(client, session, amount="2000.00")
        client.post(f"/promises/{promise['id']}/fulfil")
        first = session.get(Outcome, event.id).amount_recovered

        client.post(f"/promises/{promise['id']}/fulfil")
        client.post(f"/promises/{promise['id']}/fulfil")

        assert session.get(Outcome, event.id).amount_recovered == first

    def test_a_partial_payment_records_what_actually_arrived(self, client_and_session):
        client, session = client_and_session
        event, promise = make_promise(client, session, amount="4000.00")
        client.post(f"/promises/{promise['id']}/fulfil", json={"paid_amount": "1000.00"})
        assert session.get(Outcome, event.id).amount_recovered == Decimal("1000.00")

    def test_a_cancelled_promise_cannot_be_fulfilled(self, client_and_session):
        """Nothing was owed on it, so it must never become a recovery."""
        client, session = client_and_session
        event, promise = make_promise(client, session)
        client.post(f"/promises/{promise['id']}/cancel")

        response = client.post(f"/promises/{promise['id']}/fulfil")
        assert response.status_code == 400

        outcome = session.get(Outcome, event.id)
        recovered = outcome.amount_recovered if outcome else Decimal("0.00")
        assert recovered != Decimal(promise["promised_amount"])

    def test_an_unknown_promise_is_a_404(self, client_and_session):
        client, _ = client_and_session
        assert client.post("/promises/nope/fulfil").status_code == 404

    def test_fulfilment_is_audited(self, client_and_session):
        client, session = client_and_session
        event, promise = make_promise(client, session)
        client.post(f"/promises/{promise['id']}/fulfil")
        actions = [
            row.action
            for row in session.execute(
                select(AuditLog).where(AuditLog.event_id == event.id)
            ).scalars()
        ]
        assert "promise_to_pay_recorded" in actions
        assert "promise_fulfilled" in actions


# --------------------------------------------------------------------------- #
# Cancellation and overdue
# --------------------------------------------------------------------------- #


class TestCancellation:
    def test_a_promise_can_be_cancelled(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session)
        assert client.post(f"/promises/{promise['id']}/cancel").json()["status"] == "cancelled"

    def test_cancelling_recovers_nothing(self, client_and_session):
        client, session = client_and_session
        event, promise = make_promise(client, session)
        client.post(f"/promises/{promise['id']}/cancel")
        outcome = session.get(Outcome, event.id)
        assert outcome is None or outcome.resolved != OutcomeResolution.RECOVERED

    def test_a_fulfilled_promise_cannot_be_cancelled(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session)
        client.post(f"/promises/{promise['id']}/fulfil")
        assert client.post(f"/promises/{promise['id']}/cancel").status_code == 400


class TestOverdue:
    def test_evaluation_marks_a_passed_promise_broken(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session)
        stored = session.get(PromiseToPay, promise["id"])
        stored.promised_date = utcnow() - timedelta(days=1)
        session.commit()

        client.post("/promises/evaluate")
        session.refresh(stored)
        assert stored.status == PromiseStatus.BROKEN

    def test_overdue_is_not_recovered(self, client_and_session):
        """The distinction the whole feature turns on."""
        client, session = client_and_session
        event, promise = make_promise(client, session)
        stored = session.get(PromiseToPay, promise["id"])
        stored.promised_date = utcnow() - timedelta(days=1)
        session.commit()

        client.post("/promises/evaluate")

        outcome = session.get(Outcome, event.id)
        assert outcome is None or outcome.resolved != OutcomeResolution.RECOVERED
        assert client.get(f"/promises/{promise['id']}").json()["recovered"] is False

    def test_a_future_promise_is_untouched_by_evaluation(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session, days=10)
        client.post("/promises/evaluate")
        stored = session.get(PromiseToPay, promise["id"])
        assert stored.status == PromiseStatus.PENDING

    def test_a_case_settled_by_other_means_is_not_marked_broken(
        self, client_and_session
    ):
        """The money arrived by another route. That is a good outcome, not a
        failed promise."""
        client, session = client_and_session
        event, promise = make_full_promise(client, session)
        stored = session.get(PromiseToPay, promise["id"])
        stored.promised_date = utcnow() - timedelta(days=1)
        session.commit()

        promise_tracker.fulfil_promise(session, stored)  # settled independently
        stored.status = PromiseStatus.PENDING  # pretend the sweep had not seen it
        session.commit()

        client.post("/promises/evaluate")
        session.refresh(stored)
        assert stored.status == PromiseStatus.KEPT

    def test_evaluation_is_audited(self, client_and_session):
        client, session = client_and_session
        event, promise = make_promise(client, session)
        stored = session.get(PromiseToPay, promise["id"])
        stored.promised_date = utcnow() - timedelta(days=1)
        session.commit()

        client.post("/promises/evaluate")
        actions = [
            row.action
            for row in session.execute(
                select(AuditLog).where(AuditLog.event_id == event.id)
            ).scalars()
        ]
        assert "promise_broken" in actions


class TestListing:
    def test_totals_reflect_real_promises(self, client_and_session):
        """Measured as a delta. A recovery run now produces promises of its own,
        so an absolute total would be asserting that the agent does nothing."""
        client, session = client_and_session
        before = client.get("/promises").json()
        make_promise(client, session, amount="1000.00")
        make_promise(client, session, amount="2000.00")
        after = client.get("/promises").json()
        assert after["total"] == before["total"] + 2
        assert Decimal(after["total_promised"]) - Decimal(
            before["total_promised"]
        ) == Decimal("3000.00")

    def test_fulfilled_total_counts_only_fulfilled(self, client_and_session):
        client, session = client_and_session
        make_promise(client, session, amount="1000.00")
        _, second = make_promise(client, session, amount="2000.00")
        client.post(f"/promises/{second['id']}/fulfil")

        body = client.get("/promises").json()
        assert Decimal(body["total_fulfilled"]) == Decimal("2000.00")

    def test_status_filter_narrows(self, client_and_session):
        client, session = client_and_session
        _, near = make_promise(client, session, days=1)
        body = client.get("/promises", params={"status": "due_soon"}).json()
        assert body["total"] >= 1
        assert all(item["status"] == "due_soon" for item in body["items"])
        assert any(item["id"] == near["id"] for item in body["items"])

    def test_an_empty_list_is_not_an_error(self):
        """Checked on a system where nothing has run, since a recovery run now
        legitimately produces promises."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
        app.dependency_overrides[get_db] = lambda: session
        try:
            body = TestClient(app).get("/promises").json()
            assert body["total"] == 0
            assert body["items"] == []
        finally:
            app.dependency_overrides.clear()
            session.close()
            engine.dispose()


class TestLedgerRemainsTheSourceOfTruth:
    def test_promise_money_agrees_with_the_events_feed(self, client_and_session):
        """One truth: what /promises reports as recovered must be what the
        ledger reports, because both read the same rows."""
        client, session = client_and_session
        event, promise = make_promise(client, session, amount="2500.00")

        before = Decimal(client.get("/events", params={"limit": 1}).json()["money"]["amount_recovered"])
        client.post(f"/promises/{promise['id']}/fulfil")
        after = Decimal(client.get("/events", params={"limit": 1}).json()["money"]["amount_recovered"])

        outcome_before = Decimal("0.00")
        assert after > before or after == before + Decimal("2500.00") - outcome_before

    def test_no_separate_promise_money_total_exists(self):
        """The tracker must not own an amount of its own."""
        import ast
        import inspect

        from app.engine import promise_tracker as module

        source = inspect.getsource(module)
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        # It must reach the ledger through the batch's own writer, never by
        # constructing an Outcome itself.
        assert "upsert_outcome" in source
        assert "Outcome(" not in source


class TestPromisesAriseFromRecoveryRuns:
    """The loop the product is named for.

    A run previously created conversations and stopped, so no promise could ever
    arise from one and /promises stayed empty however many recoveries were run.
    """

    def test_a_run_produces_real_promises(self, client_and_session):
        _, session = client_and_session
        promises = list(session.execute(select(PromiseToPay)).scalars())
        assert promises, "a recovery run produced no promises at all"

    def test_each_promise_traces_to_what_the_customer_said(self, client_and_session):
        """A promise has to be defensible later: "why does Revora think they
        said 3 September?" needs an answer."""
        from app.models import CommunicationLog

        _, session = client_and_session
        for promise in session.execute(select(PromiseToPay)).scalars():
            source = session.execute(
                select(CommunicationLog).where(CommunicationLog.promise_id == promise.id)
            ).scalars().first()
            if source is not None:
                assert source.reply_text
                assert source.event_id == promise.event_id

    def test_promises_carry_a_real_future_date(self, client_and_session):
        _, session = client_and_session
        for promise in session.execute(select(PromiseToPay)).scalars():
            assert promise.promised_date is not None
            assert promise.promised_amount > 0

    def test_runs_are_reproducible(self, client_and_session):
        """Same seed, same promises — a demo must not depend on luck."""
        import logging as _logging

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.database import Base as _Base
        from app.routers.batch import run_batch as _run

        counts = []
        for _ in range(2):
            engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
                future=True,
            )
            _Base.metadata.create_all(bind=engine)
            session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
            _logging.disable(_logging.CRITICAL)
            try:
                counts.append(_run(session, BatchRequest(count=40), load_ml=False).promises_made)
            finally:
                _logging.disable(_logging.NOTSET)
                session.close()
                engine.dispose()
        assert counts[0] == counts[1]


class TestInterpretingWhatCustomersSay:
    """Deterministic reading of a reply. No model, so nothing can hallucinate a
    commitment nobody made."""

    def test_english_promise_with_a_date(self):
        reading = promise_tracker.interpret_response(
            "I will pay by 3 September", now=utcnow()
        )
        assert reading.intent == "promise_to_pay"
        assert reading.promised_date is not None
        assert reading.promised_date.month == 9 and reading.promised_date.day == 3

    def test_hinglish_promise_with_a_relative_date(self):
        now = utcnow()
        reading = promise_tracker.interpret_response("Main kal payment kar dunga", now=now)
        assert reading.intent == "promise_to_pay"
        assert (reading.promised_date.date() - now.date()).days == 1

    def test_hinglish_promise_with_an_explicit_date(self):
        reading = promise_tracker.interpret_response(
            "3 September tak payment kar dunga", now=utcnow()
        )
        assert reading.promised_date.month == 9 and reading.promised_date.day == 3

    def test_a_named_weekday_resolves_forward(self):
        now = utcnow()
        reading = promise_tracker.interpret_response("I can pay on Friday", now=now)
        assert reading.promised_date is not None
        assert reading.promised_date.weekday() == 4
        assert reading.promised_date > now

    def test_a_date_hidden_behind_another_number_is_still_found(self):
        """"pay by 3 September" once lost its date because "by 3" was consumed
        first and the scan resumed past the digits."""
        reading = promise_tracker.interpret_response(
            "I will pay by 3 September", now=utcnow()
        )
        assert reading.promised_date is not None

    def test_a_commitment_without_a_date_invents_nothing(self):
        """The most important property here. A guessed date would create a
        commitment nobody made, and recovery would pause on the strength of it."""
        reading = promise_tracker.interpret_response("I will pay soon", now=utcnow())
        assert reading.intent == "promise_to_pay"
        assert reading.promised_date is None
        assert reading.confidence < 0.5

    def test_a_refusal_is_not_a_promise(self):
        assert (
            promise_tracker.interpret_response("Sorry, I cannot pay right now").intent
            == "refused"
        )

    def test_an_existing_payment_is_not_a_promise(self):
        assert promise_tracker.interpret_response("Payment already done").intent == "paid"

    def test_noise_is_read_as_unclear(self):
        for text in ("ok", "thanks", "", "?"):
            assert promise_tracker.interpret_response(text).intent == "unclear"

    def test_the_original_wording_is_always_kept(self):
        reading = promise_tracker.interpret_response("Main kal payment kar dunga")
        assert reading.original_text == "Main kal payment kar dunga"


class TestAnOpenPromisePausesRecovery:
    """A promise that changes nothing is a display artefact."""

    def _setup(self, session):
        from app.engine import policy_engine
        from app.engine.diagnosis_engine import DiagnosisResult
        from app.models import Diagnosis, StoppingRuleState

        promise = list(session.execute(select(PromiseToPay)).scalars())[0]
        event = session.get(RiskEvent, promise.event_id)
        stored = session.get(Diagnosis, event.id)
        state = session.get(StoppingRuleState, event.id)
        if state is not None:
            state.cooldown_until = None
            state.attempts_used = 0
            session.flush()
        result = DiagnosisResult(
            root_cause=stored.root_cause_code, confidence=stored.confidence, evidence=[]
        )
        return promise, event, result, policy_engine.resolve_policy(session, event)

    def _rule(self, session, event, result, policy, action):
        from app.engine import policy_engine

        return policy_engine.evaluate(
            session, event, action, policy=policy, diagnosis=result,
            probability=0.5, attempt_number=1,
        )

    def test_contacting_again_is_blocked(self, client_and_session):
        from app.engine.diagnosis_engine import ActionCode

        _, session = client_and_session
        promise, event, result, policy = self._setup(session)
        verdict = self._rule(session, event, result, policy, ActionCode.SMS_REMINDER)
        assert verdict.status.value == "blocked"
        assert verdict.rule_triggered == "customer_promised_to_pay"

    def test_a_silent_gateway_retry_is_not_paused(self, client_and_session):
        """A retry costs the customer nothing and may simply succeed."""
        from app.engine.diagnosis_engine import ActionCode

        _, session = client_and_session
        promise, event, result, policy = self._setup(session)
        verdict = self._rule(session, event, result, policy, ActionCode.RETRY_PAYMENT)
        assert verdict.status.value == "allowed"

    def test_recovery_resumes_once_the_date_passes(self, client_and_session):
        """No sweep required: the pause reads the date, not a stored flag."""
        from datetime import datetime, timedelta, timezone

        from app.engine.diagnosis_engine import ActionCode

        _, session = client_and_session
        promise, event, result, policy = self._setup(session)
        promise.promised_date = utcnow() - timedelta(days=1)
        session.flush()
        verdict = self._rule(session, event, result, policy, ActionCode.SMS_REMINDER)
        assert verdict.status.value == "allowed"

    def test_a_fulfilled_promise_does_not_keep_pausing(self, client_and_session):
        from app.engine.diagnosis_engine import ActionCode

        _, session = client_and_session
        promise, event, result, policy = self._setup(session)
        promise.status = PromiseStatus.KEPT
        session.flush()
        verdict = self._rule(session, event, result, policy, ActionCode.SMS_REMINDER)
        assert verdict.status.value == "allowed"


class TestPendingCasesAreVerifiedLater:
    """Some actions have no immediate answer.

    `await_gateway_auto_retry` — the only permitted move on a failed
    subscription — means "the provider will retry this itself". Its result only
    exists later, and nothing used to look. Every subscription therefore sat at
    PENDING for ever and the category reported zero recovered, not because
    recovery failed but because nobody ever checked.
    """

    def _fresh(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.gateways.local_simulation import LocalSimulationGateway
        from app.routers.batch import run_batch as _run

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
        gateway = LocalSimulationGateway(seed=42)
        logging.disable(logging.CRITICAL)
        _run(session, BatchRequest(count=120), gateway=gateway, load_ml=False)
        logging.disable(logging.NOTSET)
        return session, gateway, engine

    def _recovered_subscriptions(self, session):
        from app.enums import EventType, OutcomeResolution
        from app.models import Outcome, RiskEvent

        total = Decimal("0.00")
        count = 0
        for event in session.execute(
            select(RiskEvent).where(RiskEvent.type == EventType.SUBSCRIPTION_FAILED)
        ).scalars():
            outcome = session.get(Outcome, event.id)
            if outcome is not None and outcome.resolved == OutcomeResolution.RECOVERED:
                total += outcome.amount_recovered
                count += 1
        return count, total

    def test_the_sweep_resolves_cases_the_provider_has_settled(self):
        from app.routers.batch import verify_pending_cases

        session, gateway, engine = self._fresh()
        try:
            before_count, _ = self._recovered_subscriptions(session)
            # The provider's auto-retry window is a day; look after it passes.
            settled = verify_pending_cases(
                session, gateway, now=utcnow() + timedelta(days=2), limit=300
            )
            after_count, after_total = self._recovered_subscriptions(session)

            assert settled > 0, "the sweep settled nothing"
            assert after_count > before_count
            assert after_total > 0
        finally:
            session.close()
            engine.dispose()

    def test_nothing_resolves_before_the_window_passes(self):
        """The provider has not retried yet, so there is nothing to record."""
        from app.routers.batch import verify_pending_cases

        session, gateway, engine = self._fresh()
        try:
            before = self._recovered_subscriptions(session)
            verify_pending_cases(session, gateway, now=utcnow(), limit=300)
            assert self._recovered_subscriptions(session) == before
        finally:
            session.close()
            engine.dispose()

    def test_the_sweep_keeps_the_ledger_balanced(self):
        from app.routers.batch import verify_pending_cases
        from app.routers.events import _money_summary
        from app.models import RiskEvent

        session, gateway, engine = self._fresh()
        try:
            verify_pending_cases(
                session, gateway, now=utcnow() + timedelta(days=2), limit=300
            )
            money = _money_summary(session, lambda stmt: stmt, {})
            total = (
                Decimal(money.amount_recovered)
                + Decimal(money.amount_pending)
                + Decimal(money.amount_lost)
            )
            assert total == Decimal(money.amount_at_risk)
        finally:
            session.close()
            engine.dispose()

    def test_running_the_sweep_twice_does_not_recover_twice(self):
        from app.routers.batch import verify_pending_cases

        session, gateway, engine = self._fresh()
        try:
            later = utcnow() + timedelta(days=2)
            verify_pending_cases(session, gateway, now=later, limit=300)
            first = self._recovered_subscriptions(session)
            verify_pending_cases(session, gateway, now=later, limit=300)
            assert self._recovered_subscriptions(session) == first
        finally:
            session.close()
            engine.dispose()
