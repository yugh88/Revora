"""RazorpayTestGateway — the same interface, against Razorpay's free sandbox.
BUILD_SPEC Section 5.

    PaymentGateway (interface): initiate_retry(), check_status(), cancel()
      LocalSimulationGateway   -> self-built, deterministic, zero external deps
      RazorpayTestGateway      -> this file, razorpay-python, TEST mode only

What this file is, and what it deliberately is not
---------------------------------------------------
It is a TRANSLATION LAYER and nothing else: Revora's ``RetryRequest`` in,
Razorpay's SDK call out, Razorpay's response mapped back into
``GatewayResponse`` / ``GatewayStatusResult``.

There is no decisioning here. No diagnosis, no scoring, no policy, no attempt
counting, no stopping rules, no state transitions. Those all happen upstream in
app/engine/ before this module is ever called, and they happen identically
whichever gateway is selected. Razorpay reports what happened to a payment; it
never gets to decide what Revora does about it. The one guard that does live
here — refusing to retry a hard decline — duplicates a decision the policy
engine already made, as defence in depth, and can only ever refuse, never
permit.

Test mode is enforced, not assumed
-----------------------------------
:class:`RazorpayTestGateway` refuses to construct with a key that is not
``rzp_test_``-prefixed. Section 5 permits the free test sandbox only, and a live
key reaching this code would move real money. A configuration mistake should
fail loudly at construction rather than quietly at the first charge.

Synthetic ids will not resolve at Razorpay
-------------------------------------------
The synthetic generator produces references like ``pay_A1B2C3D4E5``, which do
not exist in anyone's Razorpay account. Against this gateway they resolve to
``NOT_FOUND``, which is correct and safe: ``NOT_FOUND`` is not in
``RESOLVED_UPSTREAM``, so the Section 9 pre-execution re-check does not treat the
event as already settled. This gateway is for demonstrating real integratability
on real Razorpay objects; the built-in simulator remains the mode that runs a
full synthetic batch end to end.

How failures are classified
----------------------------
Two channels, and the distinction matters:

* ``PaymentAttemptStatus.TIMEOUT`` — a recorded attempt whose outcome is
  unknown because the network or the API did not answer in time. It becomes a
  real PaymentAttempt row and is auditable.
* :class:`~app.gateways.base.GatewayError` — the gateway could not be used at
  all, or returned something unusable. This propagates and is caught by
  ``/batch``'s per-record try/except, isolating that record while the batch
  continues (Section 9).

An unrecognised status from Razorpay raises rather than being guessed at. That
is deliberate: mapping an unknown state to PENDING would let the engine act on a
payment that might already have succeeded, and mapping it to PAID would invent a
recovery. Raising makes the record an isolated, visible failure and takes no
action at all.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.enums import EventType, GatewayUsed, PaymentAttemptStatus
from app.gateways.base import (
    GatewayError,
    GatewayResponse,
    GatewayStatusResult,
    PaymentGateway,
    RetryRequest,
    UpstreamStatus,
    is_hard_decline,
)

logger = logging.getLogger("revora.gateway.razorpay")

# The SDK is imported defensively so that merely importing this module — which
# app.routers.batch does at startup — cannot break an installation that has not
# installed razorpay. Absence surfaces as a clear error at construction time.
try:  # pragma: no cover - import guard
    import razorpay
    from razorpay.errors import BadRequestError, GatewayError as RazorpaySDKGatewayError
    from razorpay.errors import ServerError

    RAZORPAY_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    razorpay = None  # type: ignore[assignment]
    BadRequestError = RazorpaySDKGatewayError = ServerError = ()  # type: ignore[misc]
    RAZORPAY_AVAILABLE = False

#: Section 5 permits the FREE test sandbox only. Live keys are refused.
TEST_KEY_PREFIX = "rzp_test_"

#: Network-level exception names treated as a timeout rather than a hard error.
#: Matched by name so that requests/urllib3 need not be imported here.
TIMEOUT_EXCEPTION_NAMES = frozenset(
    {"Timeout", "ConnectTimeout", "ReadTimeout", "ConnectionError", "socket.timeout"}
)

# --------------------------------------------------------------------------- #
# Status maps — documented Razorpay statuses only
# --------------------------------------------------------------------------- #
#
# Every entry below is a status Razorpay documents for that resource. Nothing is
# inferred from undocumented production behaviour, and anything absent from
# these maps raises instead of being guessed.

PAYMENT_STATUS_MAP: dict[str, UpstreamStatus] = {
    "created": UpstreamStatus.PENDING,
    "authorized": UpstreamStatus.PENDING,  # authorised but not captured: still owed
    "captured": UpstreamStatus.PAID,
    "refunded": UpstreamStatus.CANCELLED,  # money returned; nothing to recover
    "failed": UpstreamStatus.FAILED,
}

PAYMENT_LINK_STATUS_MAP: dict[str, UpstreamStatus] = {
    "created": UpstreamStatus.PENDING,
    "partially_paid": UpstreamStatus.PENDING,
    "paid": UpstreamStatus.PAID,
    "cancelled": UpstreamStatus.CANCELLED,
    "expired": UpstreamStatus.FAILED,
}

SUBSCRIPTION_STATUS_MAP: dict[str, UpstreamStatus] = {
    "created": UpstreamStatus.PENDING,
    "authenticated": UpstreamStatus.PENDING,
    "active": UpstreamStatus.ACTIVE,
    "pending": UpstreamStatus.PENDING,  # a charge failed; Razorpay will retry
    "halted": UpstreamStatus.HALTED,  # its retry failed too — Section 5's end state
    "cancelled": UpstreamStatus.CANCELLED,
    "completed": UpstreamStatus.PAID,
    "expired": UpstreamStatus.FAILED,
    "paused": UpstreamStatus.PENDING,
}

INVOICE_STATUS_MAP: dict[str, UpstreamStatus] = {
    "draft": UpstreamStatus.PENDING,
    "issued": UpstreamStatus.PENDING,
    "partially_paid": UpstreamStatus.PENDING,
    "paid": UpstreamStatus.PAID,
    "cancelled": UpstreamStatus.CANCELLED,
    "expired": UpstreamStatus.FAILED,
    "deleted": UpstreamStatus.CANCELLED,
}


class RazorpayConfigurationError(GatewayError):
    """Credentials are missing, malformed, or not test-mode."""


def _looks_like_timeout(exc: BaseException) -> bool:
    """True when an exception represents a network timeout or connection failure."""
    names = {type(exc).__name__}
    for base in type(exc).__mro__:
        names.add(base.__name__)
    return bool(names & TIMEOUT_EXCEPTION_NAMES)


class RazorpayTestGateway(PaymentGateway):
    """Executes against Razorpay's free test sandbox.

    Args:
        key_id: Razorpay TEST key id. Falls back to settings.
        key_secret: Razorpay TEST key secret. Falls back to settings.
        client: A pre-built SDK client. Tests inject a stub here so no test ever
            needs live credentials or a network call.
        settings: Overrides the cached application settings.

    Raises:
        RazorpayConfigurationError: credentials absent, or not a test key.
    """

    name = GatewayUsed.RAZORPAY_TEST

    def __init__(
        self,
        key_id: str | None = None,
        key_secret: str | None = None,
        *,
        client: Any = None,
        settings: Any = None,
    ) -> None:
        if client is not None:
            # Injected client: credentials are the caller's business.
            self.client = client
            self.key_id = key_id
            return

        if settings is None:
            from app.config import get_settings

            settings = get_settings()

        resolved_id = key_id or settings.razorpay_key_id
        resolved_secret = key_secret or settings.razorpay_key_secret

        if not resolved_id or not resolved_secret:
            raise RazorpayConfigurationError(
                "Razorpay test credentials are not configured. Set RAZORPAY_KEY_ID "
                "and RAZORPAY_KEY_SECRET in backend/.env, or use "
                "gateway=local_simulation, which needs no credentials.",
                gateway=self.name,
            )

        if not resolved_id.startswith(TEST_KEY_PREFIX):
            # Never let a live key through. Section 5 permits the free test
            # sandbox only, and a live key here would move real money.
            raise RazorpayConfigurationError(
                f"RAZORPAY_KEY_ID must be a TEST-mode key beginning with "
                f"{TEST_KEY_PREFIX!r}. Revora never operates against live "
                "Razorpay credentials.",
                gateway=self.name,
            )

        if not RAZORPAY_AVAILABLE:
            raise RazorpayConfigurationError(
                "The razorpay package is not installed. Install "
                "backend/requirements.txt, or use gateway=local_simulation.",
                gateway=self.name,
            )

        self.key_id = resolved_id
        self.client = razorpay.Client(auth=(resolved_id, resolved_secret))
        self.client.set_app_details({"title": "Revora", "version": "0.5.0"})

    # ------------------------------------------------------------------ #
    # Interface
    # ------------------------------------------------------------------ #

    def initiate_retry(
        self, request: RetryRequest, *, now: datetime | None = None
    ) -> GatewayResponse:
        """Ask Razorpay to collect the outstanding amount again.

        A failed card payment cannot be re-charged server-side without the
        customer acting, so the documented mechanism is a Payment Link: a real,
        free, test-mode object the customer can pay. Subscriptions are different
        and are handled by observation, not action — see below.
        """
        # --- Section 6: a hard decline is never retried, by any gateway ---
        if is_hard_decline(request.failure_reason):
            return GatewayResponse(
                status=PaymentAttemptStatus.FAILED,
                provider_ref=None,
                failure_reason=request.failure_reason,
                retry_refused=True,
                raw={
                    "gateway": self.name.value,
                    "reason": "hard_decline_not_retryable",
                    "error_code": request.failure_reason,
                    "executed": False,
                },
            )

        # --- subscriptions: react to Razorpay's own retry, never force one ---
        if request.event_type == EventType.SUBSCRIPTION_FAILED:
            return self._observe_subscription(request)

        return self._create_payment_link(request)

    def check_status(
        self, source_ref: str, event_type: EventType, *, now: datetime | None = None
    ) -> GatewayStatusResult:
        """Fetch current upstream state. Section 9's pre-execution re-check."""
        resource, fetch, status_map = self._resolve_resource(source_ref, event_type)

        try:
            payload = fetch(source_ref)
        except Exception as exc:  # noqa: BLE001 - classified below
            return self._status_from_exception(exc, source_ref, resource)

        status = self._map_status(payload, status_map, resource, source_ref)
        return GatewayStatusResult(
            status=status,
            provider_ref=self._provider_ref(payload) or source_ref,
            raw={
                "gateway": self.name.value,
                "resource": resource,
                "razorpay_status": payload.get("status"),
                "source_ref": source_ref,
            },
        )

    def cancel(
        self,
        source_ref: str,
        event_type: EventType,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> GatewayStatusResult:
        """Cancel the underlying object upstream."""
        resource = self._resource_for(event_type, source_ref)

        try:
            if resource == "subscription":
                payload = self.client.subscription.cancel(source_ref)
            elif resource == "invoice":
                payload = self.client.invoice.cancel(source_ref)
            elif resource == "payment_link":
                payload = self.client.payment_link.cancel(source_ref)
            else:
                # A captured or failed payment has no cancel operation in the
                # API. Saying so is better than pretending it was cancelled.
                raise GatewayError(
                    f"Razorpay payments cannot be cancelled via the API "
                    f"({source_ref}); refund is a separate, deliberate action.",
                    gateway=self.name,
                    source_ref=source_ref,
                )
        except GatewayError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._as_gateway_error(exc, source_ref, f"cancel {resource}") from exc

        status_map = self._status_map_for(resource)
        return GatewayStatusResult(
            status=self._map_status(payload, status_map, resource, source_ref),
            provider_ref=self._provider_ref(payload) or source_ref,
            raw={
                "gateway": self.name.value,
                "resource": resource,
                "razorpay_status": payload.get("status"),
                "reason": reason,
            },
        )

    # ------------------------------------------------------------------ #
    # Execution helpers
    # ------------------------------------------------------------------ #

    def _create_payment_link(self, request: RetryRequest) -> GatewayResponse:
        """Create a test-mode Payment Link for the outstanding amount.

        PENDING, not SUCCESS: creating the link does not collect the money. The
        outcome arrives later, either from a webhook or from ``check_status``.
        Reporting this as a success would be inventing a recovery.
        """
        payload = {
            "amount": self._to_paise(request.amount),
            "currency": "INR",
            "description": f"Revora recovery for {request.event_id}",
            "reference_id": request.idempotency_key,
            "notes": {
                "revora_event_id": request.event_id,
                "revora_attempt": str(request.attempt_number),
                "revora_source_ref": request.source_ref,
            },
        }

        try:
            created = self.client.payment_link.create(payload)
        except Exception as exc:  # noqa: BLE001 - classified below
            return self._response_from_exception(exc, request, "payment_link.create")

        if not isinstance(created, dict) or "id" not in created:
            raise GatewayError(
                "Razorpay returned an unusable payment_link response "
                f"(type {type(created).__name__}) for {request.event_id}.",
                gateway=self.name,
                source_ref=request.source_ref,
            )

        razorpay_status = created.get("status")
        mapped = PAYMENT_LINK_STATUS_MAP.get(str(razorpay_status))
        if mapped is None:
            raise GatewayError(
                f"Unrecognised Razorpay payment_link status {razorpay_status!r} "
                f"for {created.get('id')}. Refusing to guess whether this "
                "succeeded.",
                gateway=self.name,
                source_ref=request.source_ref,
            )

        status = (
            PaymentAttemptStatus.SUCCESS
            if mapped == UpstreamStatus.PAID
            else PaymentAttemptStatus.PENDING
        )
        return GatewayResponse(
            status=status,
            provider_ref=created["id"],
            failure_reason=None,
            raw={
                "gateway": self.name.value,
                "resource": "payment_link",
                "razorpay_status": razorpay_status,
                "short_url": created.get("short_url"),
                "reference_id": created.get("reference_id"),
                "executed": True,
            },
        )

    def _observe_subscription(self, request: RetryRequest) -> GatewayResponse:
        """Report the subscription's state without forcing a charge.

        Section 6: "React to Razorpay's own auto-retry/webhook state — do not
        force extra retries." Razorpay retries a failed subscription charge
        itself and moves the subscription to ``halted`` if that retry also
        fails. Issuing our own charge here would be exactly the extra retry the
        spec forbids, so this method only ever reads.
        """
        try:
            payload = self.client.subscription.fetch(request.source_ref)
        except Exception as exc:  # noqa: BLE001
            return self._response_from_exception(exc, request, "subscription.fetch")

        razorpay_status = str(payload.get("status"))
        mapped = SUBSCRIPTION_STATUS_MAP.get(razorpay_status)
        if mapped is None:
            raise GatewayError(
                f"Unrecognised Razorpay subscription status {razorpay_status!r} "
                f"for {request.source_ref}.",
                gateway=self.name,
                source_ref=request.source_ref,
            )

        if mapped == UpstreamStatus.ACTIVE:
            status = PaymentAttemptStatus.SUCCESS
            refused = True
        elif mapped in (UpstreamStatus.HALTED, UpstreamStatus.CANCELLED):
            status = PaymentAttemptStatus.FAILED
            refused = True
        else:
            status = PaymentAttemptStatus.PENDING
            refused = False

        return GatewayResponse(
            status=status,
            provider_ref=payload.get("id") or request.source_ref,
            failure_reason=(
                None if status == PaymentAttemptStatus.SUCCESS
                else f"SUBSCRIPTION_{razorpay_status.upper()}"
            ),
            retry_refused=refused,
            raw={
                "gateway": self.name.value,
                "resource": "subscription",
                "razorpay_status": razorpay_status,
                "reason": "razorpay_manages_subscription_retries",
                "forced_retry_suppressed": True,
                "charge_at": payload.get("charge_at"),
                "executed": False,
            },
        )

    # ------------------------------------------------------------------ #
    # Mapping helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _to_paise(amount: Decimal) -> int:
        """Razorpay takes integer minor units. Exact, never via float."""
        return int((Decimal(str(amount)) * 100).quantize(Decimal("1")))

    @staticmethod
    def _provider_ref(payload: Any) -> str | None:
        if isinstance(payload, dict):
            value = payload.get("id")
            return str(value) if value else None
        return None

    def _resource_for(self, event_type: EventType, source_ref: str) -> str:
        """Which Razorpay resource a reference belongs to.

        Razorpay ids are prefixed by type, so the prefix is authoritative when
        present and the event type is the fallback.
        """
        if source_ref.startswith("plink_"):
            return "payment_link"
        if source_ref.startswith("sub_"):
            return "subscription"
        if source_ref.startswith("inv_"):
            return "invoice"
        if source_ref.startswith("pay_"):
            return "payment"

        if event_type == EventType.SUBSCRIPTION_FAILED:
            return "subscription"
        if event_type == EventType.INVOICE_OVERDUE:
            return "invoice"
        if event_type == EventType.MANDATE_FAILED:
            return "subscription"
        return "payment"

    def _status_map_for(self, resource: str) -> dict[str, UpstreamStatus]:
        return {
            "payment": PAYMENT_STATUS_MAP,
            "payment_link": PAYMENT_LINK_STATUS_MAP,
            "subscription": SUBSCRIPTION_STATUS_MAP,
            "invoice": INVOICE_STATUS_MAP,
        }[resource]

    def _resolve_resource(self, source_ref: str, event_type: EventType):
        resource = self._resource_for(event_type, source_ref)
        fetcher = {
            "payment": lambda ref: self.client.payment.fetch(ref),
            "payment_link": lambda ref: self.client.payment_link.fetch(ref),
            "subscription": lambda ref: self.client.subscription.fetch(ref),
            "invoice": lambda ref: self.client.invoice.fetch(ref),
        }[resource]
        return resource, fetcher, self._status_map_for(resource)

    def _map_status(
        self,
        payload: Any,
        status_map: dict[str, UpstreamStatus],
        resource: str,
        source_ref: str,
    ) -> UpstreamStatus:
        """Translate a Razorpay status, refusing to guess at unknown ones."""
        if not isinstance(payload, dict):
            raise GatewayError(
                f"Razorpay returned an unusable {resource} response "
                f"(type {type(payload).__name__}) for {source_ref}.",
                gateway=self.name,
                source_ref=source_ref,
            )

        raw_status = payload.get("status")
        mapped = status_map.get(str(raw_status))
        if mapped is None:
            # Never silently convert an unknown result into success — or into
            # anything else. An unknown state means we do not know whether the
            # money arrived, so no action may be taken on this event.
            raise GatewayError(
                f"Unrecognised Razorpay {resource} status {raw_status!r} for "
                f"{source_ref}. Refusing to infer an outcome.",
                gateway=self.name,
                source_ref=source_ref,
            )
        return mapped

    # ------------------------------------------------------------------ #
    # Failure classification
    # ------------------------------------------------------------------ #

    def _status_from_exception(
        self, exc: BaseException, source_ref: str, resource: str
    ) -> GatewayStatusResult:
        """A failed status fetch.

        A 404 is a genuine answer — the object does not exist — and maps to
        NOT_FOUND, which is not in RESOLVED_UPSTREAM, so the engine will not
        mistake it for a settled debt. Everything else raises.
        """
        if self._is_not_found(exc):
            return GatewayStatusResult(
                status=UpstreamStatus.NOT_FOUND,
                provider_ref=source_ref,
                raw={
                    "gateway": self.name.value,
                    "resource": resource,
                    "reason": "not_found_at_razorpay",
                    "detail": str(exc)[:300],
                },
            )
        raise self._as_gateway_error(exc, source_ref, f"fetch {resource}") from exc

    def _response_from_exception(
        self, exc: BaseException, request: RetryRequest, operation: str
    ) -> GatewayResponse:
        """A failed execution call.

        Timeouts become a recorded TIMEOUT attempt: the request may or may not
        have reached Razorpay, so the honest record is "outcome unknown", never
        success and never a clean failure. Everything else raises and is
        isolated per-record by /batch.
        """
        if _looks_like_timeout(exc):
            logger.warning(
                "razorpay_timeout",
                extra={
                    "event_id": request.event_id,
                    "stage": "execution",
                    "action": operation,
                    "outcome": "timeout",
                    "gateway": self.name.value,
                },
            )
            return GatewayResponse(
                status=PaymentAttemptStatus.TIMEOUT,
                provider_ref=None,
                failure_reason="GATEWAY_ERROR_TIMEOUT",
                raw={
                    "gateway": self.name.value,
                    "operation": operation,
                    "reason": "timeout",
                    "detail": str(exc)[:300],
                    "executed": True,
                },
            )
        raise self._as_gateway_error(exc, request.source_ref, operation) from exc

    @staticmethod
    def _is_not_found(exc: BaseException) -> bool:
        text = str(exc).lower()
        return "does not exist" in text or "not found" in text or "no such" in text

    def _as_gateway_error(
        self, exc: BaseException, source_ref: str, operation: str
    ) -> GatewayError:
        """Wrap an SDK exception so /batch isolates the record cleanly.

        The message is truncated and carries no credentials: the SDK never puts
        the secret in an exception, and truncation keeps a large HTML error page
        from flooding the audit trail.
        """
        return GatewayError(
            f"Razorpay {operation} failed for {source_ref}: "
            f"{type(exc).__name__}: {str(exc)[:300]}",
            gateway=self.name,
            source_ref=source_ref,
        )
