"""Merchant notifications. Derived, never stored.

    cd backend && PYTHONPATH=. pytest -q tests/test_notifications.py

The property worth testing is that a notification cannot outlive the situation
that justified it. Because these are computed on read rather than stored, a
promise that is paid stops being "overdue" immediately — no reconciliation job,
no stale row contradicting the ledger.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db, utcnow
from app.engine import template_engine
from app.engine.template_engine import IST
from app.enums import EventStatus
from app.main import app
from app.models import PromiseToPay, RiskEvent
from app.routers.batch import run_batch
from app.schemas.batch import BatchRequest

MIDDAY = datetime(2026, 8, 28, 12, 0, tzinfo=IST).astimezone(timezone.utc)


@pytest.fixture(autouse=True)
def _inside_contact_hours(monkeypatch):
    real = template_engine.check_contact_window
    monkeypatch.setattr(
        template_engine, "check_contact_window", lambda now=None: real(now or MIDDAY)
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


def open_event(session) -> RiskEvent:
    for event in session.execute(select(RiskEvent)).scalars():
        if event.status not in (EventStatus.RECOVERED, EventStatus.UNRECOVERABLE):
            return event
    raise AssertionError("no open case")


def make_promise(client, session, days=5):
    event = open_event(session)
    comm = client.post(
        "/communications/prepare", json={"event_id": event.id, "channel": "email"}
    ).json()
    if comm["status"] != "prepared":
        pytest.skip("compliance refused this case")
    client.post(f"/communications/{comm['id']}/simulate-send")
    client.post(
        f"/communications/{comm['id']}/simulate-response",
        json={
            "response": "promised_to_pay",
            "promised_amount": "1000.00",
            "promised_date": (utcnow() + timedelta(days=days)).isoformat(),
        },
    )
    return event, client.get("/promises").json()["items"][0]


class TestNotificationsAreReal:
    def test_a_batch_produces_alerts_from_real_state(self, client_and_session):
        client, _ = client_and_session
        body = client.get("/notifications").json()
        assert body["total"] > 0
        for item in body["items"]:
            assert item["title"]
            assert item["detail"]
            assert item["href"]

    def test_an_empty_system_has_no_alerts(self):
        """No filler to make the panel look busy."""
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
            body = TestClient(app).get("/notifications").json()
            assert body == {"total": 0, "items": []}
        finally:
            app.dependency_overrides.clear()
            session.close()
            engine.dispose()

    def test_every_alert_points_somewhere_real(self, client_and_session):
        client, _ = client_and_session
        for item in client.get("/notifications").json()["items"]:
            assert item["href"].startswith("/")

    def test_recovered_money_is_reported(self, client_and_session):
        client, _ = client_and_session
        kinds = {i["kind"] for i in client.get("/notifications").json()["items"]}
        assert "revenue_recovered" in kinds

    def test_human_review_is_reported(self, client_and_session):
        client, session = client_and_session
        escalated = [
            e
            for e in session.execute(select(RiskEvent)).scalars()
            if e.status == EventStatus.ESCALATED
        ]
        if not escalated:
            pytest.skip("no escalated case in this batch")
        kinds = {i["kind"] for i in client.get("/notifications").json()["items"]}
        assert "human_review" in kinds

    def test_no_alert_exposes_an_identifier(self, client_and_session):
        """Titles and details are for people; ids belong in the link only."""
        client, _ = client_and_session
        for item in client.get("/notifications").json()["items"]:
            assert "batch_" not in item["title"]
            assert "evt_syn" not in item["title"]
            assert "batch_" not in item["detail"]
            assert "evt_syn" not in item["detail"]


class TestAlertsCannotOutliveTheirCause:
    def test_a_promise_alert_appears(self, client_and_session):
        client, session = client_and_session
        make_promise(client, session)
        kinds = {i["kind"] for i in client.get("/notifications").json()["items"]}
        assert "promise_made" in kinds

    def test_an_overdue_alert_appears_once_the_date_passes(self, client_and_session):
        client, session = client_and_session
        _, promise = make_promise(client, session)
        stored = session.get(PromiseToPay, promise["id"])
        stored.promised_date = utcnow() - timedelta(days=1)
        session.commit()

        kinds = {i["kind"] for i in client.get("/notifications").json()["items"]}
        assert "promise_overdue" in kinds

    def test_paying_it_clears_the_overdue_alert(self, client_and_session):
        """The whole reason these are derived: a stored 'overdue' row would keep
        contradicting the ledger after the customer paid."""
        client, session = client_and_session
        _, promise = make_promise(client, session)
        stored = session.get(PromiseToPay, promise["id"])
        stored.promised_date = utcnow() - timedelta(days=1)
        session.commit()

        # Asserted about THIS promise specifically, by its own alert id.
        #
        # The global "no overdue alerts at all" version was only ever true
        # because batches produced no promises of their own. They now do, and
        # some are legitimately overdue — so a global assertion would be
        # claiming those alerts are wrong when they are correct. The property
        # under test is unchanged and the check is now stricter: paying a
        # promise clears ITS alert.
        mine_overdue = f"promise_overdue_{promise['id']}"
        mine_fulfilled = f"promise_kept_{promise['id']}"

        before = {i["id"] for i in client.get("/notifications").json()["items"]}
        assert mine_overdue in before

        client.post(f"/promises/{promise['id']}/fulfil", json={})

        after = {i["id"] for i in client.get("/notifications").json()["items"]}
        assert mine_overdue not in after
        assert mine_fulfilled in after


class TestReadOnly:
    def test_reading_alerts_writes_nothing(self, client_and_session):
        from app.models import AuditLog

        client, session = client_and_session
        before = len(list(session.execute(select(AuditLog)).scalars()))
        for _ in range(4):
            client.get("/notifications")
        assert len(list(session.execute(select(AuditLog)).scalars())) == before

    def test_the_route_exposes_no_write_method(self):
        methods = set()
        for route in app.routes:
            if getattr(route, "path", "").startswith("/notifications"):
                methods |= set(getattr(route, "methods", set()))
        assert methods <= {"GET", "HEAD", "OPTIONS"}

    def test_there_is_no_notification_table(self):
        """A stored copy would drift from the state it describes."""
        assert "notifications" not in Base.metadata.tables
        assert "notification" not in Base.metadata.tables
