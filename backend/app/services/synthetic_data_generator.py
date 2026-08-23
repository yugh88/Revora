"""Synthetic data generator. BUILD_SPEC Section 11.

Produces Indian-context risk events with edge cases injected at the rates the
spec names, using a FIXED SEED (42) so batch runs are reproducible across demo
runs and across repeated testing.

Overlapping, not bucketed
-------------------------
Section 11's note is explicit: the percentages are "independent probabilities
checked per record, not mutually exclusive buckets — a single record can land in
more than one category (e.g. a duplicate that's also malformed)". Each edge case
is therefore an independent roll per record, and a record carries a SET of
flags. Bucketing them would produce data that never exercises the interactions
where real systems break.

    ~10%  duplicate / replayed          idempotency test
    ~8%   missing / malformed fields    validation test
    ~10%  already resolved externally   race-condition test
    ~10%  hard decline                  gating test — must NOT retry
    ~5%   ambiguous root cause          low-confidence bucket, not force-classified
    few   multi-event customers         per-customer cooldown / DNC scoping test

Where the ground truth lives, and why not on the event
-------------------------------------------------------
Section 4a trains the diagnosis classifier on "your own labeled synthetic data
(the generator already assigns ground-truth root causes)". Those labels are
returned on :class:`SyntheticRecord`, NOT written into ``raw_signal``. If the
label rode along inside the event, the rule-based diagnosis engine could read it
and every accuracy number in the demo would be meaningless. The label is
available to the trainer and to tests; it is not visible to the pipeline.

Likewise, "already resolved externally" is NOT flagged on the event. It is
returned as ``upstream_world`` and seeded into the gateway, so the engine can
only discover it by calling ``check_status()`` before executing — which is
exactly the Section 9 behaviour under test.

IST without a tzdata dependency
--------------------------------
Timestamps are generated in IST as Section 11 requires, using a fixed
``UTC+05:30`` offset rather than ``zoneinfo("Asia/Kolkata")``. India has never
observed DST, so the fixed offset is exactly correct, and it keeps the container
free of a system tzdata package that slim base images omit. Storage remains UTC
(``database.TZDateTime``); IST is the wire and display form.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from app.enums import EventType, GatewayUsed, RootCauseCode
from app.gateways.base import UpstreamStatus

#: Section 11: fixed seed so runs are reproducible.
DEFAULT_SEED = 42

#: India Standard Time. Fixed offset — India observes no DST.
IST = timezone(timedelta(hours=5, minutes=30), "IST")

# --------------------------------------------------------------------------- #
# Injection rates — Section 11
# --------------------------------------------------------------------------- #

RATE_DUPLICATE = 0.10
RATE_MALFORMED = 0.08
RATE_RESOLVED_EXTERNALLY = 0.10
#: Section 11 wants ~10% of the BATCH to be hard declines. But Section 6 defines
#: a hard, no-retry cause for only two of the five event types
#: (issuer_declined on payment_degraded, bank_rejected on mandate_failed), so
#: only ~40% of records are even eligible. Rolling 10% on those would cap the
#: batch rate at ~4% and the gating test would be under-exercised. The
#: per-eligible-record probability is therefore scaled up so the batch-level
#: rate lands on target, and it stays correct automatically if the cause map
#: below ever changes. Vocabulary is unchanged — still exactly Section 6's two
#: causes.
RATE_HARD_DECLINE = 0.10
RATE_AMBIGUOUS = 0.05
#: Share of records drawn from the small repeat-customer pool, producing the
#: "few multi-event customers" the spec asks for.
RATE_REPEAT_CUSTOMER = 0.22
REPEAT_CUSTOMER_POOL_SIZE = 6


class EdgeCase(str, Enum):
    """Edge-case flags a generated record may carry. Overlapping by design.

    Generator-local rather than in app/enums.py: no column stores these. They
    describe how a record was constructed, for verification and for honest
    batch reporting.
    """

    DUPLICATE = "duplicate"
    MALFORMED = "malformed"
    RESOLVED_EXTERNALLY = "resolved_externally"
    HARD_DECLINE = "hard_decline"
    AMBIGUOUS = "ambiguous"
    MULTI_EVENT_CUSTOMER = "multi_event_customer"


# --------------------------------------------------------------------------- #
# Indian context vocabulary
# --------------------------------------------------------------------------- #

MERCHANT_NAMES = [
    "Chai Point Retail Pvt Ltd",
    "Kirana Connect Technologies",
    "Sundar Textiles Exports",
    "Bharat Fintech Solutions",
    "Deccan Logistics Pvt Ltd",
]

CUSTOMER_FIRST_NAMES = [
    "Aarav", "Diya", "Rohan", "Ananya", "Vikram", "Priya", "Arjun", "Meera",
    "Karthik", "Sneha", "Rahul", "Ishita", "Aditya", "Kavya", "Nikhil", "Pooja",
]
CUSTOMER_LAST_NAMES = [
    "Sharma", "Iyer", "Reddy", "Patel", "Banerjee", "Nair", "Gupta", "Desai",
    "Chauhan", "Menon", "Joshi", "Rao",
]

#: UPI / NACH / card mix, per Section 11, weighted per event type.
METHODS_BY_EVENT_TYPE: dict[EventType, list[tuple[str, float]]] = {
    EventType.PAYMENT_DEGRADED: [("upi", 0.42), ("card", 0.34), ("netbanking", 0.16), ("wallet", 0.08)],
    EventType.CHECKOUT_ABANDONED: [("upi", 0.50), ("card", 0.30), ("netbanking", 0.20)],
    EventType.SUBSCRIPTION_FAILED: [("card", 0.55), ("upi_autopay", 0.45)],
    EventType.INVOICE_OVERDUE: [("neft", 0.40), ("upi", 0.35), ("rtgs", 0.25)],
    EventType.MANDATE_FAILED: [("nach", 0.55), ("upi_autopay", 0.45)],
}

#: Ground-truth root cause -> realistic Razorpay-vocabulary error code.
#: invoice_overdue causes have no gateway code: no charge was ever attempted,
#: so the signal is a due date, not an error.
ERROR_CODE_BY_ROOT_CAUSE: dict[RootCauseCode, str | None] = {
    RootCauseCode.CARD_EXPIRED: "BAD_REQUEST_CARD_EXPIRED",
    RootCauseCode.INSUFFICIENT_FUNDS: "BAD_REQUEST_PAYMENT_INSUFFICIENT_FUNDS",
    RootCauseCode.ISSUER_DECLINED: "GATEWAY_ERROR_ISSUER_DECLINED",
    RootCauseCode.NETWORK_TIMEOUT: "GATEWAY_ERROR_TIMEOUT",
    RootCauseCode.THREE_DS_FAILED: "BAD_REQUEST_3DS_AUTHENTICATION_FAILED",
    RootCauseCode.BANK_SERVER_DOWN: "GATEWAY_ERROR_ISSUER_DOWN",
    RootCauseCode.RISK_ENGINE_BLOCKED: "BAD_REQUEST_RISK_THRESHOLD_EXCEEDED",
    RootCauseCode.MANDATE_REVOKED: "BAD_REQUEST_MANDATE_REVOKED",
    RootCauseCode.USER_PAUSED: "BAD_REQUEST_SUBSCRIPTION_PAUSED",
    RootCauseCode.HALTED_AFTER_MAX_RETRIES: "BAD_REQUEST_SUBSCRIPTION_HALTED",
    RootCauseCode.NOT_AUTHENTICATED: "BAD_REQUEST_MANDATE_NOT_AUTHENTICATED",
    RootCauseCode.INSUFFICIENT_BALANCE: "BAD_REQUEST_MANDATE_INSUFFICIENT_BALANCE",
    RootCauseCode.BANK_REJECTED: "BAD_REQUEST_MANDATE_BANK_REJECTED",
    RootCauseCode.EXPIRED: "BAD_REQUEST_MANDATE_EXPIRED",
    RootCauseCode.REVOKED: "BAD_REQUEST_MANDATE_REVOKED",
    RootCauseCode.OTP_TIMEOUT: "BAD_REQUEST_PAYMENT_TIMED_OUT",
    RootCauseCode.PAYMENT_STEP_DROPPED: "BAD_REQUEST_PAYMENT_FAILED",
    RootCauseCode.SESSION_EXPIRED: "BAD_REQUEST_PAYMENT_TIMED_OUT",
    RootCauseCode.PRICE_SHOCK: None,
    RootCauseCode.NO_PREFERRED_METHOD: None,
    RootCauseCode.UNKNOWN: "BAD_REQUEST_PAYMENT_FAILED",
}

#: The Section 6 hard causes, per event type, used for the ~10% hard-decline
#: injection. These must NOT be retried.
HARD_DECLINE_CAUSE_BY_EVENT_TYPE: dict[EventType, RootCauseCode] = {
    EventType.PAYMENT_DEGRADED: RootCauseCode.ISSUER_DECLINED,
    EventType.MANDATE_FAILED: RootCauseCode.BANK_REJECTED,
}

#: Probability applied per ELIGIBLE record, derived so the batch-level rate
#: lands on RATE_HARD_DECLINE. With 2 of 5 event types eligible this is
#: 0.10 * 5/2 = 0.25. Capped at 1.0 for safety.
RATE_HARD_DECLINE_ON_ELIGIBLE = min(
    1.0, RATE_HARD_DECLINE * len(EventType) / max(1, len(HARD_DECLINE_CAUSE_BY_EVENT_TYPE))
)

#: Generic code carrying no discriminating information — this is what makes the
#: ~5% ambiguous records genuinely ambiguous rather than merely labelled so.
AMBIGUOUS_ERROR_CODE = "BAD_REQUEST_PAYMENT_FAILED"

#: Realistic INR ranges (rupees) per event type.
AMOUNT_RANGE_BY_EVENT_TYPE: dict[EventType, tuple[int, int]] = {
    EventType.PAYMENT_DEGRADED: (199, 9_999),
    EventType.CHECKOUT_ABANDONED: (299, 14_999),
    EventType.SUBSCRIPTION_FAILED: (149, 4_999),
    EventType.INVOICE_OVERDUE: (2_000, 50_000),
    EventType.MANDATE_FAILED: (500, 25_000),
}
#: B2B receivables are an order of magnitude larger.
B2B_AMOUNT_RANGE = (50_000, 2_500_000)
RATE_B2B_ON_INVOICE = 0.40


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SyntheticRecord:
    """One generated record.

    Attributes:
        payload: RiskEvent-shaped dict, ready for POST /events. Malformed
            records deliberately violate this shape — that is the point.
        ground_truth_root_cause: The label. None for ambiguous records, which
            genuinely have no single correct answer and must land in the
            low-confidence bucket rather than being force-classified.
        edge_cases: Every flag this record carries. May contain several.
    """

    payload: dict[str, Any]
    ground_truth_root_cause: RootCauseCode | None
    edge_cases: frozenset[EdgeCase]

    def has(self, edge_case: EdgeCase) -> bool:
        return edge_case in self.edge_cases


@dataclass(frozen=True)
class SyntheticBatch:
    """A complete reproducible batch."""

    merchant: dict[str, Any]
    customers: list[dict[str, Any]]
    records: list[SyntheticRecord]
    #: source_ref -> UpstreamStatus value. Feed to
    #: ``LocalSimulationGateway.seed_upstream_state()``. Deliberately separate
    #: from the events so the engine must call check_status() to find out.
    upstream_world: dict[str, str]
    stats: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Generator
# --------------------------------------------------------------------------- #


def _weighted_choice(rng: random.Random, options: list[tuple[str, float]]) -> str:
    return rng.choices([o[0] for o in options], weights=[o[1] for o in options], k=1)[0]


def generate_batch(
    size: int = 50,
    *,
    seed: int = DEFAULT_SEED,
    merchant_id: str = "mer_revora_demo",
    now: datetime | None = None,
) -> SyntheticBatch:
    """Generate ``size`` synthetic records with Section 11's edge cases injected.

    Args:
        size: Number of records. /batch supports 50 and 500 (Section 10).
        seed: Fixed at 42 by default for reproducibility.
        merchant_id: Merchant all events belong to.
        now: Reference instant. Defaults to the current time in IST. Pass an
            explicit value for byte-identical output across machines.

    Returns:
        A :class:`SyntheticBatch`. Record count is exactly ``size``: duplicates
        REPLACE a slot by replaying an earlier record rather than being appended,
        so a caller asking for 500 always processes 500.
    """
    rng = random.Random(seed)
    reference = now or datetime.now(IST)

    # --- customers -------------------------------------------------------- #
    # A small repeat pool plus a long tail. Repeat customers are what exercise
    # per-customer cooldown and do-not-contact scoping.
    repeat_pool = [f"cust_repeat_{i:02d}" for i in range(REPEAT_CUSTOMER_POOL_SIZE)]
    unique_pool = [f"cust_{i:04d}" for i in range(size)]

    customers: dict[str, dict[str, Any]] = {}

    def ensure_customer(customer_id: str) -> dict[str, Any]:
        if customer_id not in customers:
            crng = random.Random(f"{seed}:{customer_id}")
            success_rate = round(crng.uniform(0.35, 0.97), 3)
            customers[customer_id] = {
                "customer_id": customer_id,
                "name": (
                    f"{crng.choice(CUSTOMER_FIRST_NAMES)} {crng.choice(CUSTOMER_LAST_NAMES)}"
                ),
                "payment_success_rate": success_rate,
                "payment_failure_rate": round(1.0 - success_rate, 3),
                "lifetime_value": Decimal(str(crng.randrange(2_000, 900_000))) ,
                "avg_payment_delay_days": round(crng.uniform(0.0, 21.0), 1),
                "preferred_channel": crng.choice(["email", "sms", "whatsapp", "in_app"]),
                # A minority are do-not-contact; the engine must respect it.
                "do_not_contact": crng.random() < 0.07,
            }
        return customers[customer_id]

    # --- records ---------------------------------------------------------- #
    event_types = list(EventType)
    records: list[SyntheticRecord] = []
    upstream_world: dict[str, str] = {}

    for index in range(size):
        flags: set[EdgeCase] = set()

        # --- duplicate: independent roll, replays an EARLIER record --------
        if records and rng.random() < RATE_DUPLICATE:
            original = rng.choice(records)
            replay_payload = _deepish_copy(original.payload)
            raw = replay_payload.get("raw_signal")
            if isinstance(raw, dict):
                raw["replayed"] = True
                raw["replay_of_event_id"] = original.payload.get("id")
            records.append(
                SyntheticRecord(
                    payload=replay_payload,
                    ground_truth_root_cause=original.ground_truth_root_cause,
                    edge_cases=frozenset(original.edge_cases | {EdgeCase.DUPLICATE}),
                )
            )
            continue

        # --- even spread across the 5 core types (Section 11) --------------
        event_type = event_types[index % len(event_types)]

        # --- customer: repeat pool or long tail ----------------------------
        if rng.random() < RATE_REPEAT_CUSTOMER:
            customer_id = rng.choice(repeat_pool)
            flags.add(EdgeCase.MULTI_EVENT_CUSTOMER)
        else:
            customer_id = unique_pool[index]
        ensure_customer(customer_id)

        # --- root cause: hard decline, ambiguous, or ordinary --------------
        hard_cause = HARD_DECLINE_CAUSE_BY_EVENT_TYPE.get(event_type)
        is_hard = hard_cause is not None and rng.random() < RATE_HARD_DECLINE_ON_ELIGIBLE
        is_ambiguous = (not is_hard) and rng.random() < RATE_AMBIGUOUS

        if is_hard:
            flags.add(EdgeCase.HARD_DECLINE)
            root_cause: RootCauseCode | None = hard_cause
            error_code = ERROR_CODE_BY_ROOT_CAUSE[hard_cause]
        elif is_ambiguous:
            flags.add(EdgeCase.AMBIGUOUS)
            # No label: genuinely undetermined, so it must reach /exceptions
            # rather than being forced into a class.
            root_cause = None
            error_code = AMBIGUOUS_ERROR_CODE
        else:
            root_cause = _pick_ordinary_root_cause(rng, event_type)
            error_code = ERROR_CODE_BY_ROOT_CAUSE.get(root_cause)

        # --- amount, B2B flag ---------------------------------------------
        is_b2b = event_type == EventType.INVOICE_OVERDUE and rng.random() < RATE_B2B_ON_INVOICE
        low, high = B2B_AMOUNT_RANGE if is_b2b else AMOUNT_RANGE_BY_EVENT_TYPE[event_type]
        amount = Decimal(f"{rng.randrange(low, high)}.{rng.randrange(0, 100):02d}")

        detected_at = reference - timedelta(
            days=rng.randrange(0, 45), hours=rng.randrange(0, 24), minutes=rng.randrange(0, 60)
        )
        source_ref = _source_ref(rng, event_type, index)

        raw_signal: dict[str, Any] = {
            "payment_method": _weighted_choice(rng, METHODS_BY_EVENT_TYPE[event_type]),
            "attempt_number": 1,
            "customer_name": customers[customer_id]["name"],
        }
        if error_code is not None:
            raw_signal["gateway_error_code"] = error_code
        if is_b2b:
            raw_signal["channel"] = "b2b"
            raw_signal["gstin"] = _synthetic_gstin(rng)
        if event_type == EventType.INVOICE_OVERDUE:
            days_overdue = rng.randrange(1, 75)
            raw_signal["days_overdue"] = days_overdue
            raw_signal["due_date"] = (detected_at - timedelta(days=days_overdue)).date().isoformat()

        payload: dict[str, Any] = {
            "id": f"evt_syn_{seed}_{index:05d}",
            "type": event_type.value,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "amount": amount,
            "currency": "INR",
            "source_ref": source_ref,
            "detected_at": detected_at,
            "raw_signal": raw_signal,
            "gateway_used": GatewayUsed.LOCAL_SIMULATION.value,
            "correlation_id": f"corr_syn_{seed}_{index:05d}",
        }

        # --- already resolved externally: recorded in the WORLD, not the event
        if rng.random() < RATE_RESOLVED_EXTERNALLY:
            flags.add(EdgeCase.RESOLVED_EXTERNALLY)
            upstream_world[source_ref] = (
                UpstreamStatus.ACTIVE.value
                if event_type == EventType.SUBSCRIPTION_FAILED
                else UpstreamStatus.PAID.value
            )

        # --- malformed: independent roll, applied LAST so it can corrupt any
        #     record including a hard-decline or externally-resolved one -----
        if rng.random() < RATE_MALFORMED:
            flags.add(EdgeCase.MALFORMED)
            payload = _corrupt(rng, payload)

        records.append(
            SyntheticRecord(
                payload=payload,
                ground_truth_root_cause=root_cause,
                edge_cases=frozenset(flags),
            )
        )

    merchant = {"id": merchant_id, "name": MERCHANT_NAMES[seed % len(MERCHANT_NAMES)]}
    batch = SyntheticBatch(
        merchant=merchant,
        customers=list(customers.values()),
        records=records,
        upstream_world=upstream_world,
        stats=summarize(records, seed=seed, size=size),
    )
    return batch


def _pick_ordinary_root_cause(rng: random.Random, event_type: EventType) -> RootCauseCode:
    """Pick a non-hard root cause valid for this event type.

    ``broken_ptp`` is excluded: it is never an originating cause. It only ever
    arises when the promise watcher raises a follow-up event (Section 4).
    """
    from app.enums import ROOT_CAUSES_BY_EVENT_TYPE

    hard = HARD_DECLINE_CAUSE_BY_EVENT_TYPE.get(event_type)
    candidates = sorted(
        cause.value
        for cause in ROOT_CAUSES_BY_EVENT_TYPE[event_type]
        if cause != hard and cause != RootCauseCode.BROKEN_PTP
    )
    return RootCauseCode(rng.choice(candidates))


def _source_ref(rng: random.Random, event_type: EventType, index: int) -> str:
    prefix = {
        EventType.PAYMENT_DEGRADED: "pay",
        EventType.CHECKOUT_ABANDONED: "order",
        EventType.SUBSCRIPTION_FAILED: "sub",
        EventType.INVOICE_OVERDUE: "inv",
        EventType.MANDATE_FAILED: "token",
    }[event_type]
    suffix = "".join(rng.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=10))
    return f"{prefix}_{suffix}"


def _synthetic_gstin(rng: random.Random) -> str:
    """Structurally-shaped (not checksum-valid) GSTIN for B2B realism."""
    state = f"{rng.randrange(1, 38):02d}"
    pan = (
        "".join(rng.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5))
        + "".join(rng.choices("0123456789", k=4))
        + rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    )
    return f"{state}{pan}{rng.randrange(1, 9)}Z{rng.choice('0123456789')}"


def _deepish_copy(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy a payload one level deep, including its raw_signal dict."""
    clone = dict(payload)
    if isinstance(clone.get("raw_signal"), dict):
        clone["raw_signal"] = dict(clone["raw_signal"])
    return clone


