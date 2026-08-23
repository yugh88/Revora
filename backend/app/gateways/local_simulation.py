"""LocalSimulationGateway — fully self-built payment simulator. BUILD_SPEC Section 5.

Zero external dependencies, zero credentials, zero network. Standard library
only. This is the mode that must never fail during judging, and it is the
default (config.Settings.default_gateway).

Determinism
-----------
No ``random`` anywhere. Every outcome is a SHA-256 hash of
``(seed, source_ref, attempt_number, purpose)`` mapped into [0, 1) and compared
against a documented probability. The same batch replayed with the same seed
produces byte-identical outcomes, which is what makes a recorded demo
reproducible and a failing batch debuggable.

The Razorpay subscription lifecycle is modelled, not approximated
-----------------------------------------------------------------
Section 5 is explicit that this gateway must "independently replicate Razorpay's
real subscription behavior (auto-retry once the next day, then move to `halted`
if that fails too) — not just generic retry logic". Otherwise the
failed-subscription direction would only be demonstrable with the Razorpay
toggle on, breaking self-sufficiency.

So a subscription charge failure here behaves the way Razorpay actually behaves:

    charge fails
        -> gateway schedules its OWN retry for the next day (status PENDING)
        -> engine must WAIT and observe, not force a retry (Section 6:
           "React to Razorpay's own auto-retry/webhook state")
        -> on the retry date, check_status() resolves it exactly once:
             success -> ACTIVE   (subscription healthy again)
             failure -> HALTED   (Section 6 hard stop for subscription_failed)

``initiate_retry()`` on a subscription therefore deliberately does NOT charge.
It registers the lifecycle and reports the scheduled retry time. An engine that
tried to force extra retries would get the same PENDING answer every time and
never a second charge — the suppression is structural, not advisory.

The "external world"
--------------------
``seed_upstream_state()`` lets a caller declare that some objects are already
paid or cancelled before the engine ever looks — Section 11's ~10%
already-resolved-externally records. The synthetic generator returns exactly
this mapping. Crucially the marker lives in the gateway, not on the event, so
the engine can only discover it the honest way: by calling ``check_status()``
before executing, as Section 9 requires.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.enums import EventType, GatewayUsed, PaymentAttemptStatus
from app.gateways.base import (
    GatewayResponse,
    GatewayStatusResult,
    PaymentGateway,
    RetryRequest,
    UpstreamStatus,
    is_hard_decline,
)

#: Razorpay's own auto-retry cadence for a failed subscription charge: once, the
#: next day. Section 5.
SUBSCRIPTION_AUTO_RETRY_DELAY = timedelta(days=1)

#: Probability that a retry succeeds, keyed by the previous failure code.
#: These are hand-set and documented rather than learned — a network timeout is
#: very likely to clear on retry, an expired card almost never does without the
#: customer acting, and a hard decline is not retried at all.
RETRY_SUCCESS_PROBABILITY: dict[str, float] = {
    "GATEWAY_ERROR_TIMEOUT": 0.75,
    "GATEWAY_ERROR_ISSUER_DOWN": 0.70,
    "BAD_REQUEST_PAYMENT_TIMED_OUT": 0.65,
    "BAD_REQUEST_PAYMENT_INSUFFICIENT_FUNDS": 0.35,
    "BAD_REQUEST_MANDATE_INSUFFICIENT_BALANCE": 0.40,
    "BAD_REQUEST_MANDATE_NOT_AUTHENTICATED": 0.55,
    "BAD_REQUEST_3DS_AUTHENTICATION_FAILED": 0.45,
    "BAD_REQUEST_CARD_EXPIRED": 0.12,
    "BAD_REQUEST_MANDATE_EXPIRED": 0.10,
    "BAD_REQUEST_MANDATE_REVOKED": 0.05,
    "BAD_REQUEST_RISK_THRESHOLD_EXCEEDED": 0.15,
    "BAD_REQUEST_PAYMENT_FAILED": 0.30,
}
DEFAULT_RETRY_SUCCESS_PROBABILITY = 0.30

#: Share of executions that hang rather than cleanly fail — Section 9 names
#: "timeout" as a scenario that must be handled explicitly.
TIMEOUT_PROBABILITY = 0.06

#: Probability the gateway's own subscription auto-retry succeeds. Below half,
#: so a meaningful number of subscriptions genuinely reach `halted` and the
#: hard-stop path is exercised in every batch.
SUBSCRIPTION_AUTO_RETRY_SUCCESS_PROBABILITY = 0.40


def _unit_interval(seed: int, *parts: Any) -> float:
    """Deterministic float in [0, 1) from a seed and any number of parts."""
    payload = "|".join([str(seed), *(str(p) for p in parts)])
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


@dataclass
class _SubscriptionLifecycle:
    """Simulated Razorpay subscription state machine.

    Mirrors the provider's own lifecycle, which is why it is separate from the
    RiskEvent state machine: the gateway's ``halted`` is an upstream fact the
    engine observes, not a status the engine chooses.
    """

    status: UpstreamStatus
    auto_retry_at: datetime | None = None
    auto_retry_resolved: bool = False
    charge_failures: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)


class LocalSimulationGateway(PaymentGateway):
    """Deterministic in-process payment simulator.

    Args:
        seed: Reproducibility seed. 42 to match the synthetic generator
            (Section 11), so gateway outcomes and generated data replay together.
        clock: Callable returning the current timezone-aware UTC time. Injectable
            so the subscription lifecycle can be advanced a day in a test without
            waiting a day.
    """

    name = GatewayUsed.LOCAL_SIMULATION

    def __init__(self, seed: int = 42, clock=None) -> None:
        self.seed = seed
        self._clock = clock
        #: idempotency_key -> the response that key already produced (Section 9).
        self._executed: dict[str, GatewayResponse] = {}
        #: source_ref -> upstream state for non-subscription objects.
        self._upstream: dict[str, UpstreamStatus] = {}
        #: source_ref -> simulated Razorpay subscription lifecycle.
        self._subscriptions: dict[str, _SubscriptionLifecycle] = {}

    # ------------------------------------------------------------------ #
    # World setup
    # ------------------------------------------------------------------ #

    def _now(self, now: datetime | None) -> datetime:
        if now is not None:
            return now
        if self._clock is not None:
            return self._clock()
        from app.database import utcnow

        return utcnow()

    def seed_upstream_state(self, mapping: dict[str, str | UpstreamStatus]) -> None:
        """Declare pre-existing upstream state for objects the engine will see.

        Used for Section 11's already-resolved-externally records. Called with
        the ``upstream_world`` mapping the synthetic generator returns.
        """
        for source_ref, status in mapping.items():
            resolved = status if isinstance(status, UpstreamStatus) else UpstreamStatus(status)
            if resolved in (UpstreamStatus.ACTIVE, UpstreamStatus.HALTED):
                self._subscriptions[source_ref] = _SubscriptionLifecycle(status=resolved)
            else:
                self._upstream[source_ref] = resolved

    def reset(self) -> None:
        """Clear all simulated state. Keeps repeated batch runs independent."""
        self._executed.clear()
        self._upstream.clear()
        self._subscriptions.clear()

    # ------------------------------------------------------------------ #
    # PaymentGateway interface
    # ------------------------------------------------------------------ #

    def initiate_retry(
        self, request: RetryRequest, *, now: datetime | None = None
    ) -> GatewayResponse:
        """Attempt one recovery execution.

        Replaying an idempotency key returns the original result without
        executing again (Section 9). Subscriptions never charge here — see the
        module docstring.
        """
        moment = self._now(now)

        # --- Section 9: idempotency, before anything else ---
        cached = self._executed.get(request.idempotency_key)
        if cached is not None:
            return GatewayResponse(
                status=cached.status,
                provider_ref=cached.provider_ref,
                failure_reason=cached.failure_reason,
                retry_refused=cached.retry_refused,
                raw={**cached.raw, "idempotent_replay": True},
            )

        # --- Section 6: hard declines are never retried ---
        if is_hard_decline(request.failure_reason):
            response = GatewayResponse(
                status=PaymentAttemptStatus.FAILED,
                provider_ref=None,
                failure_reason=request.failure_reason,
                retry_refused=True,
                raw={
                    "simulated": True,
                    "reason": "hard_decline_not_retryable",
                    "error_code": request.failure_reason,
                    "executed": False,
                },
            )
            self._executed[request.idempotency_key] = response
            return response

        # --- subscriptions follow Razorpay's own lifecycle, not ours ---
        if request.event_type == EventType.SUBSCRIPTION_FAILED:
            response = self._handle_subscription_retry(request, moment)
            self._executed[request.idempotency_key] = response
            return response

        # --- everything else: a simulated charge ---
        response = self._simulate_charge(request, moment)
        self._executed[request.idempotency_key] = response
        return response

    def check_status(
        self, source_ref: str, event_type: EventType, *, now: datetime | None = None
    ) -> GatewayStatusResult:
        """Report current upstream state.

        For subscriptions this is also where Razorpay's own auto-retry lands:
        once the retry date has passed, the pending retry resolves exactly once,
        to ACTIVE or HALTED.
        """
        moment = self._now(now)

        if event_type == EventType.SUBSCRIPTION_FAILED:
            return self._subscription_status(source_ref, moment)

        status = self._upstream.get(source_ref, UpstreamStatus.PENDING)
        return GatewayStatusResult(
            status=status,
            provider_ref=source_ref,
            raw={"simulated": True, "source_ref": source_ref, "status": status.value},
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
        moment = self._now(now)

        if event_type == EventType.SUBSCRIPTION_FAILED:
            lifecycle = self._subscriptions.setdefault(
                source_ref, _SubscriptionLifecycle(status=UpstreamStatus.PENDING)
            )
            lifecycle.status = UpstreamStatus.CANCELLED
            lifecycle.auto_retry_at = None
            lifecycle.auto_retry_resolved = True
            lifecycle.history.append(
                {"at": moment.isoformat(), "event": "cancelled", "reason": reason}
            )
        else:
            self._upstream[source_ref] = UpstreamStatus.CANCELLED

        return GatewayStatusResult(
            status=UpstreamStatus.CANCELLED,
            provider_ref=source_ref,
            raw={"simulated": True, "reason": reason, "cancelled_at": moment.isoformat()},
        )

    # ------------------------------------------------------------------ #
    # Subscription lifecycle — Section 5
    # ------------------------------------------------------------------ #

    def _handle_subscription_retry(
        self, request: RetryRequest, moment: datetime
    ) -> GatewayResponse:
        """Register the failure and let Razorpay's own retry handle it.

        Deliberately performs no charge. Section 6's subscription_failed row
        says to react to the provider's auto-retry state rather than forcing
        extra retries, so forcing one here must be structurally impossible.
        """
        lifecycle = self._subscriptions.get(request.source_ref)

        if lifecycle is None:
            lifecycle = _SubscriptionLifecycle(
                status=UpstreamStatus.FAILED,
                auto_retry_at=moment + SUBSCRIPTION_AUTO_RETRY_DELAY,
                charge_failures=1,
            )
            lifecycle.history.append(
                {
                    "at": moment.isoformat(),
                    "event": "charge_failed",
                    "error_code": request.failure_reason,
                    "auto_retry_at": lifecycle.auto_retry_at.isoformat(),
                }
            )
            self._subscriptions[request.source_ref] = lifecycle

        # Already finished, one way or the other.
        if lifecycle.status in (
            UpstreamStatus.ACTIVE,
            UpstreamStatus.HALTED,
            UpstreamStatus.CANCELLED,
        ):
            return GatewayResponse(
                status=(
                    PaymentAttemptStatus.SUCCESS
                    if lifecycle.status == UpstreamStatus.ACTIVE
                    else PaymentAttemptStatus.FAILED
                ),
                provider_ref=request.source_ref,
                failure_reason=(
                    None
                    if lifecycle.status == UpstreamStatus.ACTIVE
                    else f"SUBSCRIPTION_{lifecycle.status.value.upper()}"
                ),
                retry_refused=True,
                raw={
                    "simulated": True,
                    "subscription_status": lifecycle.status.value,
                    "reason": "subscription_lifecycle_already_terminal",
                    "forced_retry_suppressed": True,
                    "executed": False,
                },
            )

        return GatewayResponse(
            status=PaymentAttemptStatus.PENDING,
            provider_ref=request.source_ref,
            failure_reason=request.failure_reason,
            retry_refused=False,
            raw={
                "simulated": True,
                "subscription_status": lifecycle.status.value,
                "reason": "razorpay_auto_retry_scheduled",
                "auto_retry_at": (
                    lifecycle.auto_retry_at.isoformat() if lifecycle.auto_retry_at else None
                ),
                # The engine must WAIT, not charge. Section 6.
                "forced_retry_suppressed": True,
                "executed": False,
            },
        )

    def _subscription_status(self, source_ref: str, moment: datetime) -> GatewayStatusResult:
        """Resolve the provider's auto-retry if its moment has arrived."""
        lifecycle = self._subscriptions.get(source_ref)
        if lifecycle is None:
            return GatewayStatusResult(
                status=UpstreamStatus.PENDING,
                provider_ref=source_ref,
                raw={"simulated": True, "subscription_status": "pending"},
            )

        # The auto-retry fires exactly once, on or after its scheduled instant.
        if (
            not lifecycle.auto_retry_resolved
            and lifecycle.auto_retry_at is not None
            and moment >= lifecycle.auto_retry_at
        ):
            roll = _unit_interval(self.seed, source_ref, "subscription_auto_retry")
            succeeded = roll < SUBSCRIPTION_AUTO_RETRY_SUCCESS_PROBABILITY
            lifecycle.auto_retry_resolved = True
            lifecycle.status = UpstreamStatus.ACTIVE if succeeded else UpstreamStatus.HALTED
            if not succeeded:
                lifecycle.charge_failures += 1
            lifecycle.history.append(
                {
                    "at": moment.isoformat(),
                    "event": "auto_retry_succeeded" if succeeded else "auto_retry_failed",
                    "resulting_status": lifecycle.status.value,
                }
            )

        return GatewayStatusResult(
            status=lifecycle.status,
            provider_ref=source_ref,
            next_retry_at=(None if lifecycle.auto_retry_resolved else lifecycle.auto_retry_at),
            raw={
                "simulated": True,
                "subscription_status": lifecycle.status.value,
                "charge_failures": lifecycle.charge_failures,
                "auto_retry_resolved": lifecycle.auto_retry_resolved,
                "history": list(lifecycle.history),
            },
        )

    # ------------------------------------------------------------------ #
    # Charge simulation
    # ------------------------------------------------------------------ #

    def _simulate_charge(self, request: RetryRequest, moment: datetime) -> GatewayResponse:
        """Deterministic charge outcome for non-subscription event types."""
        # Section 9: never act on something already settled upstream.
        current = self._upstream.get(request.source_ref, UpstreamStatus.PENDING)
        if current in (UpstreamStatus.PAID, UpstreamStatus.CANCELLED):
            return GatewayResponse(
                status=PaymentAttemptStatus.FAILED,
                provider_ref=request.source_ref,
                failure_reason="ALREADY_RESOLVED_UPSTREAM",
                retry_refused=True,
                raw={
                    "simulated": True,
                    "reason": "already_resolved_upstream",
                    "upstream_status": current.value,
                    "executed": False,
                },
            )

        timeout_roll = _unit_interval(
            self.seed, request.source_ref, request.attempt_number, "timeout"
        )
        if timeout_roll < TIMEOUT_PROBABILITY:
            return GatewayResponse(
                status=PaymentAttemptStatus.TIMEOUT,
                provider_ref=None,
                failure_reason="GATEWAY_ERROR_TIMEOUT",
                raw={
                    "simulated": True,
                    "reason": "gateway_timeout",
                    "attempt_number": request.attempt_number,
                    "executed": True,
                },
            )

        base = RETRY_SUCCESS_PROBABILITY.get(
            request.failure_reason or "", DEFAULT_RETRY_SUCCESS_PROBABILITY
        )
        # Each further attempt is less likely to work than the one before.
        adjusted = base * (0.75 ** max(0, request.attempt_number - 1))

        roll = _unit_interval(self.seed, request.source_ref, request.attempt_number, "charge")
        if roll < adjusted:
            self._upstream[request.source_ref] = UpstreamStatus.PAID
            provider_ref = f"pay_sim_{hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:14]}"
            return GatewayResponse(
                status=PaymentAttemptStatus.SUCCESS,
                provider_ref=provider_ref,
                failure_reason=None,
                raw={
                    "simulated": True,
                    "amount": str(request.amount),
                    "currency": "INR",
                    "method": request.method,
                    "attempt_number": request.attempt_number,
                    "success_probability": round(adjusted, 4),
                    "executed": True,
                },
            )

        self._upstream[request.source_ref] = UpstreamStatus.FAILED
        return GatewayResponse(
            status=PaymentAttemptStatus.FAILED,
            provider_ref=None,
            failure_reason=request.failure_reason or "BAD_REQUEST_PAYMENT_FAILED",
            raw={
                "simulated": True,
                "amount": str(request.amount),
                "method": request.method,
                "attempt_number": request.attempt_number,
                "success_probability": round(adjusted, 4),
                "executed": True,
            },
        )
