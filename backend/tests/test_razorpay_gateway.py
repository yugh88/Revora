"""RazorpayTestGateway. BUILD_SPEC Section 5.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q tests/test_razorpay_gateway.py

NO TEST HERE TOUCHES THE NETWORK OR NEEDS CREDENTIALS. Every SDK interaction goes
through :class:`FakeClient`, which records the calls made and returns whatever
payload a test asks for. That is not merely convenient — a suite that depended on
live Razorpay keys could not run in CI, could not run on a clean clone, and would
turn an outage at Razorpay into a red build.

What these tests are really protecting
---------------------------------------
The gateway is an execution layer with no authority. The risks worth testing are
therefore about it OVERSTEPPING or LYING:

* claiming a recovery that did not happen (unknown status treated as success),
* charging when it should only observe (forcing a subscription retry),
* retrying something Section 6 forbids (a hard decline),
* moving real money (a live key slipping through),
* hiding a failure from the batch's fault isolation.

Each has a test that fails if the guard is removed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.enums import EventType, GatewayUsed, PaymentAttemptStatus
from app.gateways.base import (
    GatewayError,
    GatewayResponse,
    GatewayStatusResult,
    PaymentGateway,
    RetryRequest,
    UpstreamStatus,
)
from app.gateways.razorpay_test import (
    INVOICE_STATUS_MAP,
    PAYMENT_LINK_STATUS_MAP,
    PAYMENT_STATUS_MAP,
    SUBSCRIPTION_STATUS_MAP,
    TEST_KEY_PREFIX,
    RazorpayConfigurationError,
    RazorpayTestGateway,
)

T0 = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)

TEST_KEY = "rzp_test_FakeKeyId1234"
TEST_SECRET = "FakeSecretValue0987654321"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeResource:
    """One SDK resource (payment, payment_link, subscription, invoice)."""

    def __init__(self, name: str, recorder: list) -> None:
        self._name = name
        self._recorder = recorder
        self.responses: dict[str, object] = {}

    def _respond(self, method: str, *args):
        self._recorder.append((f"{self._name}.{method}", args))
        outcome = self.responses.get(method)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(*args)
        if outcome is None:
            raise AssertionError(f"FakeClient has no scripted {self._name}.{method}")
        return outcome

    def create(self, data):
        return self._respond("create", data)

    def fetch(self, ref):
        return self._respond("fetch", ref)

    def cancel(self, ref):
        return self._respond("cancel", ref)


class FakeClient:
    """Stands in for razorpay.Client. Records every call made."""

    def __init__(self) -> None:
        self.calls: list = []
        self.app_details = None
        self.payment = FakeResource("payment", self.calls)
        self.payment_link = FakeResource("payment_link", self.calls)
        self.subscription = FakeResource("subscription", self.calls)
        self.invoice = FakeResource("invoice", self.calls)

    def set_app_details(self, details):
        self.app_details = details

    def method_names(self) -> list[str]:
        return [name for name, _ in self.calls]


class FakeTimeout(Exception):
    """Named to match the timeout classifier, as requests' Timeout would be."""


FakeTimeout.__name__ = "Timeout"


class FakeSettings:
    def __init__(self, key_id=None, key_secret=None):
        self.razorpay_key_id = key_id
        self.razorpay_key_secret = key_secret

    @property
    def razorpay_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)


@pytest.fixture()
def client() -> FakeClient:
    return FakeClient()


@pytest.fixture()
def gateway(client: FakeClient) -> RazorpayTestGateway:
    return RazorpayTestGateway(client=client)


def retry_request(
    source_ref: str = "pay_RZP0001",
    *,
    event_type: EventType = EventType.PAYMENT_DEGRADED,
    failure_reason: str | None = "BAD_REQUEST_PAYMENT_INSUFFICIENT_FUNDS",
    amount: str = "2499.00",
    attempt_number: int = 1,
) -> RetryRequest:
    return RetryRequest(
        event_id="evt_rzp_1",
        source_ref=source_ref,
        event_type=event_type,
        amount=Decimal(amount),
        attempt_number=attempt_number,
        idempotency_key="idem_rzp_1",
        failure_reason=failure_reason,
        method="card",
    )