def _corrupt(rng: random.Random, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one realistic malformation. Section 11's ~8% validation test.

    Each variant is something a real webhook actually does: a dropped field, a
    wrong type, a negative amount, an unsupported currency.
    """
    corrupted = _deepish_copy(payload)
    variant = rng.choice(
        [
            "drop_amount",
            "negative_amount",
            "non_numeric_amount",
            "drop_customer_id",
            "bad_currency",
            "drop_source_ref",
            "raw_signal_not_an_object",
        ]
    )
    if variant == "drop_amount":
        corrupted.pop("amount", None)
    elif variant == "negative_amount":
        corrupted["amount"] = Decimal("-1") * corrupted.get("amount", Decimal("100.00"))
    elif variant == "non_numeric_amount":
        corrupted["amount"] = "not-a-number"
    elif variant == "drop_customer_id":
        corrupted.pop("customer_id", None)
    elif variant == "bad_currency":
        corrupted["currency"] = "XYZ"
    elif variant == "drop_source_ref":
        corrupted["source_ref"] = None
    elif variant == "raw_signal_not_an_object":
        corrupted["raw_signal"] = "payment failed"
    corrupted["_malformation"] = variant
    return corrupted


def summarize(records: list[SyntheticRecord], *, seed: int, size: int) -> dict[str, Any]:
    """Measured composition of a batch — counted, never asserted.

    Section 11 closes with "a 100% resolution rate on the batch is a red flag,
    not a win". These are the numbers that let that judgement be made honestly.
    """
    counts = {case.value: 0 for case in EdgeCase}
    for record in records:
        for case in record.edge_cases:
            counts[case.value] += 1

    by_type: dict[str, int] = {}
    customer_event_counts: dict[str, int] = {}
    for record in records:
        event_type = record.payload.get("type")
        if isinstance(event_type, str):
            by_type[event_type] = by_type.get(event_type, 0) + 1
        customer_id = record.payload.get("customer_id")
        if isinstance(customer_id, str):
            customer_event_counts[customer_id] = customer_event_counts.get(customer_id, 0) + 1

    multi_event_customers = {c: n for c, n in customer_event_counts.items() if n > 1}

    return {
        "seed": seed,
        "size": size,
        "generated": len(records),
        "edge_case_counts": counts,
        "edge_case_rates": {k: round(v / len(records), 4) if records else 0.0 for k, v in counts.items()},
        "by_event_type": by_type,
        "labelled_records": sum(1 for r in records if r.ground_truth_root_cause is not None),
        "unlabelled_ambiguous_records": sum(
            1 for r in records if r.ground_truth_root_cause is None
        ),
        "multi_event_customer_count": len(multi_event_customers),
        "max_events_for_one_customer": max(customer_event_counts.values(), default=0),
        "records_with_multiple_edge_cases": sum(1 for r in records if len(r.edge_cases) > 1),
    }
