"""GET /policies and PUT /policies. BUILD_SPEC Sections 4, 6 and 10.

    cd backend && PYTHONPATH=. pytest -q tests/test_policies.py

Two properties matter most here.

Policies are VERSIONED, not mutated. Decision.policy_version pins every past
decision to the policy that gated it, so a PUT must insert a new row and leave
the old one intact — otherwise editing a threshold today silently rewrites the
recorded explanation of a decision made yesterday.

The defaults the API reports must be the ENGINE'S defaults. If /policies
invented its own numbers, the UI would show configuration the engine does not
actually use.
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
from app.engine import policy_engine
from app.enums import EventType
from app.main import app
from app.models import Merchant, Policy


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
    session.add(Merchant(id="mer_p", name="Policy Merchant"))
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    logging.disable(logging.CRITICAL)
    yield TestClient(app), session
    logging.disable(logging.NOTSET)
    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


def payload(**overrides):
    body = {
        "merchant_id": "mer_p",
        "event_type": "payment_degraded",
        "max_attempts": 3,
        "cooldown_hours": 12,
        "amount_threshold": "15000.00",
        "recovery_probability_threshold": 0.1,
        "contact_limit_per_channel": 2,
        "escalation_ceiling": 2,
    }
    body.update(overrides)
    return body


class TestGetPolicies:
    def test_all_five_event_types_are_returned(self, client_and_session):
        """The UI shows the complete configuration surface, not only saved rows."""
        client, _ = client_and_session
        body = client.get("/policies").json()
        assert {item["event_type"] for item in body["items"]} == {t.value for t in EventType}

    def test_unconfigured_types_are_marked_as_defaults(self, client_and_session):
        client, _ = client_and_session
        body = client.get("/policies").json()
        assert all(item["is_default"] for item in body["items"])

    def test_the_defaults_are_the_engines_own(self, client_and_session):
        """Not a second set of numbers invented by the router."""
        client, session = client_and_session

        class _Probe:
            merchant_id = "mer_p"
            type = EventType.PAYMENT_DEGRADED

        engine_default = policy_engine.resolve_policy(session, _Probe())
        item = next(
            i
            for i in client.get("/policies").json()["items"]
            if i["event_type"] == "payment_degraded"
        )
        assert item["max_attempts"] == engine_default.max_attempts
        assert item["cooldown_hours"] == engine_default.cooldown_hours
        assert Decimal(item["amount_threshold"]) == engine_default.amount_threshold
        assert item["escalation_ceiling"] == engine_default.escalation_ceiling

    def test_a_saved_policy_replaces_its_default(self, client_and_session):
        client, _ = client_and_session
        client.put("/policies", json=payload(max_attempts=7))
        item = next(
            i
            for i in client.get("/policies").json()["items"]
            if i["event_type"] == "payment_degraded"
        )
        assert item["max_attempts"] == 7
        assert item["is_default"] is False

    def test_the_merchant_is_resolved_without_being_supplied(self, client_and_session):
        """A judge should not need to know a merchant id to open the page."""
        client, _ = client_and_session
        assert client.get("/policies").json()["merchant_id"] == "mer_p"

    def test_money_is_a_string(self, client_and_session):
        client, _ = client_and_session
        for item in client.get("/policies").json()["items"]:
            assert isinstance(item["amount_threshold"], str)
            Decimal(item["amount_threshold"])


class TestPutPolicy:
    def test_it_persists(self, client_and_session):
        client, session = client_and_session
        assert client.put("/policies", json=payload(max_attempts=5)).status_code == 200
        stored = session.execute(select(Policy)).scalars().all()
        assert len(stored) == 1
        assert stored[0].max_attempts == 5

    def test_the_first_save_is_version_one(self, client_and_session):
        client, _ = client_and_session
        assert client.put("/policies", json=payload()).json()["policy_version"] == 1

    def test_each_save_creates_a_new_version(self, client_and_session):
        client, _ = client_and_session
        versions = [
            client.put("/policies", json=payload(max_attempts=n)).json()["policy_version"]
            for n in (1, 2, 3)
        ]
        assert versions == [1, 2, 3]

    def test_previous_versions_survive(self, client_and_session):
        """Decision.policy_version pins history; overwriting would rewrite it."""
        client, session = client_and_session
        client.put("/policies", json=payload(max_attempts=1))
        client.put("/policies", json=payload(max_attempts=9))
        rows = session.execute(select(Policy).order_by(Policy.policy_version)).scalars().all()
        assert [r.max_attempts for r in rows] == [1, 9]

    def test_get_returns_the_latest_version(self, client_and_session):
        client, _ = client_and_session
        client.put("/policies", json=payload(max_attempts=1))
        client.put("/policies", json=payload(max_attempts=9))
        item = next(
            i
            for i in client.get("/policies").json()["items"]
            if i["event_type"] == "payment_degraded"
        )
        assert item["max_attempts"] == 9
        assert item["policy_version"] == 2

    def test_types_are_scoped_independently(self, client_and_session):
        client, _ = client_and_session
        client.put("/policies", json=payload(event_type="invoice_overdue", max_attempts=8))
        items = {i["event_type"]: i for i in client.get("/policies").json()["items"]}
        assert items["invoice_overdue"]["max_attempts"] == 8
        assert items["payment_degraded"]["is_default"] is True

    def test_an_unknown_merchant_is_created(self, client_and_session):
        """A clean install must be configurable from the UI with no seeding."""
        client, session = client_and_session
        response = client.put("/policies", json=payload(merchant_id="mer_new"))
        assert response.status_code == 200
        assert session.get(Merchant, "mer_new") is not None

    def test_the_saved_policy_is_what_the_engine_then_uses(self, client_and_session):
        """The whole point: the UI must change real engine behaviour, not a
        parallel copy of the settings."""
        client, session = client_and_session
        client.put("/policies", json=payload(max_attempts=6, cooldown_hours=99))

        class _Probe:
            merchant_id = "mer_p"
            type = EventType.PAYMENT_DEGRADED

        resolved = policy_engine.resolve_policy(session, _Probe())
        assert resolved.max_attempts == 6
        assert resolved.cooldown_hours == 99


class TestValidation:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("max_attempts", -1),
            ("max_attempts", 99),
            ("cooldown_hours", -5),
            ("recovery_probability_threshold", 1.5),
            ("recovery_probability_threshold", -0.1),
            ("contact_limit_per_channel", -1),
            ("amount_threshold", "-100.00"),
        ],
    )
    def test_out_of_range_values_are_rejected(self, client_and_session, field, value):
        client, _ = client_and_session
        assert client.put("/policies", json=payload(**{field: value})).status_code == 422

    def test_an_escalation_ceiling_above_l2_is_rejected_not_clamped(
        self, client_and_session
    ):
        """Section 6 caps auto-escalation at L2. A merchant who types 5 should
        be told it is not permitted, not quietly given 2."""
        client, _ = client_and_session
        assert client.put("/policies", json=payload(escalation_ceiling=5)).status_code == 422

    def test_an_unknown_event_type_is_rejected(self, client_and_session):
        client, _ = client_and_session
        assert client.put("/policies", json=payload(event_type="crypto_rugpull")).status_code == 422

    def test_a_rejected_update_persists_nothing(self, client_and_session):
        client, session = client_and_session
        client.put("/policies", json=payload(escalation_ceiling=9))
        assert session.execute(select(Policy)).scalars().all() == []

    def test_the_error_explains_the_problem(self, client_and_session):
        client, _ = client_and_session
        body = client.put("/policies", json=payload(max_attempts=99)).json()
        assert "detail" in body