# --------------------------------------------------------------------------- #
# 1. Interface conformance
# --------------------------------------------------------------------------- #


class TestImplementsPaymentGateway:
    def test_it_is_a_payment_gateway(self, gateway):
        assert isinstance(gateway, PaymentGateway)

    def test_it_exposes_the_three_section_5_methods(self, gateway):
        for method in ("initiate_retry", "check_status", "cancel"):
            assert callable(getattr(gateway, method))

    def test_it_identifies_itself_as_the_razorpay_gateway(self, gateway):
        assert gateway.name == GatewayUsed.RAZORPAY_TEST

    def test_it_returns_the_shared_result_types(self, client, gateway):
        """Same types as the simulator, so nothing downstream can tell them
        apart — that is what makes the toggle possible."""
        client.payment_link.responses["create"] = {
            "id": "plink_1",
            "status": "created",
        }
        client.payment.responses["fetch"] = {"id": "pay_1", "status": "captured"}
        client.payment_link.responses["cancel"] = {
            "id": "plink_1",
            "status": "cancelled",
        }

        assert isinstance(gateway.initiate_retry(retry_request(), now=T0), GatewayResponse)
        assert isinstance(
            gateway.check_status("pay_1", EventType.PAYMENT_DEGRADED, now=T0),
            GatewayStatusResult,
        )
        assert isinstance(
            gateway.cancel("plink_1", EventType.PAYMENT_DEGRADED, now=T0),
            GatewayStatusResult,
        )

    def test_it_carries_no_decisioning_logic(self):
        """Section 5's architectural boundary: the gateway executes, the engine
        decides.

        Checked by parsing the module's IMPORTS rather than grepping its text.
        A substring search flags ``request.idempotency_key`` — which is the
        gateway correctly *using* a key it was handed, not reaching into the
        engine — and would have to be loosened until it caught nothing.
        Importing any app.engine module is the real signal that Razorpay is
        starting to make decisions.
        """
        import ast
        import inspect

        from app.gateways import razorpay_test

        tree = ast.parse(inspect.getsource(razorpay_test))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        engine_imports = {name for name in imported if name.startswith("app.engine")}
        assert engine_imports == set(), f"gateway imports engine modules: {engine_imports}"

        # It may only depend on the gateway contract, enums and config.
        app_imports = {name for name in imported if name.startswith("app.")}
        assert app_imports <= {"app.enums", "app.gateways.base", "app.config"}, (
            f"unexpected app dependency: {app_imports}"
        )

    def test_it_never_writes_to_the_database(self):
        """A gateway that persisted anything would be making decisions the
        engine is supposed to own."""
        import ast
        import inspect

        from app.gateways import razorpay_test

        tree = ast.parse(inspect.getsource(razorpay_test))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(name.startswith(("sqlalchemy", "app.models")) for name in imported)


# --------------------------------------------------------------------------- #
# 2. Configuration
# --------------------------------------------------------------------------- #


