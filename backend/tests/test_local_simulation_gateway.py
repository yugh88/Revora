"""Tests for gateways/local_simulation.py. BUILD_SPEC Sections 5, 6 and 9.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

The centre of gravity here is the Razorpay subscription lifecycle. Section 5
requires this gateway to "independently replicate Razorpay's real subscription
behavior (auto-retry once the next day, then move to `halted` if that fails
too) — not just generic retry logic", so these tests assert the specific
sequence rather than merely that a retry happened.

The gateway's ``clock`` injection is what makes that testable: advancing a day
is a function argument, not a wait.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.enums import EventType, GatewayUsed, PaymentAttemptStatus
from app.gateways.base import (
    HARD_DECLINE_ERROR_CODES,
    RESOLVED_UPSTREAM,
    GatewayResponse,
    GatewayStatusResult,
    PaymentGateway,
    RetryRequest,
    UpstreamStatus,
    is_hard_decline,
)
from app.gateways.local_simulation import (
    SUBSCRIPTION_AUTO_RETRY_DELAY,
    LocalSimulationGateway,
    _unit_interval,
)

T0 = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)

SOFT_DECLINE = "BAD_REQUEST_PAYMENT_INSUFFICIENT_FUNDS"


@pytest.fixture()
def gateway() -> LocalSimulationGateway:
    return LocalSimulationGateway(seed=42)


def retry_request(
    source_ref: str = "pay_TEST0001",
    *,
    event_type: EventType = EventType.PAYMENT_DEGRADED,
    attempt_number: int = 1,
    failure_reason: str | None = SOFT_DECLINE,
    idempotency_key: str | None = None,
    amount: str = "2499.00",
) -> RetryRequest:
    return RetryRequest(
        event_id=f"evt_{source_ref}",
        source_ref=source_ref,
        event_type=event_type,
        amount=Decimal(amount),
        attempt_number=attempt_number,
        idempotency_key=idempotency_key or f"idem_{source_ref}_{attempt_number}",
        failure_reason=failure_reason,
        method="card",
    )


def find_subscription_ref(outcome: UpstreamStatus, seed: int = 42, limit: int = 400) -> str:
    """Find a source_ref whose deterministic auto-retry produces ``outcome``.

    The outcome is a fixed function of the ref, so both branches of the
    lifecycle are reachable without mocking anything.
    """
    for i in range(limit):
        candidate = f"sub_PROBE{i:04d}"
        probe = LocalSimulationGateway(seed=seed)
        probe.initiate_retry(
            retry_request(candidate, event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        result = probe.check_status(
            candidate, EventType.SUBSCRIPTION_FAILED, now=T0 + SUBSCRIPTION_AUTO_RETRY_DELAY
        )
        if result.status == outcome:
            return candidate
    raise AssertionError(f"no source_ref produced {outcome} within {limit} probes")


# --------------------------------------------------------------------------- #
# Interface conformance — Section 5
# --------------------------------------------------------------------------- #


class TestInterfaceConformance:
    def test_implements_the_payment_gateway_interface(self, gateway):
        assert isinstance(gateway, PaymentGateway)

    def test_exposes_the_three_required_methods(self, gateway):
        """Section 5 names exactly these three."""
        for method in ("initiate_retry", "check_status", "cancel"):
            assert callable(getattr(gateway, method))

    def test_identifies_itself_as_the_local_simulator(self, gateway):
        assert gateway.name == GatewayUsed.LOCAL_SIMULATION

    def test_needs_no_credentials(self):
        """Section 5: works without credentials, zero external dependencies."""
        assert LocalSimulationGateway().name == GatewayUsed.LOCAL_SIMULATION

    def test_returns_the_documented_result_types(self, gateway):
        assert isinstance(gateway.initiate_retry(retry_request(), now=T0), GatewayResponse)
        assert isinstance(
            gateway.check_status("pay_X", EventType.PAYMENT_DEGRADED, now=T0), GatewayStatusResult
        )
        assert isinstance(
            gateway.cancel("pay_X", EventType.PAYMENT_DEGRADED, now=T0), GatewayStatusResult
        )


# --------------------------------------------------------------------------- #
# Razorpay subscription lifecycle — Section 5, the headline requirement
# --------------------------------------------------------------------------- #


class TestSubscriptionLifecycle:
    """failed -> auto-retry the NEXT DAY -> halted if that retry also fails."""

    def test_failed_charge_schedules_a_retry_one_day_later(self, gateway):
        response = gateway.initiate_retry(
            retry_request("sub_A", event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        assert response.status == PaymentAttemptStatus.PENDING
        assert response.raw["reason"] == "razorpay_auto_retry_scheduled"
        assert response.raw["auto_retry_at"] == (T0 + timedelta(days=1)).isoformat()

    def test_the_delay_is_exactly_one_day(self):
        """Not a generic backoff — Razorpay retries once, the next day."""
        assert SUBSCRIPTION_AUTO_RETRY_DELAY == timedelta(days=1)

    def test_initiate_retry_does_not_charge_a_subscription(self, gateway):
        """Section 6: react to Razorpay's auto-retry, do not force extra retries."""
        response = gateway.initiate_retry(
            retry_request("sub_B", event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        assert response.raw["executed"] is False
        assert response.raw["forced_retry_suppressed"] is True

    def test_status_is_still_failed_before_the_retry_date(self, gateway):
        gateway.initiate_retry(
            retry_request("sub_C", event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        result = gateway.check_status(
            "sub_C", EventType.SUBSCRIPTION_FAILED, now=T0 + timedelta(hours=23)
        )
        assert result.status == UpstreamStatus.FAILED
        assert result.next_retry_at == T0 + timedelta(days=1)

    def test_failed_retry_moves_the_subscription_to_halted(self, gateway):
        """The exact sequence Section 5 names."""
        ref = find_subscription_ref(UpstreamStatus.HALTED)
        gateway.initiate_retry(
            retry_request(ref, event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        result = gateway.check_status(
            ref, EventType.SUBSCRIPTION_FAILED, now=T0 + timedelta(days=1)
        )
        assert result.status == UpstreamStatus.HALTED

    def test_successful_retry_makes_the_subscription_active_again(self, gateway):
        ref = find_subscription_ref(UpstreamStatus.ACTIVE)
        gateway.initiate_retry(
            retry_request(ref, event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        result = gateway.check_status(
            ref, EventType.SUBSCRIPTION_FAILED, now=T0 + timedelta(days=1)
        )
        assert result.status == UpstreamStatus.ACTIVE

    def test_both_lifecycle_outcomes_are_reachable(self):
        """Neither branch is dead code."""
        assert find_subscription_ref(UpstreamStatus.HALTED)
        assert find_subscription_ref(UpstreamStatus.ACTIVE)

    def test_auto_retry_resolves_exactly_once(self, gateway):
        """Polling repeatedly must not keep re-rolling the outcome.

        The status alone cannot prove this: the roll is deterministic, so a
        re-roll returns the same answer. The observable damage is in the
        history, which is what the audit trail renders — an event that polled
        five times would show five auto-retries the provider never performed.
        Both are asserted here; mutation testing showed the status check alone
        passes against a gateway that re-rolls on every poll.
        """
        ref = find_subscription_ref(UpstreamStatus.HALTED)
        gateway.initiate_retry(
            retry_request(ref, event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        first = gateway.check_status(
            ref, EventType.SUBSCRIPTION_FAILED, now=T0 + timedelta(days=1)
        )
        first_history = list(first.raw["history"])

        for extra_days in (2, 5, 30):
            later = gateway.check_status(
                ref, EventType.SUBSCRIPTION_FAILED, now=T0 + timedelta(days=extra_days)
            )
            assert later.status == first.status
            assert later.raw["history"] == first_history

    def test_repeated_polling_records_exactly_one_auto_retry(self, gateway):
        ref = find_subscription_ref(UpstreamStatus.ACTIVE)
        gateway.initiate_retry(
            retry_request(ref, event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        for extra_days in (1, 2, 3, 4):
            result = gateway.check_status(
                ref, EventType.SUBSCRIPTION_FAILED, now=T0 + timedelta(days=extra_days)
            )
        retries = [e for e in result.raw["history"] if e["event"].startswith("auto_retry")]
        assert len(retries) == 1

    def test_no_next_retry_advertised_once_resolved(self, gateway):
        ref = find_subscription_ref(UpstreamStatus.HALTED)
        gateway.initiate_retry(
            retry_request(ref, event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        result = gateway.check_status(
            ref, EventType.SUBSCRIPTION_FAILED, now=T0 + timedelta(days=1)
        )
        assert result.next_retry_at is None

    def test_engine_cannot_force_extra_retries(self, gateway):
        """Section 6. Forcing attempts 2 and 3 must never produce a charge."""
        for attempt in (1, 2, 3):
            response = gateway.initiate_retry(
                retry_request(
                    "sub_FORCE",
                    event_type=EventType.SUBSCRIPTION_FAILED,
                    attempt_number=attempt,
                    idempotency_key=f"force_{attempt}",
                ),
                now=T0,
            )
            assert response.raw["executed"] is False
            assert response.raw["forced_retry_suppressed"] is True

    def test_retry_after_halted_is_refused(self, gateway):
        """Section 6: `halted` is a hard stop."""
        ref = find_subscription_ref(UpstreamStatus.HALTED)
        gateway.initiate_retry(
            retry_request(ref, event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        gateway.check_status(ref, EventType.SUBSCRIPTION_FAILED, now=T0 + timedelta(days=1))

        response = gateway.initiate_retry(
            retry_request(
                ref,
                event_type=EventType.SUBSCRIPTION_FAILED,
                attempt_number=2,
                idempotency_key=f"after_halt_{ref}",
            ),
            now=T0 + timedelta(days=2),
        )
        assert response.retry_refused is True
        assert response.raw["executed"] is False

    def test_lifecycle_history_records_the_sequence(self, gateway):
        """The audit trail must be able to show what the provider did."""
        ref = find_subscription_ref(UpstreamStatus.HALTED)
        gateway.initiate_retry(
            retry_request(ref, event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        result = gateway.check_status(
            ref, EventType.SUBSCRIPTION_FAILED, now=T0 + timedelta(days=1)
        )
        events = [entry["event"] for entry in result.raw["history"]]
        assert events == ["charge_failed", "auto_retry_failed"]

    def test_unknown_subscription_reports_pending(self, gateway):
        result = gateway.check_status("sub_NEVER_SEEN", EventType.SUBSCRIPTION_FAILED, now=T0)
        assert result.status == UpstreamStatus.PENDING


# --------------------------------------------------------------------------- #
# Hard declines — Section 6: no retry, immediate stop
# --------------------------------------------------------------------------- #


class TestHardDeclinesAreNeverRetried:
    def test_the_two_spec_named_codes_are_hard(self):
        assert HARD_DECLINE_ERROR_CODES == {
            "GATEWAY_ERROR_ISSUER_DECLINED",
            "BAD_REQUEST_MANDATE_BANK_REJECTED",
        }

    @pytest.mark.parametrize("code", sorted(HARD_DECLINE_ERROR_CODES))
    def test_is_hard_decline_detects_them(self, code):
        assert is_hard_decline(code) is True

    @pytest.mark.parametrize("code", [SOFT_DECLINE, "GATEWAY_ERROR_TIMEOUT", None, ""])
    def test_is_hard_decline_rejects_everything_else(self, code):
        assert is_hard_decline(code) is False

    @pytest.mark.parametrize("code", sorted(HARD_DECLINE_ERROR_CODES))
    def test_hard_decline_refuses_to_execute(self, gateway, code):
        response = gateway.initiate_retry(
            retry_request("pay_HARD", failure_reason=code, idempotency_key=f"hard_{code}"), now=T0
        )
        assert response.retry_refused is True
        assert response.raw["executed"] is False
        assert response.status == PaymentAttemptStatus.FAILED
        assert response.raw["reason"] == "hard_decline_not_retryable"

    def test_soft_decline_does_execute(self, gateway):
        """Control: the refusal must be specific to hard declines."""
        response = gateway.initiate_retry(
            retry_request("pay_SOFT", failure_reason=SOFT_DECLINE), now=T0
        )
        assert response.retry_refused is False
        assert response.raw["executed"] is True

    def test_hard_decline_never_succeeds_at_any_attempt_number(self, gateway):
        for attempt in (1, 2, 3, 4):
            response = gateway.initiate_retry(
                retry_request(
                    "pay_HARD_LOOP",
                    failure_reason="GATEWAY_ERROR_ISSUER_DECLINED",
                    attempt_number=attempt,
                    idempotency_key=f"hl_{attempt}",
                ),
                now=T0,
            )
            assert response.status != PaymentAttemptStatus.SUCCESS
            assert response.raw["executed"] is False


# --------------------------------------------------------------------------- #
# Section 9 — race conditions and idempotency
# --------------------------------------------------------------------------- #


class TestAlreadyResolvedUpstream:
    def test_seeded_paid_object_reports_paid(self, gateway):
        gateway.seed_upstream_state({"pay_PAID": UpstreamStatus.PAID})
        result = gateway.check_status("pay_PAID", EventType.PAYMENT_DEGRADED, now=T0)
        assert result.status == UpstreamStatus.PAID
        assert result.is_resolved_externally is True

    def test_seeding_accepts_plain_strings(self, gateway):
        """The generator's upstream_world is JSON-friendly strings."""
        gateway.seed_upstream_state({"pay_S": "paid"})
        assert gateway.check_status("pay_S", EventType.PAYMENT_DEGRADED, now=T0).status == (
            UpstreamStatus.PAID
        )

    def test_unseen_object_is_pending_not_resolved(self, gateway):
        result = gateway.check_status("pay_UNSEEN", EventType.PAYMENT_DEGRADED, now=T0)
        assert result.status == UpstreamStatus.PENDING
        assert result.is_resolved_externally is False

    def test_executing_against_a_paid_object_is_refused(self, gateway):
        """Section 9: stop immediately, do not double-charge."""
        gateway.seed_upstream_state({"pay_PAID2": UpstreamStatus.PAID})
        response = gateway.initiate_retry(retry_request("pay_PAID2"), now=T0)
        assert response.retry_refused is True
        assert response.raw["executed"] is False
        assert response.failure_reason == "ALREADY_RESOLVED_UPSTREAM"

    def test_executing_against_a_cancelled_object_is_refused(self, gateway):
        gateway.seed_upstream_state({"pay_CANC": UpstreamStatus.CANCELLED})
        response = gateway.initiate_retry(retry_request("pay_CANC"), now=T0)
        assert response.retry_refused is True

    def test_resolved_upstream_set_matches_the_spec(self):
        assert RESOLVED_UPSTREAM == {
            UpstreamStatus.PAID,
            UpstreamStatus.CANCELLED,
            UpstreamStatus.ACTIVE,
        }

    def test_seeded_subscription_state_lands_on_the_lifecycle(self, gateway):
        gateway.seed_upstream_state({"sub_ACT": UpstreamStatus.ACTIVE})
        result = gateway.check_status("sub_ACT", EventType.SUBSCRIPTION_FAILED, now=T0)
        assert result.status == UpstreamStatus.ACTIVE


class TestIdempotency:
    def test_replaying_a_key_returns_the_same_result(self, gateway):
        request = retry_request("pay_IDEM", idempotency_key="SAME")
        first = gateway.initiate_retry(request, now=T0)
        second = gateway.initiate_retry(request, now=T0 + timedelta(hours=5))
        assert (first.status, first.provider_ref) == (second.status, second.provider_ref)

    def test_replay_is_flagged_as_a_replay(self, gateway):
        request = retry_request("pay_IDEM2", idempotency_key="SAME2")
        assert gateway.initiate_retry(request, now=T0).raw.get("idempotent_replay") is None
        assert gateway.initiate_retry(request, now=T0).raw["idempotent_replay"] is True

    def test_different_keys_execute_separately(self, gateway):
        a = gateway.initiate_retry(retry_request("pay_K", idempotency_key="K1"), now=T0)
        b = gateway.initiate_retry(retry_request("pay_K", idempotency_key="K2"), now=T0)
        assert a.raw.get("idempotent_replay") is None
        assert b.raw.get("idempotent_replay") is None

    def test_hard_decline_refusal_is_also_idempotent(self, gateway):
        request = retry_request(
            "pay_HI", failure_reason="GATEWAY_ERROR_ISSUER_DECLINED", idempotency_key="HI"
        )
        gateway.initiate_retry(request, now=T0)
        replay = gateway.initiate_retry(request, now=T0)
        assert replay.retry_refused is True
        assert replay.raw["idempotent_replay"] is True


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_unit_interval_is_stable_and_in_range(self):
        for _ in range(3):
            value = _unit_interval(42, "abc", 1, "charge")
            assert value == _unit_interval(42, "abc", 1, "charge")
            assert 0.0 <= value < 1.0

    def test_different_inputs_give_different_values(self):
        assert _unit_interval(42, "a", 1, "charge") != _unit_interval(42, "a", 2, "charge")
        assert _unit_interval(42, "a", 1, "charge") != _unit_interval(43, "a", 1, "charge")

    def test_two_gateways_with_the_same_seed_agree(self):
        refs = [f"pay_D{i:03d}" for i in range(40)]
        results = []
        for _ in range(2):
            gw = LocalSimulationGateway(seed=42)
            results.append(
                [
                    (
                        lambda r: (r.status.value, r.provider_ref, r.retry_refused)
                    )(gw.initiate_retry(retry_request(ref), now=T0))
                    for ref in refs
                ]
            )
        assert results[0] == results[1]

    def test_a_different_seed_changes_outcomes(self):
        refs = [f"pay_S{i:03d}" for i in range(60)]
        runs = []
        for seed in (42, 99):
            gw = LocalSimulationGateway(seed=seed)
            runs.append([gw.initiate_retry(retry_request(r), now=T0).status for r in refs])
        assert runs[0] != runs[1]

    def test_outcomes_are_not_all_identical(self):
        """A simulator that always succeeds would make the demo meaningless."""
        gw = LocalSimulationGateway(seed=42)
        statuses = {
            gw.initiate_retry(retry_request(f"pay_M{i:03d}"), now=T0).status for i in range(60)
        }
        assert len(statuses) > 1

    def test_reset_clears_simulated_state(self, gateway):
        request = retry_request("pay_R", idempotency_key="R1")
        gateway.initiate_retry(request, now=T0)
        gateway.reset()
        assert gateway.initiate_retry(request, now=T0).raw.get("idempotent_replay") is None


# --------------------------------------------------------------------------- #
# Cancel
# --------------------------------------------------------------------------- #


class TestCancel:
    def test_cancel_marks_a_payment_cancelled(self, gateway):
        result = gateway.cancel("pay_C", EventType.PAYMENT_DEGRADED, reason="dnc", now=T0)
        assert result.status == UpstreamStatus.CANCELLED
        assert gateway.check_status("pay_C", EventType.PAYMENT_DEGRADED, now=T0).status == (
            UpstreamStatus.CANCELLED
        )

    def test_cancel_stops_a_pending_subscription_retry(self, gateway):
        gateway.initiate_retry(
            retry_request("sub_CX", event_type=EventType.SUBSCRIPTION_FAILED), now=T0
        )
        gateway.cancel("sub_CX", EventType.SUBSCRIPTION_FAILED, reason="revoked", now=T0)
        later = gateway.check_status(
            "sub_CX", EventType.SUBSCRIPTION_FAILED, now=T0 + timedelta(days=3)
        )
        assert later.status == UpstreamStatus.CANCELLED

    def test_cancel_records_the_reason(self, gateway):
        result = gateway.cancel("pay_CR", EventType.PAYMENT_DEGRADED, reason="do_not_contact", now=T0)
        assert result.raw["reason"] == "do_not_contact"
