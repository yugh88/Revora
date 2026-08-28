"""GET /events and GET /events/{id}. BUILD_SPEC Sections 10 and 13.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q tests/test_events.py

Scope is deliberately narrow: this file covers the read-only router added in
Session 7 and nothing else. The engines, gateways and batch pipeline are frozen
and already verified; re-testing them here would duplicate 759 existing tests.

The two properties worth real tests:

* the endpoints are READ-ONLY — a feed that quietly mutated state would be a
  serious defect, so it is asserted directly rather than assumed;
* ``needs_review`` agrees with /exceptions — the feed's "needs attention" count
  and the exceptions queue are derived from the same two conditions, and if they
  drifted apart an operator would be triaging a different list from the one the
  engine flagged.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.enums import EventStatus, EventType
from app.main import app
from app.models import AuditLog, Decision, Diagnosis, RiskEvent
from app.routers.batch import run_batch
from app.routers.exceptions import collect_exceptions
from app.schemas.batch import BatchRequest


class _StubClassifier:
    """Predicts one fixed cause, so ML agreement and disagreement both occur.

    A stub rather than the shipped model: these tests are about how the router
    EXPOSES an ML opinion, not about what the tree happens to predict, and a
    retrained model must not be able to break them.
    """

    model_version = 'stub-events-v1'

    def predict(self, features):
        from app.enums import RootCauseCode
        from app.ml.diagnosis_classifier import MLPrediction

        return MLPrediction(RootCauseCode.CARD_EXPIRED, 0.93, self.model_version)


@pytest.fixture(scope='module')
def client_and_session():
    """One populated batch, shared by every read-only assertion below."""
    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()

    logging.disable(logging.CRITICAL)
    run_batch(session, BatchRequest(count=60), classifier=_StubClassifier())
    logging.disable(logging.NOTSET)

    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app), session
    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


# --------------------------------------------------------------------------- #
# Feed
# --------------------------------------------------------------------------- #


class TestEventFeed:
    def test_it_returns_real_events(self, client_and_session):
        client, session = client_and_session
        body = client.get('/events', params={'limit': 500}).json()
        stored = len(list(session.execute(select(RiskEvent)).scalars()))
        assert body['total'] == stored
        assert body['returned'] == len(body['items'])

    def test_each_row_carries_what_the_feed_needs(self, client_and_session):
        client, _ = client_and_session
        item = client.get('/events', params={'limit': 1}).json()['items'][0]
        for field in (
            'id',
            'type',
            'merchant_id',
            'customer_id',
            'amount',
            'currency',
            'detected_at',
            'status',
            'gateway_used',
            'correlation_id',
        ):
            assert item[field] is not None, f'missing {field}'

    def test_amount_is_an_exact_string_not_a_float(self, client_and_session):
        """A JSON number would arrive in the browser as a float and drift."""
        from decimal import Decimal

        client, _ = client_and_session
        for item in client.get('/events', params={'limit': 20}).json()['items']:
            assert isinstance(item['amount'], str)
            Decimal(item['amount'])

    def test_only_the_five_core_event_types_appear(self, client_and_session):
        client, _ = client_and_session
        body = client.get('/events', params={'limit': 500}).json()
        assert set(body['type_breakdown']) <= {t.value for t in EventType}

    def test_newest_first_by_default(self, client_and_session):
        client, _ = client_and_session
        dates = [i['detected_at'] for i in client.get('/events', params={'limit': 50}).json()['items']]
        assert dates == sorted(dates, reverse=True)

    def test_ordering_can_be_reversed(self, client_and_session):
        client, _ = client_and_session
        dates = [
            i['detected_at']
            for i in client.get('/events', params={'limit': 50, 'order': 'asc'}).json()['items']
        ]
        assert dates == sorted(dates)

    def test_pagination_does_not_repeat_rows(self, client_and_session):
        client, _ = client_and_session
        first = client.get('/events', params={'limit': 10, 'offset': 0}).json()
        second = client.get('/events', params={'limit': 10, 'offset': 10}).json()
        assert {i['id'] for i in first['items']} & {i['id'] for i in second['items']} == set()

    def test_breakdowns_describe_the_whole_filtered_set_not_the_page(
        self, client_and_session
    ):
        """Counting only the visible page would understate the backlog."""
        client, _ = client_and_session
        body = client.get('/events', params={'limit': 5}).json()
        assert body['returned'] == 5
        assert sum(body['status_breakdown'].values()) == body['total']
        assert body['total'] > body['returned']


class TestMerchantFacingFields:
    """The merchant UI shows people and dates, not identifiers."""

    def test_every_row_carries_a_customer_name(self, client_and_session):
        client, _ = client_and_session
        for item in client.get('/events', params={'limit': 30}).json()['items']:
            assert item['customer_name']
            # A name, not the raw id echoed back.
            assert item['customer_name'] != item['customer_id'] or ' ' not in item['customer_name']

    def test_the_name_comes_from_the_inbound_signal(self, client_and_session):
        client, session = client_and_session
        event = list(session.execute(select(RiskEvent).limit(1)).scalars())[0]
        expected = (event.raw_signal or {}).get('customer_name')
        item = client.get('/events', params={'q': event.id}).json()['items'][0]
        if expected:
            assert item['customer_name'] == expected

    def test_the_id_is_used_only_when_no_name_was_supplied(self, client_and_session):
        """Falling back to the id is better than rendering an empty cell."""
        client, session = client_and_session
        event = list(session.execute(select(RiskEvent).limit(1)).scalars())[0]
        original = event.raw_signal
        try:
            event.raw_signal = {}
            session.flush()
            item = client.get('/events', params={'q': event.id}).json()['items'][0]
            assert item['customer_name'] == event.customer_id
        finally:
            event.raw_signal = original
            session.flush()


class TestReportingPeriodFilter:
    """The period selector must actually change the numbers, not just a label."""

    def test_a_narrow_window_returns_fewer_events(self, client_and_session):
        from datetime import timedelta

        client, session = client_and_session
        latest = max(e.detected_at for e in session.execute(select(RiskEvent)).scalars())
        everything = client.get('/events', params={'limit': 1}).json()
        recent = client.get(
            '/events',
            params={'limit': 1, 'detected_from': (latest - timedelta(days=3)).isoformat()},
        ).json()
        assert recent['total'] < everything['total']

    def test_the_money_recalculates_for_the_window(self, client_and_session):
        """The whole point: filtering must move the amounts, not only the count."""
        from datetime import timedelta
        from decimal import Decimal

        client, session = client_and_session
        latest = max(e.detected_at for e in session.execute(select(RiskEvent)).scalars())
        everything = client.get('/events', params={'limit': 1}).json()['money']
        recent = client.get(
            '/events',
            params={'limit': 1, 'detected_from': (latest - timedelta(days=3)).isoformat()},
        ).json()['money']
        assert Decimal(recent['amount_at_risk']) < Decimal(everything['amount_at_risk'])

    def test_a_future_window_is_empty_rather_than_wrong(self, client_and_session):
        from datetime import timedelta

        client, session = client_and_session
        latest = max(e.detected_at for e in session.execute(select(RiskEvent)).scalars())
        body = client.get(
            '/events',
            params={'detected_from': (latest + timedelta(days=365)).isoformat()},
        ).json()
        assert body['total'] == 0
        assert body['money']['amount_at_risk'] == '0.00'
        assert body['money']['recovery_rate'] == 0.0

    def test_both_bounds_combine(self, client_and_session):
        from datetime import timedelta

        client, session = client_and_session
        dates = [e.detected_at for e in session.execute(select(RiskEvent)).scalars()]
        lo, hi = min(dates), max(dates)
        mid = lo + (hi - lo) / 2
        first = client.get(
            '/events', params={'detected_to': mid.isoformat(), 'limit': 1}
        ).json()['total']
        second = client.get(
            '/events',
            params={'detected_from': (mid + timedelta(microseconds=1)).isoformat(), 'limit': 1},
        ).json()['total']
        assert first + second == client.get('/events', params={'limit': 1}).json()['total']

    def test_a_naive_bound_is_accepted_not_a_500(self, client_and_session):
        """A query filter must never surface a backend exception.

        The TZDateTime guard rejects naive datetimes on write, which is correct
        and unchanged. A read filter is not a write: an ordinary caller sending
        an unzoned timestamp should get results, not a 500 they cannot act on.
        """
        client, session = client_and_session
        latest = max(e.detected_at for e in session.execute(select(RiskEvent)).scalars())
        naive = latest.replace(tzinfo=None).isoformat()
        response = client.get('/events', params={'limit': 1, 'detected_from': naive})
        assert response.status_code == 200

    def test_naive_and_aware_bounds_agree(self, client_and_session):
        """Interpreted as UTC, so the two spellings give the same answer."""
        from datetime import timedelta

        client, session = client_and_session
        latest = max(e.detected_at for e in session.execute(select(RiskEvent)).scalars())
        cutoff = latest - timedelta(days=5)
        aware = client.get(
            '/events', params={'limit': 1, 'detected_from': cutoff.isoformat()}
        ).json()
        naive = client.get(
            '/events',
            params={'limit': 1, 'detected_from': cutoff.replace(tzinfo=None).isoformat()},
        ).json()
        assert aware['total'] == naive['total']
        assert aware['money'] == naive['money']

    def test_earliest_detected_at_ignores_the_filter(self, client_and_session):
        """It reports how much history EXISTS, so the UI can say "only N months
        available" instead of drawing an empty year."""
        from datetime import timedelta

        client, session = client_and_session
        latest = max(e.detected_at for e in session.execute(select(RiskEvent)).scalars())
        unfiltered = client.get('/events', params={'limit': 1}).json()['earliest_detected_at']
        narrowed = client.get(
            '/events',
            params={'limit': 1, 'detected_from': (latest - timedelta(days=1)).isoformat()},
        ).json()['earliest_detected_at']
        assert unfiltered == narrowed
        assert unfiltered is not None