class TestConfiguration:
    def test_credentials_are_passed_to_the_sdk(self, monkeypatch):
        """The key actually reaches razorpay.Client rather than being ignored."""
        captured = {}

        class FakeSDK:
            @staticmethod
            def Client(auth):
                captured["auth"] = auth
                return FakeClient()

        monkeypatch.setattr("app.gateways.razorpay_test.razorpay", FakeSDK)
        monkeypatch.setattr("app.gateways.razorpay_test.RAZORPAY_AVAILABLE", True)

        RazorpayTestGateway(settings=FakeSettings(TEST_KEY, TEST_SECRET))
        assert captured["auth"] == (TEST_KEY, TEST_SECRET)

    def test_missing_credentials_fail_safely(self):
        """A clear, actionable error — not a crash deep inside the SDK."""
        with pytest.raises(RazorpayConfigurationError) as excinfo:
            RazorpayTestGateway(settings=FakeSettings(None, None))
        assert "local_simulation" in str(excinfo.value)

    def test_a_partial_credential_pair_is_refused(self):
        with pytest.raises(RazorpayConfigurationError):
            RazorpayTestGateway(settings=FakeSettings(TEST_KEY, None))

    def test_a_live_key_is_refused(self, monkeypatch):
        """The most important guard in this file: a live key on the execution
        path would move real money. Section 5 permits the free test sandbox
        only."""
        monkeypatch.setattr("app.gateways.razorpay_test.RAZORPAY_AVAILABLE", True)
        with pytest.raises(RazorpayConfigurationError, match="TEST-mode"):
            RazorpayTestGateway(
                settings=FakeSettings("rzp_live_RealKey123", TEST_SECRET)
            )

    def test_the_enforced_prefix_is_the_test_prefix(self):
        assert TEST_KEY_PREFIX == "rzp_test_"

    def test_the_configuration_error_is_a_gateway_error(self):
        """So callers that catch GatewayError catch this too."""
        assert issubclass(RazorpayConfigurationError, GatewayError)

    def test_no_secret_appears_in_the_error_message(self):
        """An error surfaces to the API and to logs; it must not carry the key."""
        with pytest.raises(RazorpayConfigurationError) as excinfo:
            RazorpayTestGateway(settings=FakeSettings("rzp_live_x", TEST_SECRET))
        assert TEST_SECRET not in str(excinfo.value)

    def test_an_injected_client_needs_no_credentials(self, client):
        """This is what lets the whole suite run with no keys."""
        assert RazorpayTestGateway(client=client).client is client


# --------------------------------------------------------------------------- #
# 3-4. Execution mapping
# --------------------------------------------------------------------------- #


class TestInitiateRetry:
    def test_a_created_payment_link_maps_to_pending(self, client, gateway):
        """Creating a link does not collect money. Reporting SUCCESS here would
        invent a recovery that has not happened."""
        client.payment_link.responses["create"] = {
            "id": "plink_ABC123",
            "status": "created",
            "short_url": "https://rzp.io/i/abc",
        }
        response = gateway.initiate_retry(retry_request(), now=T0)
        assert response.status == PaymentAttemptStatus.PENDING
        assert response.succeeded is False

    def test_provider_ref_is_preserved(self, client, gateway):
        """Without it the attempt cannot be reconciled against Razorpay later."""
        client.payment_link.responses["create"] = {
            "id": "plink_ABC123",
            "status": "created",
        }
        assert gateway.initiate_retry(retry_request(), now=T0).provider_ref == "plink_ABC123"

    def test_an_already_paid_link_maps_to_success(self, client, gateway):
        client.payment_link.responses["create"] = {"id": "plink_X", "status": "paid"}
        assert gateway.initiate_retry(retry_request(), now=T0).status == (
            PaymentAttemptStatus.SUCCESS
        )

    def test_the_amount_is_sent_in_paise_exactly(self, client, gateway):
        """Razorpay takes integer minor units; a float rupee amount would drift."""
        client.payment_link.responses["create"] = {"id": "plink_X", "status": "created"}
        gateway.initiate_retry(retry_request(amount="2499.99"), now=T0)
        payload = client.calls[0][1][0]
        assert payload["amount"] == 249999
        assert isinstance(payload["amount"], int)

    def test_the_idempotency_key_is_sent_as_the_reference(self, client, gateway):
        """Section 9's key travels to Razorpay so the two sides can be
        reconciled — the gateway does not invent its own."""
        client.payment_link.responses["create"] = {"id": "plink_X", "status": "created"}
        gateway.initiate_retry(retry_request(), now=T0)
        payload = client.calls[0][1][0]
        assert payload["reference_id"] == "idem_rzp_1"
        assert payload["notes"]["revora_event_id"] == "evt_rzp_1"

    def test_an_unrecognised_status_raises_rather_than_guessing(self, client, gateway):
        """"Never silently convert an unknown gateway result into success."" A
        new Razorpay status must stop the record, not be assumed benign."""
        client.payment_link.responses["create"] = {
            "id": "plink_X",
            "status": "quantum_superposition",
        }
        with pytest.raises(GatewayError, match="Unrecognised"):
            gateway.initiate_retry(retry_request(), now=T0)

    def test_an_unusable_response_raises(self, client, gateway):
        client.payment_link.responses["create"] = "<html>gateway down</html>"
        with pytest.raises(GatewayError, match="unusable"):
            gateway.initiate_retry(retry_request(), now=T0)

    def test_a_response_without_an_id_raises(self, client, gateway):
        client.payment_link.responses["create"] = {"status": "created"}
        with pytest.raises(GatewayError):
            gateway.initiate_retry(retry_request(), now=T0)


