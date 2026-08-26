"""GET /scripts/{event_id}. BUILD_SPEC Sections 7 and 10.

    cd backend && PYTHONPATH=. pytest -q tests/test_scripts.py

The endpoint is an INSPECTION, not an action. Section 7 is explicit that
rendering a script must not bypass policy or stopping-rule enforcement, and the
converse matters just as much: opening the scripts page must not change what the
engine has done. Both directions are tested.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.engine import template_engine
from app.engine.template_engine import IST
from app.main import app
from app.models import AuditLog, Decision, PaymentAttempt, RiskEvent, StoppingRuleState
from app.routers.batch import run_batch
from app.schemas.batch import BatchRequest


@pytest.fixture(scope="module")
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


def any_event_id(session) -> str:
    return list(session.execute(select(RiskEvent).limit(1)).scalars())[0].id


#: Fixed instants either side of the Section 7 contact window (08:00-19:00 IST).
MIDDAY_IST = datetime(2026, 8, 26, 12, 0, tzinfo=IST).astimezone(timezone.utc)
NIGHT_IST = datetime(2026, 8, 26, 22, 30, tzinfo=IST).astimezone(timezone.utc)


def _freeze_contact_clock(monkeypatch, moment) -> None:
    """Evaluate the REAL contact-window rule against a fixed instant.

    The endpoint deliberately uses the current time, which is correct
    production behaviour and must stay that way — but it made these tests pass
    before 19:00 IST and fail after it. One shipped a genuine failure and one
    silently skipped.

    This supplies a clock; it does not disable a rule. ``check_contact_window``
    still runs in full, still reads compliance_rules.yaml, and still returns a
    real verdict — it is simply told what time it is, using the ``now``
    parameter the engine already exposes for exactly this purpose. Stubbing the
    rule itself, or adding a query parameter to the production endpoint so a
    test could bypass it, would both be worse.
    """
    real = template_engine.check_contact_window
    monkeypatch.setattr(
        template_engine,
        "check_contact_window",
        lambda now=None: real(now or moment),
    )


@pytest.fixture()
def at_midday(monkeypatch):
    """Inside the permitted contact window."""
    _freeze_contact_clock(monkeypatch, MIDDAY_IST)


@pytest.fixture()
def after_hours(monkeypatch):
    """Outside the permitted contact window."""
    _freeze_contact_clock(monkeypatch, NIGHT_IST)


class TestScriptEndpoint:
    def test_it_returns_a_script_for_a_real_event(self, client_and_session):
        client, session = client_and_session
        body = client.get(f"/scripts/{any_event_id(session)}").json()
        assert body["event_id"]
        assert body["reasoning"]

    def test_a_missing_event_is_a_404(self, client_and_session):
        client, _ = client_and_session
        assert client.get("/scripts/evt_nope").status_code == 404

    def test_every_response_carries_the_full_compliance_verdict(
        self, client_and_session
    ):
        """Pass or fail, the caller can see exactly what was checked."""
        client, session = client_and_session
        for event in list(session.execute(select(RiskEvent).limit(12)).scalars()):
            body = client.get(f"/scripts/{event.id}").json()
            rule_ids = {c["rule_id"] for c in body["compliance_checks"]}
            assert {"contact_time_window", "frequency_cap", "no_false_urgency"} <= rule_ids
            for check in body["compliance_checks"]:
                assert check["detail"], "a check with no explanation is not auditable"

    def test_a_non_compliant_response_carries_no_script_text(
        self, client_and_session, after_hours
    ):
        """A refused script that still shipped the text could be sent anyway.

        The after_hours fixture guarantees there is something to assert on. Run
        against the wall clock this passed vacuously all day — the loop simply
        found no refusals to check — and only did any work in the evening.
        """
        client, session = client_and_session
        refused = []
        for event in list(session.execute(select(RiskEvent).limit(10)).scalars()):
            body = client.get(f"/scripts/{event.id}").json()
            if not body["compliant"]:
                refused.append(body)

        assert refused, "outside the contact window every script must be refused"
        for body in refused:
            assert body["script"] == ""
            assert body["failure_reason"]

    def test_a_compliant_response_carries_real_hinglish(
        self, client_and_session, at_midday
    ):
        """Inside the contact window, the endpoint returns real rendered text.

        The at_midday fixture fixes the clock; every other rule still runs
        normally, so an event refused for a frequency cap or false urgency is
        still refused here.
        """
        client, session = client_and_session
        found = False
        for event in list(session.execute(select(RiskEvent).limit(40)).scalars()):
            body = client.get(f"/scripts/{event.id}").json()
            if body["compliant"]:
                assert body["script"]
                assert any(w in body["script"] for w in ("Namaste", "aapka", "hai", "kar"))
                found = True
                break
        assert found, "no compliant script in this batch"

    def test_tone_and_urgency_are_reported(self, client_and_session):
        client, session = client_and_session
        body = client.get(f"/scripts/{any_event_id(session)}").json()
        assert body["tone"] in ("friendly", "neutral", "formal")
        assert body["urgency"] in ("low", "medium", "high")

    def test_the_template_key_traces_the_wording_to_yaml(
        self, client_and_session, at_midday
    ):
        """The wording must be traceable to a YAML entry, not to code.

        Previously this skipped whenever the suite ran after 19:00 IST, which
        meant it silently stopped protecting anything for half of every day.
        """
        client, session = client_and_session
        for event in list(session.execute(select(RiskEvent).limit(20)).scalars()):
            body = client.get(f"/scripts/{event.id}").json()
            if body["compliant"]:
                assert body["template_key"]
                assert "." in body["template_key"]
                return
        raise AssertionError("no compliant script in this batch")

    def test_urgency_never_exceeds_the_escalation_level(self, client_and_session):
        """Rule 4, verified against real persisted stopping-rule state."""
        client, session = client_and_session
        rank = {"low": 0, "medium": 1, "high": 2}
        for event in list(session.execute(select(RiskEvent).limit(40)).scalars()):
            body = client.get(f"/scripts/{event.id}").json()
            state = session.get(StoppingRuleState, event.id)
            level = state.escalation_level if state else 0
            assert rank[body["urgency"]] <= level or body["urgency"] == "low"

    def test_the_reasoning_is_present_even_when_the_script_is_refused(
        self, client_and_session
    ):
        client, session = client_and_session
        for event in list(session.execute(select(RiskEvent).limit(40)).scalars()):
            body = client.get(f"/scripts/{event.id}").json()
            assert body["reasoning"], "reasoning must always be available"

    def test_no_secrets_appear(self, client_and_session):
        client, session = client_and_session
        blob = client.get(f"/scripts/{any_event_id(session)}").text.lower()
        for needle in ("rzp_test_", "rzp_live_", "password", "api_key", "secret"):
            assert needle not in blob


class TestScriptGenerationIsSideEffectFree:
    """Opening the scripts page must not change what the engine has done."""

    def test_generating_a_script_writes_no_audit_row(self, client_and_session):
        client, session = client_and_session
        before = len(list(session.execute(select(AuditLog)).scalars()))
        for event in list(session.execute(select(RiskEvent).limit(10)).scalars()):
            client.get(f"/scripts/{event.id}")
        assert len(list(session.execute(select(AuditLog)).scalars())) == before

    def test_generating_a_script_creates_no_attempt(self, client_and_session):
        client, session = client_and_session
        before = len(list(session.execute(select(PaymentAttempt)).scalars()))
        for event in list(session.execute(select(RiskEvent).limit(10)).scalars()):
            client.get(f"/scripts/{event.id}")
        assert len(list(session.execute(select(PaymentAttempt)).scalars())) == before

    def test_generating_a_script_does_not_move_the_state_machine(
        self, client_and_session
    ):
        client, session = client_and_session
        before = {e.id: e.status for e in session.execute(select(RiskEvent)).scalars()}
        for event_id in list(before)[:10]:
            client.get(f"/scripts/{event_id}")
        after = {e.id: e.status for e in session.execute(select(RiskEvent)).scalars()}
        assert before == after

    def test_generating_a_script_creates_no_decision(self, client_and_session):
        client, session = client_and_session
        before = len(list(session.execute(select(Decision)).scalars()))
        for event in list(session.execute(select(RiskEvent).limit(10)).scalars()):
            client.get(f"/scripts/{event.id}")
        assert len(list(session.execute(select(Decision)).scalars())) == before

    def test_the_production_endpoint_uses_the_real_clock(self):
        """The router must NOT pin the time.

        generate_script accepts an optional `now` so compliance can be tested
        deterministically, and the tests above use it. Production must never
        pass it: an endpoint with a frozen clock would answer "inside the
        contact window" at 3am. This is the guard against someone resolving a
        future timing flake by freezing the wrong side of the boundary.
        """
        import ast
        import inspect

        from app.routers import scripts as scripts_router

        tree = ast.parse(inspect.getsource(scripts_router))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "generate_script"
        ]
        assert calls, "the router no longer calls generate_script"
        for call in calls:
            passed = {kw.arg for kw in call.keywords}
            assert "now" not in passed, (
                "the production endpoint pinned the clock; it must use the "
                "real current time"
            )

    def test_the_endpoint_exposes_no_write_method(self, client_and_session):
        methods = set()
        for route in app.routes:
            if getattr(route, "path", "").startswith("/scripts"):
                methods |= set(getattr(route, "methods", set()))
        assert methods <= {"GET", "HEAD", "OPTIONS"}

    def test_repeated_calls_are_identical(self, client_and_session):
        """Deterministic: there is no model here."""
        client, session = client_and_session
        event_id = any_event_id(session)
        first = client.get(f"/scripts/{event_id}").json()
        second = client.get(f"/scripts/{event_id}").json()
        assert first["script"] == second["script"]
        assert first["reasoning"] == second["reasoning"]
