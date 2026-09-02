"""Single-case dry run. BUILD_SPEC Section 10.

    cd backend && PYTHONPATH=. pytest -q tests/test_dry_run.py

A testing console is only worth anything if it exercises the REAL pipeline.
These tests assert that: the same diagnosis engine, the same policy gate, the
same ledger. If the console could reach a verdict the batch would not, it would
prove nothing.
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
from app.models import AuditLog, Decision, Diagnosis, RiskEvent


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


def case(**overrides):
    body = {
        "event_type": "payment_degraded",
        "customer_name": "Meera Nair",
        "amount": "8500.00",
        "gateway_error_code": "BAD_REQUEST_CARD_EXPIRED",
        "attempts_already_made": 0,
        "payment_success_rate": 0.7,
        "avg_payment_delay_days": 3.0,
        "do_not_contact": False,
    }
    body.update(overrides)
    return body


class TestItRunsTheRealPipeline:
    def test_a_case_runs_end_to_end(self, client_and_session):
        client, _ = client_and_session
        response = client.post("/batch/dry-run", json=case())
        assert response.status_code == 200
        assert response.json()["steps"]

    def test_it_persists_a_real_event(self, client_and_session):
        """A trace that rolled itself back could not honestly show an audit
        entry, and would prove less."""
        client, session = client_and_session
        body = client.post("/batch/dry-run", json=case()).json()
        stored = session.get(RiskEvent, body["event_id"])
        assert stored is not None
        assert stored.amount == Decimal("8500.00")

    def test_the_real_diagnosis_engine_ran(self, client_and_session):
        client, session = client_and_session
        body = client.post("/batch/dry-run", json=case()).json()
        stored = session.get(Diagnosis, body["event_id"])
        assert stored is not None

    def test_the_real_decision_engine_ran(self, client_and_session):
        client, session = client_and_session
        body = client.post("/batch/dry-run", json=case()).json()
        decisions = list(
            session.execute(
                select(Decision).where(Decision.event_id == body["event_id"])
            ).scalars()
        )
        assert decisions

    def test_it_writes_a_real_audit_trail(self, client_and_session):
        client, session = client_and_session
        body = client.post("/batch/dry-run", json=case()).json()
        entries = list(
            session.execute(
                select(AuditLog).where(AuditLog.event_id == body["event_id"])
            ).scalars()
        )
        assert len(entries) == body["audit_entries"] > 0

    def test_the_case_appears_in_the_normal_feed(self, client_and_session):
        """It is a real case, so it belongs with the others."""
        client, _ = client_and_session
        body = client.post("/batch/dry-run", json=case()).json()
        feed = client.get("/events", params={"limit": 50}).json()
        assert any(item["id"] == body["event_id"] for item in feed["items"])

    def test_the_trace_is_read_back_not_predicted(self, client_and_session):
        """Each step must match what was stored, not what the inputs implied."""
        client, session = client_and_session
        body = client.post("/batch/dry-run", json=case()).json()
        stored = session.get(Diagnosis, body["event_id"])
        diagnosis_step = next(s for s in body["steps"] if s["stage"] == "diagnosis")
        assert diagnosis_step["outcome"] == stored.root_cause_code.value


class TestInputsGenuinelyChangeTheOutcome:
    """Every field must matter, or the console is theatre."""

    def test_do_not_contact_blocks_at_the_policy_gate(self, client_and_session):
        client, _ = client_and_session
        body = client.post(
            "/batch/dry-run", json=case(do_not_contact=True, event_type="invoice_overdue")
        ).json()
        policy = next(s for s in body["steps"] if s["stage"] == "policy")
        assert policy["status"] == "blocked"

    def test_a_permitted_case_is_not_blocked(self, client_and_session):
        client, _ = client_and_session
        body = client.post("/batch/dry-run", json=case(do_not_contact=False)).json()
        policy = next(s for s in body["steps"] if s["stage"] == "policy")
        assert policy["status"] == "passed"

    def test_the_failure_reason_changes_the_diagnosis(self, client_and_session):
        client, _ = client_and_session
        expired = client.post(
            "/batch/dry-run", json=case(gateway_error_code="BAD_REQUEST_CARD_EXPIRED")
        ).json()
        declined = client.post(
            "/batch/dry-run", json=case(gateway_error_code="GATEWAY_ERROR_ISSUER_DECLINED")
        ).json()

        first = next(s for s in expired["steps"] if s["stage"] == "diagnosis")["outcome"]
        second = next(s for s in declined["steps"] if s["stage"] == "diagnosis")["outcome"]
        assert first != second

    def test_the_event_type_changes_the_action(self, client_and_session):
        client, _ = client_and_session
        payment = client.post("/batch/dry-run", json=case()).json()
        subscription = client.post(
            "/batch/dry-run",
            json=case(event_type="subscription_failed", gateway_error_code=None),
        ).json()

        first = next(s for s in payment["steps"] if s["stage"] == "decision")["outcome"]
        second = next(
            s for s in subscription["steps"] if s["stage"] == "decision"
        )["outcome"]
        assert first != second

    def test_the_amount_is_carried_through_exactly(self, client_and_session):
        client, _ = client_and_session
        body = client.post("/batch/dry-run", json=case(amount="1234.56")).json()
        assert Decimal(body["amount_at_risk"]) == Decimal("1234.56")


class TestTheTraceIsHonest:
    def test_it_covers_the_whole_lifecycle(self, client_and_session):
        client, _ = client_and_session
        stages = {s["stage"] for s in client.post("/batch/dry-run", json=case()).json()["steps"]}
        for stage in ("detection", "diagnosis", "decision", "policy", "verification", "recovery", "audit"):
            assert stage in stages

    def test_a_stage_that_did_not_happen_is_reported_as_skipped(self, client_and_session):
        """Not quietly omitted, and never dressed up as success."""
        client, _ = client_and_session
        body = client.post(
            "/batch/dry-run", json=case(do_not_contact=True, event_type="invoice_overdue")
        ).json()
        promise = next(s for s in body["steps"] if s["title"] == "Promise to pay")
        assert promise["status"] == "skipped"
        assert promise["outcome"] == "none"

    def test_nothing_recovered_is_reported_as_nothing(self, client_and_session):
        client, _ = client_and_session
        body = client.post(
            "/batch/dry-run", json=case(do_not_contact=True, event_type="invoice_overdue")
        ).json()
        assert Decimal(body["amount_recovered"]) == Decimal("0.00")

    def test_a_rejected_case_returns_a_readable_message(self, client_and_session):
        client, _ = client_and_session
        response = client.post("/batch/dry-run", json=case(amount="-5"))
        assert response.status_code == 422

    def test_two_runs_create_two_separate_cases(self, client_and_session):
        """Distinct source references, so one test never overwrites another."""
        client, _ = client_and_session
        first = client.post("/batch/dry-run", json=case()).json()
        second = client.post("/batch/dry-run", json=case()).json()
        assert first["event_id"] != second["event_id"]