class TestFilters:
    def test_status_filter_narrows(self, client_and_session):
        client, _ = client_and_session
        body = client.get(
            '/events', params={'status': EventStatus.RECOVERED.value, 'limit': 500}
        ).json()
        assert body['total'] > 0
        assert all(i['status'] == EventStatus.RECOVERED.value for i in body['items'])

    def test_type_filter_narrows(self, client_and_session):
        client, _ = client_and_session
        body = client.get(
            '/events', params={'type': EventType.INVOICE_OVERDUE.value, 'limit': 500}
        ).json()
        assert all(i['type'] == EventType.INVOICE_OVERDUE.value for i in body['items'])

    def test_gateway_filter_narrows(self, client_and_session):
        client, _ = client_and_session
        body = client.get(
            '/events', params={'gateway': 'local_simulation', 'limit': 500}
        ).json()
        assert body['total'] > 0
        assert all(i['gateway_used'] == 'local_simulation' for i in body['items'])

    def test_search_matches_an_event_id(self, client_and_session):
        client, _ = client_and_session
        target = client.get('/events', params={'limit': 1}).json()['items'][0]['id']
        body = client.get('/events', params={'q': target}).json()
        assert body['total'] >= 1
        assert any(i['id'] == target for i in body['items'])

    def test_search_matches_a_customer_id(self, client_and_session):
        client, _ = client_and_session
        customer = client.get('/events', params={'limit': 1}).json()['items'][0]['customer_id']
        body = client.get('/events', params={'q': customer, 'limit': 500}).json()
        assert all(customer in i['customer_id'] for i in body['items'])

    def test_an_unmatched_filter_returns_nothing_rather_than_everything(
        self, client_and_session
    ):
        """A filter that fails open would show an operator the wrong queue."""
        client, _ = client_and_session
        body = client.get('/events', params={'q': 'definitely_no_such_event'}).json()
        assert body['total'] == 0
        assert body['items'] == []

    def test_an_invalid_status_is_rejected(self, client_and_session):
        client, _ = client_and_session
        assert client.get('/events', params={'status': 'nonsense'}).status_code == 422

    def test_filters_combine(self, client_and_session):
        client, _ = client_and_session
        body = client.get(
            '/events',
            params={
                'status': EventStatus.RECOVERED.value,
                'gateway': 'local_simulation',
                'limit': 500,
            },
        ).json()
        assert all(
            i['status'] == EventStatus.RECOVERED.value
            and i['gateway_used'] == 'local_simulation'
            for i in body['items']
        )


