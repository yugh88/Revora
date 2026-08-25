"""GET /audit. BUILD_SPEC Sections 4 and 10.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

What the audit trail has to prove is that the pipeline is traceable:

    detection -> diagnosis -> decision -> policy -> execution -> verification
    -> recovery / escalation

so the strongest tests here walk a real recovered event and assert every stage
is present in order, and walk a policy-blocked event and assert the execution
stages are ABSENT. The second is as important as the first: an event stopped by
the gate should have nothing recorded after policy, and if execution entries
turned up anyway it would mean the engine acted after deciding not to.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.enums import AuditStage, EventStatus
from app.models import AuditLog, ImmutableAuditLogError, RiskEvent
from app.routers.batch import run_batch
from app.schemas.batch import BatchRequest


def memory_engine():
    """In-memory SQLite shared across threads (see test_exceptions for why)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    return engine


def make_client(session: Session):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


@pytest.fixture(autouse=True)
def quiet_logs():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture()
def db_session():
    engine = memory_engine()
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(scope="module")
def _run():
    engine = memory_engine()
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    logging.disable(logging.CRITICAL)
    response = run_batch(session, BatchRequest(count=60), load_ml=False)
    logging.disable(logging.NOTSET)
    yield session, response
    session.close()
    engine.dispose()


def events_with_status(session: Session, status: EventStatus) -> list[RiskEvent]:
    return [
        row
        for row in session.execute(select(RiskEvent)).scalars()
        if row.status == status
    ]


# --------------------------------------------------------------------------- #
# The trail exists and is complete
# --------------------------------------------------------------------------- #


class TestAuditRecordsThePipeline:
    def test_audit_entries_are_created(self, _run):
        session, response = _run
        assert response.audit_entries > 0
        assert len(list(session.execute(select(AuditLog)).scalars())) == response.audit_entries

    def test_every_processed_event_has_a_trail(self, _run):
        """An event with no audit entries would be an event nobody can explain."""
        session, _ = _run
        events = {e.id for e in session.execute(select(RiskEvent)).scalars()}
        audited = {
            row.event_id
            for row in session.execute(select(AuditLog)).scalars()
            if row.event_id
        }
        assert events == audited

    def test_every_event_begins_with_detection(self, _run):
        session, _ = _run
        client = make_client(session)
        for event in list(session.execute(select(RiskEvent)).scalars())[:15]:
            trail = client.get(f"/audit/trail/{event.id}").json()
            assert trail["items"][0]["stage"] == AuditStage.DETECTION.value

    def test_a_recovered_event_shows_the_full_pipeline(self, _run):
        """The headline traceability claim, on a real recovered event."""
        session, _ = _run
        recovered = events_with_status(session, EventStatus.RECOVERED)
        assert recovered, "no recovered events to trace"
        client = make_client(session)

        traced = False
        for event in recovered:
            trail = client.get(f"/audit/trail/{event.id}").json()
            present = trail["stages_present"]
            if AuditStage.EXECUTION.value in present:
                # An engine-driven recovery must show every stage.
                for stage in (
                    AuditStage.DETECTION,
                    AuditStage.DIAGNOSIS,
                    AuditStage.DECISION,
                    AuditStage.POLICY,
                    AuditStage.EXECUTION,
                    AuditStage.VERIFICATION,
                    AuditStage.RECOVERY,
                ):
                    assert stage.value in present, f"{event.id} missing {stage.value}"
                traced = True
                break
        assert traced, "no engine-recovered event found to trace end to end"

    def test_stages_appear_in_pipeline_order(self, _run):
        """Ordering is the point of a trail; a shuffled one cannot be read."""
        session, _ = _run
        order = {
            AuditStage.DETECTION.value: 0,
            AuditStage.DIAGNOSIS.value: 1,
            AuditStage.DECISION.value: 2,
            AuditStage.POLICY.value: 3,
            AuditStage.EXECUTION.value: 4,
            AuditStage.VERIFICATION.value: 5,
        }
        session_client = make_client(session)
        for event in list(session.execute(select(RiskEvent)).scalars())[:20]:
            trail = session_client.get(f"/audit/trail/{event.id}").json()
            ranks = [
                order[item["stage"]]
                for item in trail["items"]
                if item["stage"] in order
            ]
            assert ranks == sorted(ranks), f"{event.id} stages out of order"

    def test_a_blocked_event_has_no_execution_stages(self, _run):
        """If the gate refused, nothing may have executed. Execution entries
        here would mean the engine acted after deciding not to."""
        session, _ = _run
        stopped = events_with_status(session, EventStatus.STOPPED)
        assert stopped, "no policy-stopped events in this batch"
        client = make_client(session)
        for event in stopped:
            trail = client.get(f"/audit/trail/{event.id}").json()
            assert AuditStage.EXECUTION.value in trail["stages_missing"]
            assert AuditStage.VERIFICATION.value in trail["stages_missing"]

    def test_missing_stages_are_reported_explicitly(self, _run):
        """Saying what is absent is how the trail proves the engine declined to
        act rather than quietly failing."""
        session, _ = _run
        event = list(session.execute(select(RiskEvent)).scalars())[0]
        trail = make_client(session).get(f"/audit/trail/{event.id}").json()
        assert isinstance(trail["stages_missing"], list)
        assert set(trail["stages_present"]) & set(trail["stages_missing"]) == set()

    def test_an_escalated_event_records_the_escalation(self, _run):
        session, _ = _run
        escalated = events_with_status(session, EventStatus.ESCALATED)
        if not escalated:
            pytest.skip("no escalated events in this batch")
        client = make_client(session)
        trail = client.get(f"/audit/trail/{escalated[0].id}").json()
        assert AuditStage.ESCALATION.value in trail["stages_present"]

    def test_entries_carry_reasoning(self, _run):
        """An audit line with no justification explains nothing."""
        session, _ = _run
        rows = list(session.execute(select(AuditLog)).scalars())
        assert sum(1 for r in rows if r.reasoning) / len(rows) > 0.95

    def test_each_stage_uses_its_own_action_verb(self, _run):
        """Stage alone is not enough: two stages sharing one action verb would
        make the trail ambiguous about what actually happened at each step.
        Mutation testing found that relabelling the policy entry as
        "decision_made" passed every other test here.
        """
        session, _ = _run
        by_stage: dict[str, set[str]] = {}
        for row in session.execute(select(AuditLog)).scalars():
            by_stage.setdefault(row.stage.value, set()).add(row.action)

        assert "event_detected" in by_stage[AuditStage.DETECTION.value]
        assert "policy_evaluated" in by_stage[AuditStage.POLICY.value]
        assert "decision_made" in by_stage[AuditStage.DECISION.value]
        assert "outcome_verified" in by_stage[AuditStage.VERIFICATION.value]
        # The policy stage must not be labelled with the decision stage's verb.
        assert "decision_made" not in by_stage[AuditStage.POLICY.value]

    def test_the_policy_entry_records_the_structured_result(self, _run):
        """Section 4's policy_result is what /exceptions renders; the audit
        trail must carry the same structure."""
        session, _ = _run
        policy_rows = [
            r
            for r in session.execute(select(AuditLog)).scalars()
            if r.action == "policy_evaluated"
        ]
        assert policy_rows
        for row in policy_rows[:10]:
            assert isinstance(row.after_state, dict)
            assert row.after_state.get("status") in ("allowed", "blocked")

    def test_state_transitions_record_before_and_after(self, _run):
        session, _ = _run
        transitions = [
            r
            for r in session.execute(select(AuditLog)).scalars()
            if r.action == "state_transition"
        ]
        assert transitions
        for row in transitions:
            assert row.before_state
            assert row.after_state
            assert row.before_state != row.after_state

    def test_a_missing_event_returns_404(self, db_session):
        assert make_client(db_session).get("/audit/trail/evt_nope").status_code == 404


