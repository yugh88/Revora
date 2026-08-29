"""Email, SMS and Voice recovery contacts. BUILD_SPEC Sections 7 and 10.

    cd backend && PYTHONPATH=. pytest -q tests/test_communications.py

The risky claims a communication layer can make are all claims about the outside
world: that a message went out, that it was delivered, that a customer replied.
Revora has no provider, so every one of those would be false. These tests exist
to make the false versions impossible to express:

* the status vocabulary contains no "sent" and no "delivered",
* every record is marked simulated,
* a blocked message carries no text that could be copied and sent,
* a response is only ever recorded by an explicit simulation.
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
from app.engine import template_engine
from app.engine.template_engine import IST
from app.enums import Channel, CommunicationStatus, CustomerResponse, EventStatus
from app.main import app
from app.models import CommunicationLog, Outcome, PromiseToPay, RiskEvent
from app.routers.batch import run_batch
from app.schemas.batch import BatchRequest


#: Midday IST — inside the permitted contact window.
MIDDAY = datetime(2026, 8, 28, 12, 0, tzinfo=IST).astimezone(timezone.utc)


@pytest.fixture(autouse=True)
def _inside_contact_hours(monkeypatch):
    """Evaluate the REAL contact-window rule against a fixed, in-window time.

    Without this these tests pass before 19:00 IST and skip after it, which
    means they stop protecting anything for half of every day. The rule itself
    still runs in full — it is only told what time it is, through the ``now``
    parameter the engine already exposes. Every other compliance check is
    untouched, so a message refused for frequency or urgency is still refused.
    """
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
    raise AssertionError("no open case available")


def prepared(client, session, channel: str = "email"):
    """Prepare a message, skipping cases compliance happens to refuse."""
    for event in session.execute(select(RiskEvent)).scalars():
        if event.status in (EventStatus.RECOVERED, EventStatus.UNRECOVERABLE):
            continue
        body = client.post(
            "/communications/prepare", json={"event_id": event.id, "channel": channel}
        ).json()
        if body["status"] == "prepared":
            return event, body
    pytest.skip("compliance refused every case in this batch")


# --------------------------------------------------------------------------- #
# Nothing is ever sent
# --------------------------------------------------------------------------- #


class TestNothingIsEverSent:
    def test_the_vocabulary_contains_no_sent_or_delivered(self):
        """The strongest guarantee available: the UI cannot render a value that
        does not exist."""
        values = {status.value for status in CommunicationStatus}
        assert "sent" not in values
        assert "delivered" not in values
        assert values == {"prepared", "simulated", "blocked"}

    def test_every_record_is_marked_simulated(self, client_and_session):
        client, session = client_and_session
        _, body = prepared(client, session)
        assert body["is_simulated"] is True
        stored = session.get(CommunicationLog, body["id"])
        assert stored.is_simulated is True

    def test_simulating_a_send_does_not_claim_delivery(self, client_and_session):
        client, session = client_and_session
        _, body = prepared(client, session)
        sent = client.post(f"/communications/{body['id']}/simulate-send").json()
        assert sent["status"] == "simulated"
        assert sent["is_simulated"] is True

    def test_no_provider_is_contacted(self):
        """The module must not import an HTTP client or a mail library — that
        is what makes 'no customer was contacted' verifiable rather than a
        promise in a comment."""
        import ast
        import inspect

        from app.routers import communications as module

        tree = ast.parse(inspect.getsource(module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for banned in ("smtplib", "requests", "httpx", "twilio", "boto3", "urllib", "email"):
            assert not any(name.startswith(banned) for name in imported), banned


# --------------------------------------------------------------------------- #
# Preparing
# --------------------------------------------------------------------------- #


class TestPreparing:
    def test_a_message_can_be_prepared(self, client_and_session):
        client, session = client_and_session
        _, body = prepared(client, session)
        assert body["body"]
        assert body["status"] == "prepared"

    def test_it_is_bound_to_the_recovery_case(self, client_and_session):
        client, session = client_and_session
        event, body = prepared(client, session)
        assert body["event_id"] == event.id
        assert body["customer_name"] == (event.raw_signal or {}).get("customer_name")

    def test_the_channel_can_be_chosen(self, client_and_session):
        client, session = client_and_session
        for channel in ("email", "sms", "voice_script"):
            event = open_event(session)
            body = client.post(
                "/communications/prepare",
                json={"event_id": event.id, "channel": channel},
            ).json()
            assert body["channel"] == channel

    def test_the_channel_defaults_from_the_decision(self, client_and_session):
        """The channel follows the action the engine already chose, rather than
        being an unrelated pick."""
        client, session = client_and_session
        event = open_event(session)
        body = client.post("/communications/prepare", json={"event_id": event.id}).json()
        assert body["channel"] in {c.value for c in Channel}

    def test_an_unreachable_channel_is_refused(self, client_and_session):
        client, session = client_and_session
        event = open_event(session)
        response = client.post(
            "/communications/prepare", json={"event_id": event.id, "channel": "external"}
        )
        assert response.status_code == 400

    def test_an_unknown_case_is_a_404(self, client_and_session):
        client, _ = client_and_session
        response = client.post("/communications/prepare", json={"event_id": "nope"})
        assert response.status_code == 404

    def test_preparing_writes_no_recovery(self, client_and_session):
        """Writing a message is not collecting money."""
        client, session = client_and_session
        event = open_event(session)
        before = session.get(Outcome, event.id)
        before_amount = before.amount_recovered if before else Decimal("0.00")

        client.post("/communications/prepare", json={"event_id": event.id})

        after = session.get(Outcome, event.id)
        assert (after.amount_recovered if after else Decimal("0.00")) == before_amount


class TestComplianceIsNotBypassed:
    def test_a_blocked_message_carries_no_text(self, client_and_session, monkeypatch):
        """A refused message that still shipped its text could be copied and
        sent, which would defeat the gate entirely."""
        from app.engine import template_engine

        monkeypatch.setattr(
            template_engine,
            "render_script",
            lambda slots, tone, urgency: ("We will take legal action", "stub"),
        )
        client, session = client_and_session
        event = open_event(session)
        body = client.post("/communications/prepare", json={"event_id": event.id}).json()

        assert body["status"] == "blocked"
        assert body["body"] == ""
        assert body["blocked_reason"]

    def test_a_blocked_message_cannot_be_sent(self, client_and_session, monkeypatch):
        from app.engine import template_engine

        monkeypatch.setattr(
            template_engine,
            "render_script",
            lambda slots, tone, urgency: ("We will take legal action", "stub"),
        )
        client, session = client_and_session
        event = open_event(session)
        body = client.post("/communications/prepare", json={"event_id": event.id}).json()

        response = client.post(f"/communications/{body['id']}/simulate-send")
        assert response.status_code == 400

    def test_the_body_comes_from_the_compliance_checked_engine(self, client_and_session):
        """Same source as the Recovery Messages page — not a second writer."""
        client, session = client_and_session
        event, body = prepared(client, session)
        script = client.get(f"/scripts/{event.id}").json()
        if script["compliant"]:
            assert body["body"] == script["script"]


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #


class TestSimulatedResponses:
    def test_a_response_needs_the_message_to_have_been_sent(self, client_and_session):
        client, session = client_and_session
        _, body = prepared(client, session)
        response = client.post(
            f"/communications/{body['id']}/simulate-response",
            json={"response": "no_response"},
        )
        assert response.status_code == 400

    def test_a_response_is_never_inferred(self, client_and_session):
        """Preparing and sending must leave the reply field empty. A customer
        who has not answered has simply not answered."""
        client, session = client_and_session
        _, body = prepared(client, session)
        sent = client.post(f"/communications/{body['id']}/simulate-send").json()
        assert sent["customer_response"] is None
        assert sent["responded_at"] is None

    def test_a_simulated_reply_is_recorded(self, client_and_session):
        client, session = client_and_session
        _, body = prepared(client, session)
        client.post(f"/communications/{body['id']}/simulate-send")
        answered = client.post(
            f"/communications/{body['id']}/simulate-response",
            json={"response": "no_response"},
        ).json()
        assert answered["customer_response"] == "no_response"
        assert answered["responded_at"]

    def test_no_response_creates_no_promise(self, client_and_session):
        client, session = client_and_session
        _, body = prepared(client, session)
        client.post(f"/communications/{body['id']}/simulate-send")
        answered = client.post(
            f"/communications/{body['id']}/simulate-response",
            json={"response": "no_response"},
        ).json()
        assert answered["promise_id"] is None
        assert list(session.execute(select(PromiseToPay)).scalars()) == []


class TestPromiseComesFromTheConversation:
    """A promise must be a consequence of the exchange, not something typed in
    on the customer's behalf."""

    def test_promising_to_pay_creates_a_real_promise(self, client_and_session):
        client, session = client_and_session
        event, body = prepared(client, session)
        client.post(f"/communications/{body['id']}/simulate-send")

        answered = client.post(
            f"/communications/{body['id']}/simulate-response",
            json={
                "response": "promised_to_pay",
                "promised_amount": "1200.00",
                "promised_date": (utcnow() + timedelta(days=4)).isoformat(),
            },
        ).json()

        assert answered["promise_id"]
        promise = session.get(PromiseToPay, answered["promise_id"])
        assert promise is not None
        assert promise.event_id == event.id
        assert promise.promised_amount == Decimal("1200.00")

    def test_the_promise_appears_on_the_promises_endpoint(self, client_and_session):
        client, session = client_and_session
        _, body = prepared(client, session)
        client.post(f"/communications/{body['id']}/simulate-send")
        client.post(
            f"/communications/{body['id']}/simulate-response",
            json={"response": "promised_to_pay", "promised_amount": "900.00"},
        )
        promises = client.get("/promises").json()
        assert promises["total"] == 1
        assert Decimal(promises["total_promised"]) == Decimal("900.00")

    def test_the_promise_defaults_to_the_whole_balance(self, client_and_session):
        client, session = client_and_session
        event, body = prepared(client, session)
        client.post(f"/communications/{body['id']}/simulate-send")
        answered = client.post(
            f"/communications/{body['id']}/simulate-response",
            json={"response": "promised_to_pay"},
        ).json()
        promise = session.get(PromiseToPay, answered["promise_id"])
        assert promise.promised_amount == event.amount

    def test_a_promise_still_moves_no_money(self, client_and_session):
        client, session = client_and_session
        event, body = prepared(client, session)
        before = session.get(Outcome, event.id)
        before_amount = before.amount_recovered if before else Decimal("0.00")

        client.post(f"/communications/{body['id']}/simulate-send")
        client.post(
            f"/communications/{body['id']}/simulate-response",
            json={"response": "promised_to_pay", "promised_amount": "500.00"},
        )

        after = session.get(Outcome, event.id)
        assert (after.amount_recovered if after else Decimal("0.00")) == before_amount


