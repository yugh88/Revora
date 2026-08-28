"""Recovery run history. BUILD_SPEC Section 10.

    cd backend && PYTHONPATH=. pytest -q tests/test_recovery_runs.py

A completed run used to exist only in the browser tab that started it. These
tests cover the persisted record that lets a merchant come back to it.

The property that matters most is that a stored run is a SNAPSHOT, not a second
calculation. It reports what that run reported at the time, and it must not
change if the ledger moves on afterwards — recomputing would quietly rewrite
what the merchant actually saw.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Outcome, RecoveryRun
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
    app.dependency_overrides[get_db] = lambda: session
    logging.disable(logging.CRITICAL)
    yield TestClient(app), session
    logging.disable(logging.NOTSET)
    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


class TestRunsArePersisted:
    def test_a_completed_run_is_recorded(self, client_and_session):
        client, session = client_and_session
        client.post("/batch", json={"count": 20})
        rows = list(session.execute(select(RecoveryRun)).scalars())
        assert len(rows) == 1

    def test_the_record_matches_what_the_run_reported(self, client_and_session):
        client, session = client_and_session
        response = client.post("/batch", json={"count": 20}).json()
        run = list(session.execute(select(RecoveryRun)).scalars())[0]

        assert run.id == response["batch_id"]
        assert run.total_records == response["total_records"]
        assert run.processed == response["processed"]
        assert run.amount_at_risk == Decimal(response["money"]["amount_at_risk"])
        assert run.amount_recovered == Decimal(response["money"]["amount_recovered"])
        assert run.recovery_rate == response["recovery_rate"]

    def test_each_run_gets_its_own_record(self, client_and_session):
        client, session = client_and_session
        client.post("/batch", json={"count": 20})
        client.post("/batch", json={"count": 20})
        rows = list(session.execute(select(RecoveryRun)).scalars())
        assert len(rows) == 2
        assert rows[0].id != rows[1].id

    def test_history_write_failure_does_not_fail_the_run(self, client_and_session, monkeypatch):
        """The analysis already succeeded and its events are in the ledger.
        Losing the bookkeeping entry must not throw that work away."""
        from app.routers import batch as batch_module

        def explode(*args, **kwargs):
            raise RuntimeError("history table unavailable")

        monkeypatch.setattr(batch_module, "record_run", explode)
        response = client_and_session[0].post("/batch", json={"count": 10})
        assert response.status_code == 200
        assert response.json()["processed"] > 0


class TestRunNames:
    def test_names_are_merchant_readable(self, client_and_session):
        client, session = client_and_session
        client.post("/batch", json={"count": 10})
        run = list(session.execute(select(RecoveryRun)).scalars())[0]
        assert "Recovery Run" in run.name or "Morning Recovery Run" in run.name
        # No raw identifier leaks into the name a merchant reads.
        assert "batch_" not in run.name
        assert "evt_syn" not in run.name

    def test_names_are_unique_across_runs(self, client_and_session):
        client, session = client_and_session
        client.post("/batch", json={"count": 10})
        client.post("/batch", json={"count": 10})
        names = [r.name for r in session.execute(select(RecoveryRun)).scalars()]
        assert len(set(names)) == len(names)

    def test_the_name_is_stored_not_recomputed(self, client_and_session):
        """A name derived from "now" on every read would change as the clock
        moved; the merchant would see a run rename itself."""
        client, session = client_and_session
        client.post("/batch", json={"count": 10})
        first = client.get("/batch/runs").json()["items"][0]["name"]
        second = client.get("/batch/runs").json()["items"][0]["name"]
        assert first == second


class TestListingRuns:
    def test_it_lists_completed_runs(self, client_and_session):
        client, _ = client_and_session
        client.post("/batch", json={"count": 10})
        client.post("/batch", json={"count": 10})
        body = client.get("/batch/runs").json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_newest_first(self, client_and_session):
        client, _ = client_and_session
        client.post("/batch", json={"count": 10})
        client.post("/batch", json={"count": 10})
        stamps = [item["finished_at"] for item in client.get("/batch/runs").json()["items"]]
        assert stamps == sorted(stamps, reverse=True)

    def test_money_is_an_exact_string(self, client_and_session):
        client, _ = client_and_session
        client.post("/batch", json={"count": 10})
        item = client.get("/batch/runs").json()["items"][0]
        for field in ("amount_at_risk", "amount_recovered", "amount_pending", "amount_lost"):
            assert isinstance(item[field], str)
            Decimal(item[field])

    def test_an_empty_history_is_not_an_error(self, client_and_session):
        client, _ = client_and_session
        body = client.get("/batch/runs").json()
        assert body == {"total": 0, "items": []}


class TestReopeningARun:
    def test_a_run_can_be_reopened(self, client_and_session):
        client, _ = client_and_session
        original = client.post("/batch", json={"count": 20}).json()
        reopened = client.get(f"/batch/runs/{original['batch_id']}").json()
        assert reopened["run"]["id"] == original["batch_id"]

    def test_the_reopened_figures_match_the_original_exactly(self, client_and_session):
        """The whole point: come back later, see the same numbers."""
        client, _ = client_and_session
        original = client.post("/batch", json={"count": 20}).json()
        snapshot = client.get(f"/batch/runs/{original['batch_id']}").json()["snapshot"]

        assert snapshot["money"] == original["money"]
        assert snapshot["recovery_rate"] == original["recovery_rate"]
        assert snapshot["processed"] == original["processed"]
        assert snapshot["status_breakdown"] == original["status_breakdown"]
        assert snapshot["event_type_breakdown"] == original["event_type_breakdown"]
        assert snapshot["stopping_rule_triggers"] == original["stopping_rule_triggers"]

    def test_a_later_ledger_change_does_not_rewrite_a_past_run(self, client_and_session):
        """A snapshot records what was reported, not what is true now.

        If the ledger later changes, the run must still show the figures the
        merchant saw and acted on. Recomputing on read would silently rewrite
        history.
        """
        client, session = client_and_session
        original = client.post("/batch", json={"count": 20}).json()

        # Move the ledger after the fact.
        for outcome in session.execute(select(Outcome)).scalars():
            outcome.amount_recovered = Decimal("999999.00")
        session.commit()

        reopened = client.get(f"/batch/runs/{original['batch_id']}").json()
        assert reopened["run"]["amount_recovered"] == original["money"]["amount_recovered"]
        assert reopened["snapshot"]["money"] == original["money"]

    def test_a_missing_run_is_a_404_with_a_readable_message(self, client_and_session):
        client, _ = client_and_session
        response = client.get("/batch/runs/no_such_run")
        assert response.status_code == 404
        detail = response.json()["detail"]
        assert "no longer available" in detail
        # A merchant-facing message, not an internal identifier dump.
        assert "no_such_run" not in detail

    def test_reopening_is_read_only(self, client_and_session):
        client, session = client_and_session
        original = client.post("/batch", json={"count": 20}).json()
        before = len(list(session.execute(select(RecoveryRun)).scalars()))
        for _ in range(3):
            client.get(f"/batch/runs/{original['batch_id']}")
        assert len(list(session.execute(select(RecoveryRun)).scalars())) == before

    def test_the_run_routes_expose_no_write_method(self):
        methods = set()
        for route in app.routes:
            if getattr(route, "path", "").startswith("/batch/runs"):
                methods |= set(getattr(route, "methods", set()))
        assert methods <= {"GET", "HEAD", "OPTIONS"}


class TestHistoryIsNotASecondSourceOfTruth:
    def test_the_ledger_still_answers_what_is_true_now(self, client_and_session):
        """History says what a run reported; /events says what is true today.
        Both must be available and they must not be confused for each other."""
        client, _ = client_and_session
        original = client.post("/batch", json={"count": 20}).json()

        live = client.get("/events", params={"limit": 1}).json()["money"]
        stored = client.get(f"/batch/runs/{original['batch_id']}").json()["run"]

        # With one run and an untouched ledger the two agree exactly.
        assert Decimal(live["amount_recovered"]) == Decimal(stored["amount_recovered"])
        assert Decimal(live["amount_at_risk"]) == Decimal(stored["amount_at_risk"])

    def test_two_runs_accumulate_in_the_ledger_but_not_in_one_record(
        self, client_and_session
    ):
        client, _ = client_and_session
        first = client.post("/batch", json={"count": 20}).json()
        second = client.post("/batch", json={"count": 20}).json()

        live = client.get("/events", params={"limit": 1}).json()["money"]
        combined = Decimal(first["money"]["amount_at_risk"]) + Decimal(
            second["money"]["amount_at_risk"]
        )
        assert Decimal(live["amount_at_risk"]) == combined

        stored = client.get(f"/batch/runs/{first['batch_id']}").json()["run"]
        assert Decimal(stored["amount_at_risk"]) == Decimal(first["money"]["amount_at_risk"])