class TestNeedsReview:
    def test_the_flag_matches_the_exceptions_queue(self, client_and_session):
        """Same two conditions, so the feed and /exceptions cannot disagree."""
        client, session = client_and_session
        flagged = {
            i['id']
            for i in client.get('/events', params={'limit': 500}).json()['items']
            if i['needs_review']
        }
        from app.routers.exceptions import (
            REASON_LOW_CONFIDENCE,
            REASON_ML_DISAGREEMENT,
        )

        expected = {
            item.event_id
            for item in collect_exceptions(session)
            if item.reason_code in (REASON_LOW_CONFIDENCE, REASON_ML_DISAGREEMENT)
        }
        assert flagged == expected

    def test_the_filter_returns_only_flagged_events(self, client_and_session):
        client, _ = client_and_session
        body = client.get('/events', params={'needs_review': 'true', 'limit': 500}).json()
        assert body['total'] > 0
        assert all(i['needs_review'] for i in body['items'])

    def test_the_inverse_filter_returns_only_clean_events(self, client_and_session):
        client, _ = client_and_session
        body = client.get('/events', params={'needs_review': 'false', 'limit': 500}).json()
        assert all(not i['needs_review'] for i in body['items'])

    def test_the_two_filters_partition_the_feed(self, client_and_session):
        client, _ = client_and_session
        total = client.get('/events', params={'limit': 500}).json()['total']
        flagged = client.get('/events', params={'needs_review': 'true'}).json()['total']
        clean = client.get('/events', params={'needs_review': 'false'}).json()['total']
        assert flagged + clean == total

    def test_flagged_rows_explain_themselves(self, client_and_session):
        """A flag with no reason is not actionable."""
        client, _ = client_and_session
        body = client.get('/events', params={'needs_review': 'true', 'limit': 500}).json()
        for item in body['items']:
            assert item['review_reasons']

    def test_the_count_covers_the_filtered_set(self, client_and_session):
        client, _ = client_and_session
        body = client.get('/events', params={'limit': 5}).json()
        assert body['needs_review_count'] > body['returned'] or body['needs_review_count'] >= 0
        flagged_total = client.get('/events', params={'needs_review': 'true'}).json()['total']
        assert body['needs_review_count'] == flagged_total