class TestListing:
    def test_it_lists_recovery_contacts(self, client_and_session):
        client, session = client_and_session
        prepared(client, session, "email")
        prepared(client, session, "sms")
        body = client.get("/communications").json()
        assert body["total"] >= 2

    def test_the_channel_filter_narrows(self, client_and_session):
        client, session = client_and_session
        prepared(client, session, "email")
        prepared(client, session, "sms")
        body = client.get("/communications", params={"channel": "sms"}).json()
        assert all(item["channel"] == "sms" for item in body["items"])

    def test_an_empty_list_is_not_an_error(self):
        """Checked on a system where nothing has run.

        The shared fixture runs a batch, and a batch now records the contacts
        the agent decided to make — so asserting emptiness there would be
        asserting that the agent does nothing.
        """
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
            body = TestClient(app).get("/communications").json()
            assert body["total"] == 0
            assert body["items"] == []
        finally:
            app.dependency_overrides.clear()
            session.close()
            engine.dispose()

    def test_no_record_claims_delivery(self, client_and_session):
        client, session = client_and_session
        _, body = prepared(client, session)
        client.post(f"/communications/{body['id']}/simulate-send")
        blob = client.get("/communications").text.lower()
        for claim in ('"sent"', "delivered", "call_duration", "provider"):
            assert claim not in blob