# --------------------------------------------------------------------------- #
# Correlation
# --------------------------------------------------------------------------- #


class TestCorrelation:
    def test_every_entry_has_a_correlation_id(self, _run):
        session, _ = _run
        for row in session.execute(select(AuditLog)).scalars():
            assert row.correlation_id

    def test_an_event_and_its_audit_share_one_correlation_id(self, _run):
        """This is the link that makes batch -> event -> audit traceable."""
        session, _ = _run
        events = {e.id: e.correlation_id for e in session.execute(select(RiskEvent)).scalars()}
        for row in session.execute(select(AuditLog)).scalars():
            if row.event_id:
                assert row.correlation_id == events[row.event_id]

    def test_filtering_by_correlation_id_returns_one_event(self, _run):
        session, _ = _run
        event = list(session.execute(select(RiskEvent)).scalars())[0]
        body = (
            make_client(session)
            .get("/audit", params={"correlation_id": event.correlation_id, "limit": 500})
            .json()
        )
        assert body["total"] > 0
        assert {i["event_id"] for i in body["items"]} == {event.id}


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #


class TestFilters:
    def test_unfiltered_returns_everything(self, _run):
        session, response = _run
        body = make_client(session).get("/audit", params={"limit": 2000}).json()
        assert body["total"] == response.audit_entries

    def test_event_id_filter(self, _run):
        session, _ = _run
        event = list(session.execute(select(RiskEvent)).scalars())[0]
        body = (
            make_client(session)
            .get("/audit", params={"event_id": event.id, "limit": 500})
            .json()
        )
        assert body["total"] > 0
        assert all(i["event_id"] == event.id for i in body["items"])

    def test_stage_filter(self, _run):
        session, _ = _run
        body = (
            make_client(session)
            .get("/audit", params={"stage": AuditStage.DECISION.value, "limit": 2000})
            .json()
        )
        assert body["total"] > 0
        assert all(i["stage"] == AuditStage.DECISION.value for i in body["items"])

    def test_actor_filter(self, _run):
        session, _ = _run
        body = make_client(session).get("/audit", params={"actor": "system", "limit": 2000}).json()
        assert body["total"] > 0
        assert all(i["actor"] == "system" for i in body["items"])

    def test_action_filter(self, _run):
        session, _ = _run
        body = (
            make_client(session)
            .get("/audit", params={"action": "event_detected", "limit": 2000})
            .json()
        )
        assert body["total"] > 0
        assert all(i["action"] == "event_detected" for i in body["items"])

    def test_time_range_filter(self, _run):
        session, _ = _run
        rows = list(session.execute(select(AuditLog).order_by(AuditLog.id)).scalars())
        midpoint = rows[len(rows) // 2].timestamp
        body = (
            make_client(session)
            .get("/audit", params={"since": midpoint.isoformat(), "limit": 2000})
            .json()
        )
        assert body["total"] <= len(rows)
        assert body["total"] > 0

    def test_filters_combine(self, _run):
        session, _ = _run
        event = list(session.execute(select(RiskEvent)).scalars())[0]
        body = (
            make_client(session)
            .get(
                "/audit",
                params={
                    "event_id": event.id,
                    "stage": AuditStage.DETECTION.value,
                    "limit": 500,
                },
            )
            .json()
        )
        assert body["total"] == 1

    def test_an_unmatched_filter_returns_nothing(self, _run):
        """A filter that fails open would show the wrong trail entirely."""
        session, _ = _run
        body = make_client(session).get("/audit", params={"event_id": "evt_nope"}).json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_an_invalid_stage_is_rejected(self, _run):
        session, _ = _run
        assert make_client(session).get("/audit", params={"stage": "nonsense"}).status_code == 422

    def test_ordering_can_be_reversed(self, _run):
        session, _ = _run
        client = make_client(session)
        ascending = client.get("/audit", params={"order": "asc", "limit": 10}).json()
        descending = client.get("/audit", params={"order": "desc", "limit": 10}).json()
        assert ascending["items"][0]["id"] < descending["items"][0]["id"]

    def test_pagination_does_not_repeat_entries(self, _run):
        session, _ = _run
        client = make_client(session)
        first = client.get("/audit", params={"limit": 25, "offset": 0}).json()
        second = client.get("/audit", params={"limit": 25, "offset": 25}).json()
        ids_first = {i["id"] for i in first["items"]}
        ids_second = {i["id"] for i in second["items"]}
        assert ids_first & ids_second == set()

    def test_stage_breakdown_sums_to_the_total(self, _run):
        session, _ = _run
        body = make_client(session).get("/audit", params={"limit": 5}).json()
        assert sum(body["stage_breakdown"].values()) == body["total"]
        assert body["returned"] == 5


# --------------------------------------------------------------------------- #
# Immutability and safety
# --------------------------------------------------------------------------- #


class TestImmutability:
    def test_the_api_exposes_no_write_route(self, _run):
        """Section 4: append-only. A mutating audit endpoint would break it."""
        from app.main import app

        methods = set()
        for route in app.routes:
            if getattr(route, "path", "").startswith("/audit"):
                methods |= set(getattr(route, "methods", set()))
        assert methods <= {"GET", "HEAD", "OPTIONS"}

    def test_an_entry_cannot_be_edited(self, db_session):
        from app.models import CustomerProfile, Merchant
        from app.enums import AuditActor

        db_session.add(Merchant(id="mer_a", name="Audit"))
        db_session.add(CustomerProfile(customer_id="cust_a"))
        db_session.flush()
        db_session.add(
            AuditLog(
                event_id=None,
                correlation_id="corr_a",
                actor=AuditActor.SYSTEM,
                stage=AuditStage.DETECTION,
                action="test",
                reasoning="original",
            )
        )
        db_session.commit()

        row = list(db_session.execute(select(AuditLog)).scalars())[0]
        row.reasoning = "rewritten history"
        with pytest.raises(ImmutableAuditLogError):
            db_session.flush()
        db_session.rollback()

    def test_reading_the_log_does_not_change_it(self, _run):
        session, response = _run
        client = make_client(session)
        before = response.audit_entries
        client.get("/audit", params={"limit": 500})
        client.get("/audit", params={"stage": "decision"})
        after = len(list(session.execute(select(AuditLog)).scalars()))
        assert after == before


class TestNoSecretsInAuditOutput:
    def test_no_credentials_appear_anywhere(self, _run):
        """Section 3 keeps secrets in .env; they must not leak into a log a
        judge will read on screen."""
        session, _ = _run
        body = make_client(session).get("/audit", params={"limit": 2000}).text.lower()
        for needle in ("rzp_test_", "rzp_live_", "razorpay_key", "password", "api_key"):
            assert needle not in body

    def test_no_credentials_in_a_trail(self, _run):
        session, _ = _run
        event = list(session.execute(select(RiskEvent)).scalars())[0]
        body = make_client(session).get(f"/audit/trail/{event.id}").text.lower()
        for needle in ("rzp_test_", "secret", "password"):
            assert needle not in body
