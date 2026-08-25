"""GET /exceptions. BUILD_SPEC Sections 4a, 9 and 10.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

The endpoint's whole job is to answer "why didn't the engine act, or why does a
human need to look?" — so these tests check that each reason is DERIVED from
something an engine actually recorded, not asserted independently. A taxonomy
that could drift from the audit trail would make both untrustworthy, so several
tests below tie an exception row back to the Decision, Diagnosis or
MLDiagnosisPrediction that produced it.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.engine.diagnosis_engine import LOW_CONFIDENCE_THRESHOLD
from app.enums import EventStatus, EventType, PolicyResultStatus, RootCauseCode
from app.ml.diagnosis_classifier import MLPrediction
from app.models import Decision, Diagnosis, MLDiagnosisPrediction, RiskEvent
from app.routers.batch import run_batch
from app.routers.exceptions import (  # noqa: F401
    REASON_DO_NOT_CONTACT,
    REASON_ESCALATED,
    REASON_HARD_DECLINE,
    REASON_LOW_CONFIDENCE,
    REASON_ML_DISAGREEMENT,
    REASON_TEXT,
    collect_exceptions,
    count_exceptions,
)
from app.schemas.batch import BatchRequest


def memory_engine():
    """In-memory SQLite shared across threads.

    StaticPool is required, not cosmetic: the default pool for ``sqlite://``
    hands each thread its own connection, and each connection to :memory: is a
    SEPARATE empty database. FastAPI runs sync endpoints in a worker thread, so
    without this the request would query a database with no tables in it.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture(autouse=True)
def quiet_logs():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture()
def db_session():
    engine = memory_engine()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_client(session: Session):
    """HTTP client bound to a given session.

    The endpoints are exercised over HTTP rather than by calling the route
    functions directly: a direct call passes FastAPI's ``Query(...)`` sentinels
    as if they were values, so query-parameter handling — the whole point of the
    filter tests — would never actually run.
    """
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


class DisagreeingClassifier:
    model_version = "stub-dis-v1"

    def predict(self, features):
        return MLPrediction(RootCauseCode.NETWORK_TIMEOUT, 0.97, self.model_version)


@pytest.fixture(scope="module")
def _run():
    """One batch with a disagreeing classifier, so every reason code appears."""
    engine = memory_engine()
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    logging.disable(logging.CRITICAL)
    response = run_batch(
        session, BatchRequest(count=60), classifier=DisagreeingClassifier()
    )
    logging.disable(logging.NOTSET)
    yield session, response
    session.close()
    engine.dispose()


def reasons_for(items, event_id: str) -> set[str]:
    return {i.reason_code for i in items if i.event_id == event_id}


# --------------------------------------------------------------------------- #
# Reasons are derived, not invented
# --------------------------------------------------------------------------- #