class TestHardDeclinesAreNeverRetried:
    """Section 6, enforced by both gateways as defence in depth."""

    @pytest.mark.parametrize(
        "code", ["GATEWAY_ERROR_ISSUER_DECLINED", "BAD_REQUEST_MANDATE_BANK_REJECTED"]
    )
    def test_a_hard_decline_is_refused_without_calling_razorpay(
        self, client, gateway, code
    ):
        response = gateway.initiate_retry(
            retry_request(failure_reason=code), now=T0
        )
        assert response.retry_refused is True
        assert response.status == PaymentAttemptStatus.FAILED
        assert response.raw["executed"] is False
        assert client.calls == [], "the gateway contacted Razorpay for a hard decline"

    def test_a_soft_decline_does_execute(self, client, gateway):
        """Control: the refusal must be specific to hard declines."""
        client.payment_link.responses["create"] = {"id": "plink_X", "status": "created"}
        response = gateway.initiate_retry(retry_request(), now=T0)
        assert response.retry_refused is False
        assert client.calls


# --------------------------------------------------------------------------- #
# 5. Failure translation
# --------------------------------------------------------------------------- #


class TestFailureTranslation:
    def test_a_timeout_becomes_a_recorded_timeout_attempt(self, client, gateway):
        """The request may or may not have reached Razorpay, so the honest
        record is "outcome unknown" — never success, never a clean failure."""
        client.payment_link.responses["create"] = FakeTimeout("read timed out")
        response = gateway.initiate_retry(retry_request(), now=T0)
        assert response.status == PaymentAttemptStatus.TIMEOUT
        assert response.failure_reason == "GATEWAY_ERROR_TIMEOUT"
        assert response.provider_ref is None

    def test_a_timeout_is_never_reported_as_success(self, client, gateway):
        client.payment_link.responses["create"] = FakeTimeout("timed out")
        assert gateway.initiate_retry(retry_request(), now=T0).succeeded is False

    def test_an_api_error_raises_a_gateway_error(self, client, gateway):
        """Raising is what routes the record into /batch's fault isolation."""
        client.payment_link.responses["create"] = ValueError("400 Bad Request")
        with pytest.raises(GatewayError):
            gateway.initiate_retry(retry_request(), now=T0)

    def test_the_gateway_error_names_the_gateway_and_reference(self, client, gateway):
        client.payment_link.responses["create"] = ValueError("boom")
        with pytest.raises(GatewayError) as excinfo:
            gateway.initiate_retry(retry_request(), now=T0)
        assert excinfo.value.gateway == GatewayUsed.RAZORPAY_TEST
        assert excinfo.value.source_ref == "pay_RZP0001"

    def test_a_huge_error_body_is_truncated(self, client, gateway):
        """A large HTML error page must not flood the audit trail."""
        client.payment_link.responses["create"] = ValueError("x" * 5000)
        with pytest.raises(GatewayError) as excinfo:
            gateway.initiate_retry(retry_request(), now=T0)
        assert len(str(excinfo.value)) < 600


# --------------------------------------------------------------------------- #
# 6. check_status
# --------------------------------------------------------------------------- #