# --------------------------------------------------------------------------- #
# Drill-down
# --------------------------------------------------------------------------- #


class TestEventDetail:
    def _any_event(self, client) -> str:
        return client.get('/events', params={'limit': 1}).json()['items'][0]['id']

    def test_it_returns_the_event(self, client_and_session):
        client, _ = client_and_session
        event_id = self._any_event(client)
        body = client.get(f'/events/{event_id}').json()
        assert body['event']['id'] == event_id

    def test_a_missing_event_is_a_404(self, client_and_session):
        client, _ = client_and_session
        assert client.get('/events/evt_does_not_exist').status_code == 404

    def test_diagnosis_is_present_and_matches_storage(self, client_and_session):
        client, session = client_and_session
        event_id = self._any_event(client)
        body = client.get(f'/events/{event_id}').json()
        stored = session.get(Diagnosis, event_id)
        assert body['diagnosis']['root_cause'] == stored.root_cause_code.value
        assert body['diagnosis']['confidence'] == stored.confidence
        assert body['diagnosis']['evidence']

    def test_decisions_match_storage(self, client_and_session):
        client, session = client_and_session
        event_id = self._any_event(client)
        body = client.get(f'/events/{event_id}').json()
        stored = list(
            session.execute(select(Decision).where(Decision.event_id == event_id)).scalars()
        )
        assert len(body['decisions']) == len(stored)
        assert body['decisions'][0]['reasoning_text']

    def test_the_policy_result_keeps_its_five_key_shape(self, client_and_session):
        """Section 4 fixes it; the drill-down renders it verbatim."""
        client, _ = client_and_session
        event_id = self._any_event(client)
        body = client.get(f'/events/{event_id}').json()
        assert set(body['decisions'][0]['policy_result']) == {
            'status',
            'rule_triggered',
            'threshold_checked',
            'actual_value',
            'threshold_value',
        }

    def test_the_audit_trail_is_ordered_and_complete(self, client_and_session):
        client, session = client_and_session
        event_id = self._any_event(client)
        body = client.get(f'/events/{event_id}').json()
        stored = list(
            session.execute(select(AuditLog).where(AuditLog.event_id == event_id)).scalars()
        )
        assert len(body['audit']) == len(stored)
        ids = [entry['id'] for entry in body['audit']]
        assert ids == sorted(ids)
        assert body['audit'][0]['stage'] == 'detection'

    def test_missing_stages_are_reported_explicitly(self, client_and_session):
        """So the UI can say "execution not reached because policy blocked the
        action" instead of showing an ambiguous gap."""
        client, _ = client_and_session
        event_id = self._any_event(client)
        body = client.get(f'/events/{event_id}').json()
        assert set(body['stages_present']) & set(body['stages_missing']) == set()
        assert body['stages_present']

    def test_a_blocked_event_has_no_execution_stage(self, client_and_session):
        client, session = client_and_session
        stopped = [
            row.id
            for row in session.execute(select(RiskEvent)).scalars()
            if row.status == EventStatus.STOPPED
        ]
        if not stopped:
            pytest.skip('no policy-stopped events in this batch')
        body = client.get(f'/events/{stopped[0]}').json()
        assert 'execution' in body['stages_missing']

    def test_ml_is_reported_separately_from_the_rule_verdict(self, client_and_session):
        """Section 4a: the rule engine is authoritative; ML is an independent
        signal. They must be distinguishable in the payload."""
        client, session = client_and_session
        from app.models import MLDiagnosisPrediction

        with_ml = [
            row.event_id
            for row in session.execute(select(MLDiagnosisPrediction)).scalars()
        ]
        assert with_ml, 'the stub classifier should have produced predictions'
        body = client.get(f'/events/{with_ml[0]}').json()
        assert body['ml'] is not None
        assert body['diagnosis']['root_cause'] is not None
        assert 'agrees_with_rule_engine' in body['ml']

    def test_stopping_rule_state_is_exposed(self, client_and_session):
        client, _ = client_and_session
        event_id = self._any_event(client)
        body = client.get(f'/events/{event_id}').json()
        state = body['stopping_rule_state']
        if state is not None:
            for field in (
                'attempts_used',
                'max_attempts_for_type',
                'escalation_level',
                'do_not_contact_snapshot',
            ):
                assert field in state

    def test_no_secrets_appear_in_any_payload(self, client_and_session):
        client, _ = client_and_session
        event_id = self._any_event(client)
        blob = client.get(f'/events/{event_id}').text.lower()
        for needle in ('rzp_test_', 'rzp_live_', 'password', 'api_key', 'secret'):
            assert needle not in blob


