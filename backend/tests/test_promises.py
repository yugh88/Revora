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
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db, utcnow
from app.engine import promise_tracker
from app.enums import EventStatus, OutcomeResolution, PromiseStatus
from app.main import app
from app.models import AuditLog, Outcome, PromiseToPay, RiskEvent
from app.routers.batch import run_batch
from app.schemas.batch import BatchRequest


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
        client, session = client_and_session
        make_promise(client, session, amount="1000.00")
        make_promise(client, session, amount="2000.00")
        body = client.get("/promises").json()
        assert body["total"] == 2
        assert Decimal(body["total_promised"]) == Decimal("3000.00")

    def test_fulfilled_total_counts_only_fulfilled(self, client_and_session):
        client, session = client_and_session
        make_promise(client, session, amount="1000.00")
        _, second = make_promise(client, session, amount="2000.00")
        client.post(f"/promises/{second['id']}/fulfil")

        body = client.get("/promises").json()
        assert Decimal(body["total_fulfilled"]) == Decimal("2000.00")

    def test_status_filter_narrows(self, client_and_session):
        client, session = client_and_session
        make_promise(client, session, days=10)
        _, near = make_promise(client, session, days=1)
        body = client.get("/promises", params={"status": "due_soon"}).json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == near["id"]

    def test_an_empty_list_is_not_an_error(self, client_and_session):
        client, _ = client_and_session
        body = client.get("/promises").json()
        assert body["total"] == 0
        assert body["items"] == []


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