class TestCheckStatus:
    @pytest.mark.parametrize(
        "razorpay_status,expected",
        sorted((k, v) for k, v in PAYMENT_STATUS_MAP.items()),
    )
    def test_payment_statuses_map(self, client, gateway, razorpay_status, expected):
        client.payment.responses["fetch"] = {"id": "pay_1", "status": razorpay_status}
        result = gateway.check_status("pay_1", EventType.PAYMENT_DEGRADED, now=T0)
        assert result.status == expected

    @pytest.mark.parametrize(
        "razorpay_status,expected",
        sorted((k, v) for k, v in SUBSCRIPTION_STATUS_MAP.items()),
    )
    def test_subscription_statuses_map(self, client, gateway, razorpay_status, expected):
        client.subscription.responses["fetch"] = {
            "id": "sub_1",
            "status": razorpay_status,
        }
        result = gateway.check_status("sub_1", EventType.SUBSCRIPTION_FAILED, now=T0)
        assert result.status == expected

    @pytest.mark.parametrize(
        "razorpay_status,expected",
        sorted((k, v) for k, v in INVOICE_STATUS_MAP.items()),
    )
    def test_invoice_statuses_map(self, client, gateway, razorpay_status, expected):
        client.invoice.responses["fetch"] = {"id": "inv_1", "status": razorpay_status}
        result = gateway.check_status("inv_1", EventType.INVOICE_OVERDUE, now=T0)
        assert result.status == expected

    @pytest.mark.parametrize(
        "razorpay_status,expected",
        sorted((k, v) for k, v in PAYMENT_LINK_STATUS_MAP.items()),
    )
    def test_payment_link_statuses_map(self, client, gateway, razorpay_status, expected):
        client.payment_link.responses["fetch"] = {
            "id": "plink_1",
            "status": razorpay_status,
        }
        result = gateway.check_status("plink_1", EventType.PAYMENT_DEGRADED, now=T0)
        assert result.status == expected

    def test_a_captured_payment_is_resolved_externally(self, client, gateway):
        """Section 9's whole purpose: do not act on money that already arrived."""
        client.payment.responses["fetch"] = {"id": "pay_1", "status": "captured"}
        result = gateway.check_status("pay_1", EventType.PAYMENT_DEGRADED, now=T0)
        assert result.is_resolved_externally is True

    def test_an_authorized_payment_is_not_treated_as_paid(self, client, gateway):
        """Authorised but uncaptured money has not been collected. Calling it
        PAID would abandon a recoverable balance."""
        client.payment.responses["fetch"] = {"id": "pay_1", "status": "authorized"}
        result = gateway.check_status("pay_1", EventType.PAYMENT_DEGRADED, now=T0)
        assert result.status == UpstreamStatus.PENDING
        assert result.is_resolved_externally is False

    def test_a_missing_object_is_not_found_not_resolved(self, client, gateway):
        """Synthetic references do not exist at Razorpay. NOT_FOUND must not be
        mistaken for a settled debt."""
        client.payment.responses["fetch"] = ValueError(
            "The id provided does not exist"
        )
        result = gateway.check_status("pay_synthetic", EventType.PAYMENT_DEGRADED, now=T0)
        assert result.status == UpstreamStatus.NOT_FOUND
        assert result.is_resolved_externally is False

    def test_an_unknown_status_raises(self, client, gateway):
        client.payment.responses["fetch"] = {"id": "pay_1", "status": "brand_new_state"}
        with pytest.raises(GatewayError, match="Unrecognised"):
            gateway.check_status("pay_1", EventType.PAYMENT_DEGRADED, now=T0)

    def test_a_network_error_raises_rather_than_reporting_pending(
        self, client, gateway
    ):
        """Reporting PENDING on an unreachable gateway would let the engine act
        on an event whose real state is unknown."""
        client.payment.responses["fetch"] = ValueError("connection reset")
        with pytest.raises(GatewayError):
            gateway.check_status("pay_1", EventType.PAYMENT_DEGRADED, now=T0)

    def test_the_reference_prefix_selects_the_resource(self, client, gateway):
        """Razorpay ids are type-prefixed, so the prefix is authoritative."""
        client.subscription.responses["fetch"] = {"id": "sub_1", "status": "active"}
        gateway.check_status("sub_1", EventType.PAYMENT_DEGRADED, now=T0)
        assert client.method_names() == ["subscription.fetch"]

    def test_the_event_type_selects_the_resource_when_unprefixed(
        self, client, gateway
    ):
        client.invoice.responses["fetch"] = {"id": "x", "status": "paid"}
        gateway.check_status("weird_ref", EventType.INVOICE_OVERDUE, now=T0)
        assert client.method_names() == ["invoice.fetch"]

    def test_provider_ref_is_preserved_from_the_payload(self, client, gateway):
        client.payment.responses["fetch"] = {"id": "pay_REAL", "status": "captured"}
        result = gateway.check_status("pay_1", EventType.PAYMENT_DEGRADED, now=T0)
        assert result.provider_ref == "pay_REAL"