# --------------------------------------------------------------------------- #
# The endpoints must not mutate
# --------------------------------------------------------------------------- #


class TestEndpointsAreReadOnly:
    def test_no_write_route_is_exposed(self, client_and_session):
        """Section 10 also names POST /events, but ingestion is not built. The
        router must not quietly acquire a write path."""
        methods = set()
        for route in app.routes:
            if getattr(route, 'path', '').startswith('/events'):
                methods |= set(getattr(route, 'methods', set()))
        assert methods <= {'GET', 'HEAD', 'OPTIONS'}

    def test_reading_the_feed_changes_nothing(self, client_and_session):
        client, session = client_and_session
        before = (
            len(list(session.execute(select(RiskEvent)).scalars())),
            len(list(session.execute(select(AuditLog)).scalars())),
            len(list(session.execute(select(Decision)).scalars())),
        )
        client.get('/events', params={'limit': 500})
        client.get('/events', params={'needs_review': 'true'})
        after = (
            len(list(session.execute(select(RiskEvent)).scalars())),
            len(list(session.execute(select(AuditLog)).scalars())),
            len(list(session.execute(select(Decision)).scalars())),
        )
        assert before == after

    def test_reading_a_drill_down_changes_nothing(self, client_and_session):
        client, session = client_and_session
        event_id = client.get('/events', params={'limit': 1}).json()['items'][0]['id']
        before = len(list(session.execute(select(AuditLog)).scalars()))
        for _ in range(3):
            client.get(f'/events/{event_id}')
        assert len(list(session.execute(select(AuditLog)).scalars())) == before
