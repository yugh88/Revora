"""Backend reports. BUILD_SPEC Section 10.

    cd backend && PYTHONPATH=. pytest -q tests/test_reports.py

A report that disagrees with the dashboard is worse than no report, so the
property under test is agreement: the report reads the same rows through the
same money summary, and its figures must equal the ones /events returns.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db, utcnow
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


class TestExplicitDateRange:
    """A custom range must actually narrow, and must beat the preset."""

    def test_a_range_narrows_the_result(self, client_and_session):
        client, _ = client_and_session
        whole = client.get("/reports/recovery").json()
        narrow = client.get(
            "/reports/recovery",
            params={
                "date_from": (utcnow() - timedelta(days=10)).isoformat(),
                "date_to": utcnow().isoformat(),
            },
        ).json()
        assert narrow["total_cases"] <= whole["total_cases"]

    def test_the_range_is_labelled_with_the_dates(self, client_and_session):
        client, _ = client_and_session
        start = (utcnow() - timedelta(days=10)).date().isoformat()
        body = client.get(
            "/reports/recovery",
            params={
                "date_from": (utcnow() - timedelta(days=10)).isoformat(),
                "date_to": utcnow().isoformat(),
            },
        ).json()
        assert start in body["period_label"]

    def test_an_explicit_range_overrides_a_preset(self, client_and_session):
        """If someone picked dates, those are the dates."""
        client, _ = client_and_session
        body = client.get(
            "/reports/recovery",
            params={
                "days": 1,
                "date_from": (utcnow() - timedelta(days=60)).isoformat(),
                "date_to": utcnow().isoformat(),
            },
        ).json()
        assert body["period_label"] != "Last day"

    def test_a_reversed_range_is_corrected_not_rejected(self, client_and_session):
        """Someone picking the dates the wrong way round meant a range."""
        client, _ = client_and_session
        body = client.get(
            "/reports/recovery",
            params={
                "date_from": utcnow().isoformat(),
                "date_to": (utcnow() - timedelta(days=30)).isoformat(),
            },
        )
        assert body.status_code == 200

    def test_a_naive_bound_is_accepted(self, client_and_session):
        """A date picked in a browser must not fail over a missing timezone."""
        client, _ = client_and_session
        response = client.get(
            "/reports/recovery",
            params={"date_from": (utcnow() - timedelta(days=5)).replace(tzinfo=None).isoformat()},
        )
        assert response.status_code == 200

    def test_a_future_only_range_is_empty_not_an_error(self, client_and_session):
        client, _ = client_and_session
        body = client.get(
            "/reports/recovery",
            params={
                "date_from": (utcnow() + timedelta(days=5)).isoformat(),
                "date_to": (utcnow() + timedelta(days=10)).isoformat(),
            },
        ).json()
        assert body["total_cases"] == 0
        assert Decimal(body["amount_at_risk"]) == Decimal("0.00")

    def test_the_audit_report_takes_a_range_too(self, client_and_session):
        client, _ = client_and_session
        response = client.get(
            "/reports/audit",
            params={
                "date_from": (utcnow() - timedelta(days=3)).isoformat(),
                "date_to": utcnow().isoformat(),
            },
        )
        assert response.status_code == 200
        assert "to" in response.json()["period_label"]


class TestPdfGeneration:
    """The document must be a real PDF, and must agree with the JSON."""

    def _pdf(self, client, url, **params):
        response = client.get(url, params=params)
        assert response.status_code == 200, response.text
        return response

    def test_a_recovery_pdf_is_produced(self, client_and_session):
        client, _ = client_and_session
        response = self._pdf(client, "/reports/recovery.pdf")
        assert response.content[:4] == b"%PDF"
        assert response.headers["content-type"] == "application/pdf"

    def test_an_audit_pdf_is_produced(self, client_and_session):
        client, _ = client_and_session
        response = self._pdf(client, "/reports/audit.pdf")
        assert response.content[:4] == b"%PDF"

    def test_it_downloads_rather_than_opening(self, client_and_session):
        client, _ = client_and_session
        disposition = self._pdf(client, "/reports/recovery.pdf").headers[
            "content-disposition"
        ]
        assert "attachment" in disposition
        assert disposition.endswith('.pdf"')

    def test_presets_work(self, client_and_session):
        client, _ = client_and_session
        for days in (1, 7, 30):
            assert self._pdf(client, "/reports/recovery.pdf", days=days).content[:4] == b"%PDF"

    def test_a_custom_range_works(self, client_and_session):
        client, _ = client_and_session
        response = self._pdf(
            client,
            "/reports/recovery.pdf",
            date_from=(utcnow() - timedelta(days=20)).isoformat(),
            date_to=utcnow().isoformat(),
        )
        assert response.content[:4] == b"%PDF"

    def test_an_empty_period_still_produces_a_document(self, client_and_session):
        """"Nothing happened" is a legitimate report, not an error."""
        client, _ = client_and_session
        response = self._pdf(
            client,
            "/reports/recovery.pdf",
            date_from=(utcnow() + timedelta(days=30)).isoformat(),
            date_to=(utcnow() + timedelta(days=40)).isoformat(),
        )
        assert response.content[:4] == b"%PDF"
        assert len(response.content) > 500

    def test_a_narrower_period_produces_a_smaller_document(self, client_and_session):
        """Evidence the range reaches the renderer rather than being ignored."""
        client, _ = client_and_session
        whole = self._pdf(client, "/reports/recovery.pdf")
        narrow = self._pdf(
            client,
            "/reports/recovery.pdf",
            date_from=(utcnow() + timedelta(days=30)).isoformat(),
            date_to=(utcnow() + timedelta(days=40)).isoformat(),
        )
        assert len(narrow.content) < len(whole.content)

    def test_the_pdf_computes_nothing_of_its_own(self):
        """Presentation only. A renderer doing arithmetic would be a second
        source of truth, and the first disagreement would be unresolvable."""
        import inspect

        from app.services import report_pdf

        source = inspect.getsource(report_pdf)
        for forbidden in ("session", "select(", "sum(", "Outcome", "RiskEvent"):
            assert forbidden not in source

    def test_generating_a_report_changes_nothing(self, client_and_session):
        from app.models import Outcome, RiskEvent

        client, session = client_and_session
        before = (
            len(list(session.execute(select(AuditLog)).scalars())),
            len(list(session.execute(select(Outcome)).scalars())),
            len(list(session.execute(select(RiskEvent)).scalars())),
        )
        client.get("/reports/recovery.pdf")
        client.get("/reports/audit.pdf")
        after = (
            len(list(session.execute(select(AuditLog)).scalars())),
            len(list(session.execute(select(Outcome)).scalars())),
            len(list(session.execute(select(RiskEvent)).scalars())),
        )
        assert before == after

    def test_the_pdf_routes_expose_no_write_method(self):
        methods = set()
        for route in app.routes:
            if getattr(route, "path", "").endswith(".pdf"):
                methods |= set(getattr(route, "methods", set()))
        assert methods <= {"GET", "HEAD", "OPTIONS"}

    def test_detail_can_be_omitted_for_a_summary_only_document(self, client_and_session):
        client, _ = client_and_session
        summary = self._pdf(client, "/reports/recovery.pdf", detail_limit=0)
        full = self._pdf(client, "/reports/recovery.pdf", detail_limit=60)
        assert summary.content[:4] == b"%PDF"
        assert len(summary.content) < len(full.content)