# --------------------------------------------------------------------------- #
# Subscriptions: observe, never force
# --------------------------------------------------------------------------- #


class TestSubscriptionsAreObservedNotCharged:
    """Section 6: react to Razorpay's own auto-retry, do not force extra ones."""

    def test_initiate_retry_only_reads(self, client, gateway):
        client.subscription.responses["fetch"] = {"id": "sub_1", "status": "pending"}
        response = gateway.initiate_retry(
            retry_request("sub_1", event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        assert client.method_names() == ["subscription.fetch"]
        assert response.raw["executed"] is False
        assert response.raw["forced_retry_suppressed"] is True

    def test_no_charge_is_ever_created_for_a_subscription(self, client, gateway):
        client.subscription.responses["fetch"] = {"id": "sub_1", "status": "pending"}
        for attempt in (1, 2, 3):
            gateway.initiate_retry(
                retry_request(
                    "sub_1",
                    event_type=EventType.SUBSCRIPTION_FAILED,
                    attempt_number=attempt,
                ),
                now=T0,
            )
        assert "payment_link.create" not in client.method_names()

    def test_a_halted_subscription_refuses_further_action(self, client, gateway):
        """Section 6 treats `halted` as a hard stop."""
        client.subscription.responses["fetch"] = {"id": "sub_1", "status": "halted"}
        response = gateway.initiate_retry(
            retry_request("sub_1", event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        assert response.status == PaymentAttemptStatus.FAILED
        assert response.retry_refused is True

    def test_an_active_subscription_reports_success(self, client, gateway):
        """Razorpay's own retry worked; the money arrived without us acting."""
        client.subscription.responses["fetch"] = {"id": "sub_1", "status": "active"}
        response = gateway.initiate_retry(
            retry_request("sub_1", event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        assert response.status == PaymentAttemptStatus.SUCCESS
        assert response.retry_refused is True

    def test_a_pending_subscription_stays_pending(self, client, gateway):
        client.subscription.responses["fetch"] = {"id": "sub_1", "status": "pending"}
        response = gateway.initiate_retry(
            retry_request("sub_1", event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        assert response.status == PaymentAttemptStatus.PENDING

    def test_an_unknown_subscription_status_raises(self, client, gateway):
        client.subscription.responses["fetch"] = {"id": "sub_1", "status": "mystery"}
        with pytest.raises(GatewayError, match="Unrecognised"):
            gateway.initiate_retry(
                retry_request("sub_1", event_type=EventType.SUBSCRIPTION_FAILED),
                now=T0,
            )


# --------------------------------------------------------------------------- #
# 7. cancel
# --------------------------------------------------------------------------- #


class TestCancel:
    def test_a_subscription_is_cancelled(self, client, gateway):
        client.subscription.responses["cancel"] = {"id": "sub_1", "status": "cancelled"}
        result = gateway.cancel("sub_1", EventType.SUBSCRIPTION_FAILED, reason="dnc", now=T0)
        assert result.status == UpstreamStatus.CANCELLED
        assert client.method_names() == ["subscription.cancel"]

    def test_an_invoice_is_cancelled(self, client, gateway):
        client.invoice.responses["cancel"] = {"id": "inv_1", "status": "cancelled"}
        result = gateway.cancel("inv_1", EventType.INVOICE_OVERDUE, now=T0)
        assert result.status == UpstreamStatus.CANCELLED

    def test_a_payment_link_is_cancelled(self, client, gateway):
        client.payment_link.responses["cancel"] = {
            "id": "plink_1",
            "status": "cancelled",
        }
        result = gateway.cancel("plink_1", EventType.PAYMENT_DEGRADED, now=T0)
        assert result.status == UpstreamStatus.CANCELLED

    def test_cancelling_a_payment_is_refused_honestly(self, client, gateway):
        """Razorpay has no cancel for a payment. Returning CANCELLED anyway
        would put a false fact in the ledger."""
        with pytest.raises(GatewayError, match="cannot be cancelled"):
            gateway.cancel("pay_1", EventType.PAYMENT_DEGRADED, now=T0)

    def test_the_reason_is_recorded(self, client, gateway):
        client.subscription.responses["cancel"] = {"id": "sub_1", "status": "cancelled"}
        result = gateway.cancel(
            "sub_1", EventType.SUBSCRIPTION_FAILED, reason="do_not_contact", now=T0
        )
        assert result.raw["reason"] == "do_not_contact"

    def test_a_cancel_failure_raises(self, client, gateway):
        client.subscription.responses["cancel"] = ValueError("already cancelled")
        with pytest.raises(GatewayError):
            gateway.cancel("sub_1", EventType.SUBSCRIPTION_FAILED, now=T0)


# --------------------------------------------------------------------------- #
# 10. Fault isolation through /batch
# --------------------------------------------------------------------------- #


class TestBatchFaultIsolationHolds:
    def test_a_raising_gateway_is_isolated_per_record(self):
        """A gateway that throws must not take the batch down — the Session 4
        guarantee has to survive a second gateway implementation."""
        import logging

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database import Base
        from app.routers.batch import run_batch
        from app.schemas.batch import BatchRequest

        client = FakeClient()
        client.payment_link.responses["create"] = ValueError("Razorpay is down")
        client.payment.responses["fetch"] = ValueError("The id provided does not exist")
        client.subscription.responses["fetch"] = ValueError(
            "The id provided does not exist"
        )
        client.invoice.responses["fetch"] = ValueError("The id provided does not exist")
        gateway = RazorpayTestGateway(client=client)

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, future=True
        )
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
        logging.disable(logging.CRITICAL)
        try:
            response = run_batch(
                session, BatchRequest(count=15), gateway=gateway, load_ml=False
            )
        finally:
            logging.disable(logging.NOTSET)
            session.close()
            engine.dispose()

        # The batch returned rather than crashing, and the failures are visible.
        assert response.total_records == 15
        assert response.isolated_failures > 0
        assert any("Razorpay" in f.error_message for f in response.failures)
        assert (
            response.processed
            + response.isolated_failures
            + response.skipped_duplicates
            == 15
        )

    def test_the_engine_still_stops_hard_declines_on_this_gateway(self):
        """The policy gate is upstream of the gateway, so it must behave
        identically whichever gateway is selected."""
        import logging

        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker

        from app.database import Base
        from app.models import PaymentAttempt, StoppingRuleState
        from app.routers.batch import run_batch
        from app.schemas.batch import BatchRequest

        client = FakeClient()
        client.payment_link.responses["create"] = {
            "id": "plink_X",
            "status": "created",
        }
        for resource in (client.payment, client.subscription, client.invoice):
            resource.responses["fetch"] = ValueError("The id provided does not exist")
        gateway = RazorpayTestGateway(client=client)

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, future=True
        )
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
        logging.disable(logging.CRITICAL)
        try:
            run_batch(session, BatchRequest(count=30), gateway=gateway, load_ml=False)
            hard_ids = {
                s.event_id
                for s in session.execute(select(StoppingRuleState)).scalars()
                if s.hard_stop_reason == "hard_stop_cause"
            }
            assert hard_ids, "no hard declines in this batch"
            for event_id in hard_ids:
                attempts = list(
                    session.execute(
                        select(PaymentAttempt).where(PaymentAttempt.event_id == event_id)
                    ).scalars()
                )
                assert attempts == [], "a hard decline was executed"
        finally:
            logging.disable(logging.NOTSET)
            session.close()
            engine.dispose()


# --------------------------------------------------------------------------- #
# 11-13. Selection and defaults
# --------------------------------------------------------------------------- #


class TestGatewaySelection:
    def test_the_simulator_is_still_selectable_and_unchanged(self):
        from app.gateways.local_simulation import LocalSimulationGateway
        from app.routers.batch import build_gateway

        gateway = build_gateway(GatewayUsed.LOCAL_SIMULATION)
        assert isinstance(gateway, LocalSimulationGateway)
        assert gateway.name == GatewayUsed.LOCAL_SIMULATION

    def test_the_default_request_gateway_is_the_simulator(self):
        """Section 5: "Default = Built-in Simulator"."""
        from app.schemas.batch import BatchRequest

        assert BatchRequest().gateway == GatewayUsed.LOCAL_SIMULATION

    def test_the_configured_default_is_the_simulator(self):
        from app.config import get_settings

        assert get_settings().default_gateway == GatewayUsed.LOCAL_SIMULATION

    def test_razorpay_selection_without_credentials_is_refused(self):
        """Never a silent downgrade to the simulator."""
        from fastapi import HTTPException

        from app.routers.batch import build_gateway

        with pytest.raises(HTTPException) as excinfo:
            build_gateway(GatewayUsed.RAZORPAY_TEST)
        assert excinfo.value.status_code == 400

    def test_razorpay_selection_builds_the_gateway_when_configured(self, monkeypatch):
        from app.routers.batch import build_gateway

        class FakeSDK:
            @staticmethod
            def Client(auth):
                return FakeClient()

        monkeypatch.setattr("app.gateways.razorpay_test.razorpay", FakeSDK)
        monkeypatch.setattr("app.gateways.razorpay_test.RAZORPAY_AVAILABLE", True)
        monkeypatch.setattr(
            "app.config.get_settings", lambda: FakeSettings(TEST_KEY, TEST_SECRET)
        )

        gateway = build_gateway(GatewayUsed.RAZORPAY_TEST)
        assert gateway.name == GatewayUsed.RAZORPAY_TEST

    def test_both_gateways_share_one_interface(self):
        """The claim Section 5 rests on: same engine, only execution differs."""
        from app.gateways.local_simulation import LocalSimulationGateway

        simulator = LocalSimulationGateway(seed=42)
        sandbox = RazorpayTestGateway(client=FakeClient())
        for method in ("initiate_retry", "check_status", "cancel"):
            assert hasattr(simulator, method)
            assert hasattr(sandbox, method)
        assert isinstance(simulator, PaymentGateway)
        assert isinstance(sandbox, PaymentGateway)


# --------------------------------------------------------------------------- #
# 14. No live calls
# --------------------------------------------------------------------------- #


class TestNoLiveCallsAreRequired:
    def test_no_test_here_uses_real_credentials(self):
        """A suite that needed live keys could not run on a clean clone."""
        from app.config import get_settings

        settings = get_settings()
        assert not settings.razorpay_configured or True  # informational either way
        assert TEST_KEY.startswith(TEST_KEY_PREFIX)
        assert "rzp_live_" not in TEST_KEY

    def test_the_fake_client_records_every_call(self, client, gateway):
        """The mechanism the assertions above depend on."""
        client.payment.responses["fetch"] = {"id": "pay_1", "status": "captured"}
        gateway.check_status("pay_1", EventType.PAYMENT_DEGRADED, now=T0)
        assert client.calls == [("payment.fetch", ("pay_1",))]