class TestRecoveryRunsFeedCommunications:
    """A run must surface the people the agent decided to contact.

    Before this, the batch executed contact actions but recorded nothing, so a
    merchant had to create the contact by hand to see it — which made an
    autonomous agent look like an address book with a send button.
    """

    def test_a_run_records_the_contacts_it_decided_to_make(self, client_and_session):
        client, session = client_and_session
        rows = list(session.execute(select(CommunicationLog)).scalars())
        assert rows, "the batch decided to contact people but recorded nothing"

    def test_those_contacts_appear_in_the_listing(self, client_and_session):
        client, _ = client_and_session
        body = client.get("/communications").json()
        assert body["total"] > 0

    def test_each_carries_the_agent_s_channel_reasoning(self, client_and_session):
        """A merchant must be able to see WHY the agent picked that channel."""
        client, session = client_and_session
        for row in session.execute(select(CommunicationLog)).scalars():
            assert row.channel_reason

    def test_only_reachable_channels_are_used(self, client_and_session):
        from app.routers.communications import CONTACT_CHANNELS

        _, session = client_and_session
        for row in session.execute(select(CommunicationLog)).scalars():
            assert row.channel in CONTACT_CHANNELS

    def test_a_run_never_claims_a_send_it_could_not_make(self, client_and_session):
        """Compliance still gates every recorded contact: a refused one is
        stored blocked with no body, exactly as if prepared by hand."""
        _, session = client_and_session
        for row in session.execute(select(CommunicationLog)).scalars():
            if row.status == CommunicationStatus.BLOCKED:
                assert row.body == ""
                assert row.blocked_reason
            else:
                assert row.is_simulated is True

    def test_recording_failure_never_fails_the_run(self, client_and_session, monkeypatch):
        """The recovery action has already happened and is in the ledger.
        Losing the conversation record must not throw that work away."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.routers import batch as batch_module

        def explode(*args, **kwargs):
            raise RuntimeError("communication log unavailable")

        monkeypatch.setattr(batch_module, "_record_agent_contact", explode)

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
        logging.disable(logging.CRITICAL)
        try:
            result = batch_module.run_batch(session, BatchRequest(count=15), load_ml=False)
        finally:
            logging.disable(logging.NOTSET)
            session.close()
            engine.dispose()

        assert result.processed > 0


class TestVoiceIsGenuinelySelected:
    """Voice must be reachable through real reasoning, not hardcoded rows.

    It previously never appeared: the only action mapped to voice was rare, and
    the batch overrode the recommender's own reasoning with the action map —
    discarding the escalation and amount signals entirely.
    """

    def test_a_run_produces_voice_conversations(self, client_and_session):
        _, session = client_and_session
        channels = {
            row.channel for row in session.execute(select(CommunicationLog)).scalars()
        }
        assert Channel.VOICE_SCRIPT in channels, "voice is unreachable in a normal run"

    def test_voice_is_chosen_for_the_larger_amounts(self, client_and_session):
        """A call costs more, so it should track the money — not appear at random."""
        from app.models import RiskEvent

        _, session = client_and_session
        voice, other = [], []
        for row in session.execute(select(CommunicationLog)).scalars():
            event = session.get(RiskEvent, row.event_id)
            (voice if row.channel == Channel.VOICE_SCRIPT else other).append(event.amount)
        if voice and other:
            assert max(voice) > min(other)

    def test_every_voice_conversation_explains_itself(self, client_and_session):
        _, session = client_and_session
        for row in session.execute(select(CommunicationLog)).scalars():
            if row.channel == Channel.VOICE_SCRIPT:
                assert row.channel_reason
                assert "_" not in row.channel_reason  # merchant language

    def test_a_voice_conversation_carries_a_script(self, client_and_session):
        """The script IS the call. Without it there is nothing to play or read."""
        _, session = client_and_session
        for row in session.execute(select(CommunicationLog)).scalars():
            if row.channel == Channel.VOICE_SCRIPT and row.status != CommunicationStatus.BLOCKED:
                assert row.body

    def test_escalated_cases_get_a_call_not_another_message(self, client_and_session):
        from app.models import RiskEvent, StoppingRuleState
        from app.routers.communications import recommend_channel

        _, session = client_and_session
        event = next(iter(session.execute(select(RiskEvent)).scalars()))
        state = session.get(StoppingRuleState, event.id)
        if state is None:
            pytest.skip("no stopping-rule state for this case")
        original = state.escalation_level
        try:
            state.escalation_level = 2
            session.flush()
            channel, reason = recommend_channel(session, event, None)
            assert channel == Channel.VOICE_SCRIPT
            assert "escalated" in reason.lower()
        finally:
            state.escalation_level = original
            session.flush()

    def test_the_voice_filter_returns_them(self, client_and_session):
        client, _ = client_and_session
        body = client.get("/communications", params={"channel": "voice_script"}).json()
        assert body["total"] > 0
        assert all(item["channel"] == "voice_script" for item in body["items"])
