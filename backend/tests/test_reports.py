"""Backend reports. BUILD_SPEC Section 10.

    cd backend && PYTHONPATH=. pytest -q tests/test_reports.py

A report that disagrees with the dashboard is worse than no report, so the
property under test is agreement: the report reads the same rows through the
same money summary, and its figures must equal the ones /events returns.
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
from app.models import AuditLog
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


class TestRecoveryReport:
    def test_it_reports_real_figures(self, client_and_session):
        client, _ = client_and_session
        body = client.get("/reports/recovery").json()
        assert body["total_cases"] > 0
        assert Decimal(body["amount_at_risk"]) > 0

    def test_it_agrees_with_the_dashboard_exactly(self, client_and_session):
        """The whole point. Both read the same rows through the same summary."""
        client, _ = client_and_session
        report = client.get("/reports/recovery").json()
        live = client.get("/events", params={"limit": 1}).json()

        assert report["amount_at_risk"] == live["money"]["amount_at_risk"]
        assert report["amount_recovered"] == live["money"]["amount_recovered"]
        assert report["amount_pending"] == live["money"]["amount_pending"]
        assert report["amount_lost"] == live["money"]["amount_lost"]
        assert report["arr_retained"] == live["money"]["arr_retained"]
        assert report["recovery_rate"] == live["money"]["recovery_rate"]

    def test_the_ledger_identity_holds_in_the_report(self, client_and_session):
        client, _ = client_and_session
        body = client.get("/reports/recovery").json()
        total = (
            Decimal(body["amount_recovered"])
            + Decimal(body["amount_pending"])
            + Decimal(body["amount_lost"])
        )
        assert total == Decimal(body["amount_at_risk"])

    def test_categories_reconcile_to_the_total(self, client_and_session):
        client, _ = client_and_session
        body = client.get("/reports/recovery").json()
        assert sum(row["cases"] for row in body["by_category"]) == body["total_cases"]

    def test_a_category_filter_narrows(self, client_and_session):
        client, _ = client_and_session
        whole = client.get("/reports/recovery").json()
        part = client.get(
            "/reports/recovery", params={"category": "invoice_overdue"}
        ).json()
        assert part["total_cases"] <= whole["total_cases"]
        assert all(row["label"] == "invoice_overdue" for row in part["by_category"])

    def test_a_period_filter_narrows(self, client_and_session):
        client, _ = client_and_session
        whole = client.get("/reports/recovery").json()
        day = client.get("/reports/recovery", params={"days": 1}).json()
        assert day["total_cases"] <= whole["total_cases"]
        assert day["period_label"] == "Last day"

    def test_the_period_is_labelled_for_a_person(self, client_and_session):
        client, _ = client_and_session
        for days, label in ((7, "Last week"), (30, "Last month"), (365, "Last 12 months")):
            body = client.get("/reports/recovery", params={"days": days}).json()
            assert body["period_label"] == label
        assert client.get("/reports/recovery").json()["period_label"] == "All time"

    def test_money_is_an_exact_string(self, client_and_session):
        client, _ = client_and_session
        body = client.get("/reports/recovery").json()
        for field in ("amount_at_risk", "amount_recovered", "arr_retained"):
            assert isinstance(body[field], str)
            Decimal(body[field])


class TestAuditReport:
    def test_it_counts_real_entries(self, client_and_session):
        client, session = client_and_session
        stored = len(list(session.execute(select(AuditLog)).scalars()))
        body = client.get("/reports/audit").json()
        assert body["total_entries"] == stored

    def test_stages_reconcile_to_the_total(self, client_and_session):
        client, _ = client_and_session
        body = client.get("/reports/audit").json()
        assert sum(row["entries"] for row in body["by_stage"]) == body["total_entries"]

    def test_a_stage_filter_narrows(self, client_and_session):
        client, _ = client_and_session
        whole = client.get("/reports/audit").json()
        part = client.get("/reports/audit", params={"stage": "decision"}).json()
        assert part["total_entries"] <= whole["total_entries"]
        assert all(row["label"] == "decision" for row in part["by_stage"])

    def test_an_empty_period_is_zeros_not_an_error(self, client_and_session):
        """"Nothing happened last Tuesday" is a legitimate answer."""
        client, _ = client_and_session
        # A one-day window on a batch whose events are dated across 45 days.
        body = client.get("/reports/audit", params={"days": 1}).json()
        assert body["total_entries"] >= 0
        assert isinstance(body["by_stage"], list)


class TestReportsAreReadOnly:
    def test_generating_a_report_changes_nothing(self, client_and_session):
        from app.models import Outcome, RiskEvent

        client, session = client_and_session
        before = (
            len(list(session.execute(select(AuditLog)).scalars())),
            len(list(session.execute(select(Outcome)).scalars())),
            len(list(session.execute(select(RiskEvent)).scalars())),
        )
        for _ in range(3):
            client.get("/reports/recovery")
            client.get("/reports/audit")
        after = (
            len(list(session.execute(select(AuditLog)).scalars())),
            len(list(session.execute(select(Outcome)).scalars())),
            len(list(session.execute(select(RiskEvent)).scalars())),
        )
        assert before == after

    def test_the_routes_expose_no_write_method(self):
        methods = set()
        for route in app.routes:
            if getattr(route, "path", "").startswith("/reports"):
                methods |= set(getattr(route, "methods", set()))
        assert methods <= {"GET", "HEAD", "OPTIONS"}