class TestReasonsComeFromRecordedState:
    def test_exceptions_are_produced(self, _run):
        session, _ = _run
        assert collect_exceptions(session)

    def test_ml_disagreement_matches_the_stored_prediction(self, _run):
        """Every ml_rule_disagreement row must correspond to a persisted
        MLDiagnosisPrediction with agrees_with_rule_engine False."""
        session, _ = _run
        items = collect_exceptions(session)
        flagged = {
            i.event_id for i in items if i.reason_code == REASON_ML_DISAGREEMENT
        }
        stored = {
            row.event_id
            for row in session.execute(select(MLDiagnosisPrediction)).scalars()
            if not row.agrees_with_rule_engine
        }
        assert flagged == stored

    def test_low_confidence_matches_the_stored_diagnosis(self, _run):
        session, _ = _run
        items = collect_exceptions(session)
        flagged = {i.event_id for i in items if i.reason_code == REASON_LOW_CONFIDENCE}
        stored = {
            row.event_id
            for row in session.execute(select(Diagnosis)).scalars()
            if row.confidence < LOW_CONFIDENCE_THRESHOLD
        }
        assert flagged == stored

    def test_policy_blocks_match_the_stored_decisions(self, _run):
        """Every blocked decision must surface; a blocked event that never
        appeared in /exceptions would be an event that silently did nothing."""
        session, _ = _run
        items = collect_exceptions(session)
        policy_stage = {i.event_id for i in items if i.stage == "policy"}
        blocked = {
            row.event_id
            for row in session.execute(select(Decision)).scalars()
            if isinstance(row.policy_result, dict)
            and row.policy_result.get("status") == PolicyResultStatus.BLOCKED.value
        }
        assert policy_stage == blocked

    def test_escalated_events_appear(self, _run):
        session, _ = _run
        items = collect_exceptions(session)
        flagged = {i.event_id for i in items if i.reason_code == REASON_ESCALATED}
        escalated = {
            row.id
            for row in session.execute(select(RiskEvent)).scalars()
            if row.status == EventStatus.ESCALATED
        }
        assert flagged == escalated

    def test_hard_declines_are_represented(self, _run):
        """Section 11 injects ~10% hard declines; they must be visible as such,
        not lumped under a generic policy_blocked."""
        session, _ = _run
        items = collect_exceptions(session)
        assert any(i.reason_code == REASON_HARD_DECLINE for i in items)

    def test_do_not_contact_gets_its_own_reason(self, _run):
        """"The customer opted out" is a different answer from "policy blocked"."""
        session, _ = _run
        items = collect_exceptions(session)
        assert any(i.reason_code == REASON_DO_NOT_CONTACT for i in items)

    def test_one_event_can_carry_several_reasons(self, _run):
        """A low-confidence diagnosis that was also blocked is common; dropping
        either reason would misreport why review is needed."""
        session, _ = _run
        items = collect_exceptions(session)
        per_event: dict[str, int] = {}
        for item in items:
            per_event[item.event_id] = per_event.get(item.event_id, 0) + 1
        assert any(count > 1 for count in per_event.values())

    def test_count_matches_collect(self, _run):
        session, _ = _run
        assert count_exceptions(session) == len(collect_exceptions(session))

    def test_batch_exceptions_raised_matches(self, _run):
        session, response = _run
        assert response.exceptions_raised == count_exceptions(session)


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #


class TestExceptionStructure:
    def test_every_item_is_fully_populated(self, _run):
        """A judge should not have to open the database to understand a row."""
        session, _ = _run
        for item in collect_exceptions(session):
            assert item.event_id
            assert item.event_type in {t.value for t in EventType}
            assert Decimal(item.amount) >= 0
            assert item.currency == "INR"
            assert item.status
            assert item.reason
            assert item.reason_code
            assert item.stage
            assert item.correlation_id
            assert item.detected_at

    def test_reasons_are_human_readable(self, _run):
        session, _ = _run
        for item in collect_exceptions(session):
            assert item.reason == REASON_TEXT.get(item.reason_code, item.reason_code)
            assert item.reason != item.reason_code

    def test_the_ml_disagreement_wording_matches_the_spec(self):
        """Section 4a names this label exactly."""
        assert REASON_TEXT[REASON_ML_DISAGREEMENT] == "ML/rule disagreement — needs review"

    def test_a_blocked_row_shows_the_actual_comparison(self, _run):
        """Section 4's structured policy_result rendered as "actual vs
        threshold" is what makes the refusal legible."""
        session, _ = _run
        blocked = [i for i in collect_exceptions(session) if i.stage == "policy"]
        assert blocked
        for item in blocked:
            assert item.rule_triggered
            assert item.threshold_checked

    def test_ml_rows_show_both_verdicts(self, _run):
        """The point of a disagreement row is the comparison."""
        session, _ = _run
        ml_items = [
            i for i in collect_exceptions(session) if i.reason_code == REASON_ML_DISAGREEMENT
        ]
        assert ml_items
        for item in ml_items:
            assert item.rule_root_cause
            assert item.ml_root_cause
            assert item.ml_confidence is not None
            assert item.ml_agrees is False

    def test_items_are_newest_first(self, _run):
        session, _ = _run
        items = collect_exceptions(session)
        timestamps = [i.occurred_at for i in items]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_no_secrets_appear(self, _run):
        """Nothing in an exception row should ever carry a credential."""
        session, _ = _run
        blob = " ".join(
            str(item.model_dump()) for item in collect_exceptions(session)
        ).lower()
        for needle in ("rzp_test_", "rzp_live_", "secret", "password", "api_key"):
            assert needle not in blob


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #


class TestFilters:
    def test_unfiltered_returns_everything(self, _run):
        session, _ = _run
        body = make_client(session).get("/exceptions", params={"limit": 1000}).json()
        assert body["total"] == count_exceptions(session)

    def test_reason_filter_narrows_correctly(self, _run):
        session, _ = _run
        body = (
            make_client(session)
            .get("/exceptions", params={"reason_code": REASON_ML_DISAGREEMENT, "limit": 1000})
            .json()
        )
        assert body["total"] > 0
        assert all(i["reason_code"] == REASON_ML_DISAGREEMENT for i in body["items"])
        assert body["total"] < count_exceptions(session)

    def test_event_type_filter_narrows_correctly(self, _run):
        session, _ = _run
        body = (
            make_client(session)
            .get(
                "/exceptions",
                params={"event_type": EventType.INVOICE_OVERDUE.value, "limit": 1000},
            )
            .json()
        )
        assert all(
            i["event_type"] == EventType.INVOICE_OVERDUE.value for i in body["items"]
        )

    def test_status_filter_narrows_correctly(self, _run):
        session, _ = _run
        body = (
            make_client(session)
            .get("/exceptions", params={"status": EventStatus.ESCALATED.value, "limit": 1000})
            .json()
        )
        assert body["total"] > 0
        assert all(i["status"] == EventStatus.ESCALATED.value for i in body["items"])

    def test_an_unknown_reason_returns_nothing_rather_than_everything(self, _run):
        """A filter that silently fails open would show a reviewer the wrong queue."""
        session, _ = _run
        body = (
            make_client(session)
            .get("/exceptions", params={"reason_code": "not_a_real_reason"})
            .json()
        )
        assert body["total"] == 0
        assert body["items"] == []

    def test_the_breakdown_describes_the_filtered_set(self, _run):
        session, _ = _run
        body = (
            make_client(session)
            .get("/exceptions", params={"reason_code": REASON_ML_DISAGREEMENT, "limit": 5})
            .json()
        )
        assert set(body["reason_breakdown"]) == {REASON_ML_DISAGREEMENT}
        assert body["reason_breakdown"][REASON_ML_DISAGREEMENT] == body["total"]

    def test_the_breakdown_is_not_limited_to_the_page(self, _run):
        """Counting only the current page would understate the queue."""
        session, _ = _run
        body = make_client(session).get("/exceptions", params={"limit": 3}).json()
        assert body["returned"] == 3
        assert sum(body["reason_breakdown"].values()) == body["total"]
        assert body["total"] > body["returned"]

    def test_pagination_walks_without_gaps_or_repeats(self, _run):
        session, _ = _run
        client = make_client(session)
        total = count_exceptions(session)
        collected = []
        offset = 0
        while offset < total:
            page = client.get("/exceptions", params={"limit": 7, "offset": offset}).json()
            collected.extend((i["event_id"], i["reason_code"]) for i in page["items"])
            offset += 7
        assert len(collected) == total

    def test_the_endpoint_returns_200(self, _run):
        session, _ = _run
        assert make_client(session).get("/exceptions").status_code == 200


class TestEmptyState:
    def test_no_events_yields_an_empty_queue(self, db_session):
        body = make_client(db_session).get("/exceptions").json()
        assert body["total"] == 0
        assert body["items"] == []
        assert body["reason_breakdown"] == {}

    def test_a_clean_event_raises_no_exception(self, db_session):
        """Only genuinely problematic events should appear, or the queue is
        noise."""
        from app.engine.decision_engine import decide
        from app.models import CustomerProfile, Merchant

        db_session.add(Merchant(id="mer_c", name="Clean"))
        db_session.add(
            CustomerProfile(customer_id="cust_c", payment_success_rate=0.9)
        )
        db_session.flush()
        event = RiskEvent(
            id="evt_clean",
            type=EventType.PAYMENT_DEGRADED,
            merchant_id="mer_c",
            customer_id="cust_c",
            amount=Decimal("1500.00"),
            source_ref="pay_clean",
            raw_signal={"gateway_error_code": "BAD_REQUEST_CARD_EXPIRED"},
            correlation_id="corr_clean",
        )
        db_session.add(event)
        db_session.flush()
        decide(db_session, event, load_ml=False)
        db_session.commit()

        assert collect_exceptions(db_session) == []
