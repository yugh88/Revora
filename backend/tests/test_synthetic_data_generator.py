"""Tests for services/synthetic_data_generator.py. BUILD_SPEC Section 11.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

Two things these tests are careful about.

First, rates are asserted with TOLERANCE, not exact counts. Section 11 specifies
independent probabilities per record; pinning an exact count would only pass by
turning those probabilities into a quota, which is a different — and worse —
generator. Bounds are wide enough that an unlucky draw does not fail the build,
tight enough that a broken injection does.

Second, a batch is generated once per module and shared. Generating 500 records
inside 40 separate tests is wasted work, and the batch is immutable anyway.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.enums import ROOT_CAUSES_BY_EVENT_TYPE, EventType, GatewayUsed, RootCauseCode
from app.gateways.base import UpstreamStatus, is_hard_decline
from app.gateways.local_simulation import LocalSimulationGateway
from app.services.synthetic_data_generator import (
    AMBIGUOUS_ERROR_CODE,
    DEFAULT_SEED,
    HARD_DECLINE_CAUSE_BY_EVENT_TYPE,
    IST,
    RATE_AMBIGUOUS,
    RATE_DUPLICATE,
    RATE_HARD_DECLINE,
    RATE_HARD_DECLINE_ON_ELIGIBLE,
    RATE_MALFORMED,
    RATE_RESOLVED_EXTERNALLY,
    EdgeCase,
    SyntheticBatch,
    generate_batch,
)

#: Fixed reference instant so output is identical on every machine.
REFERENCE = datetime(2026, 8, 23, 14, 30, tzinfo=IST)

BATCH_SIZE = 500

REQUIRED_FIELDS = (
    "id",
    "type",
    "merchant_id",
    "customer_id",
    "amount",
    "currency",
    "source_ref",
    "detected_at",
    "raw_signal",
)


@pytest.fixture(scope="module")
def batch() -> SyntheticBatch:
    return generate_batch(BATCH_SIZE, seed=DEFAULT_SEED, now=REFERENCE)


@pytest.fixture(scope="module")
def clean_records(batch: SyntheticBatch):
    """Records that are not malformed — safe to read structurally."""
    return [r for r in batch.records if not r.has(EdgeCase.MALFORMED)]


def rate(batch: SyntheticBatch, case: EdgeCase) -> float:
    return batch.stats["edge_case_rates"][case.value]


# --------------------------------------------------------------------------- #
# Shape and Indian context — Section 11
# --------------------------------------------------------------------------- #


class TestBatchShape:
    def test_seed_is_42(self):
        """Section 11 fixes the seed."""
        assert DEFAULT_SEED == 42

    def test_generates_exactly_the_requested_size(self, batch):
        """Duplicates replace a slot rather than being appended, so a caller
        asking for 500 always processes 500."""
        assert len(batch.records) == BATCH_SIZE
        assert batch.stats["generated"] == BATCH_SIZE

    def test_small_batch_size_also_works(self):
        """Section 10 offers 50 and 500."""
        assert len(generate_batch(50, now=REFERENCE).records) == 50

    def test_all_five_core_event_types_appear(self, batch):
        assert set(batch.stats["by_event_type"]) == {t.value for t in EventType}

    def test_distribution_across_event_types_is_even(self, batch):
        """Section 11: even distribution across the 5 core types."""
        counts = batch.stats["by_event_type"]
        expected = BATCH_SIZE / len(EventType)
        for event_type, count in counts.items():
            assert abs(count - expected) < expected * 0.30, f"{event_type} skewed: {count}"

    def test_no_event_types_beyond_the_five(self, batch):
        for record in batch.records:
            assert record.payload["type"] in {t.value for t in EventType}

    def test_currency_is_inr(self, clean_records):
        assert {r.payload["currency"] for r in clean_records} == {"INR"}

    def test_amounts_are_positive_decimals(self, clean_records):
        for record in clean_records:
            assert isinstance(record.payload["amount"], Decimal)
            assert record.payload["amount"] > 0

    def test_timestamps_are_ist(self, clean_records):
        """Section 11 asks for IST timestamps."""
        offset = timedelta(hours=5, minutes=30)
        for record in clean_records:
            detected_at = record.payload["detected_at"]
            assert detected_at.tzinfo is not None
            assert detected_at.utcoffset() == offset

    def test_timestamps_are_in_the_past(self, clean_records):
        for record in clean_records:
            assert record.payload["detected_at"] <= REFERENCE

    def test_payment_methods_cover_upi_nach_and_card(self, clean_records):
        """Section 11: UPI / NACH / card mix."""
        methods = {r.payload["raw_signal"]["payment_method"] for r in clean_records}
        assert "card" in methods
        assert any(m.startswith("upi") for m in methods)
        assert "nach" in methods

    def test_error_codes_use_razorpay_vocabulary(self, clean_records):
        codes = {
            r.payload["raw_signal"].get("gateway_error_code")
            for r in clean_records
            if r.payload["raw_signal"].get("gateway_error_code")
        }
        assert codes
        for code in codes:
            assert code.startswith(("BAD_REQUEST_", "GATEWAY_ERROR_", "SERVER_ERROR_"))

    def test_gateway_defaults_to_the_local_simulator(self, clean_records):
        assert {r.payload["gateway_used"] for r in clean_records} == {
            GatewayUsed.LOCAL_SIMULATION.value
        }

    def test_every_record_carries_a_correlation_id(self, clean_records):
        for record in clean_records:
            assert record.payload["correlation_id"]

    def test_b2b_invoices_are_flagged_and_larger(self, batch):
        """Section 4: B2B receivables are invoice_overdue + channel=b2b."""
        b2b = [
            r
            for r in batch.records
            if not r.has(EdgeCase.MALFORMED)
            and isinstance(r.payload.get("raw_signal"), dict)
            and r.payload["raw_signal"].get("channel") == "b2b"
        ]
        assert b2b, "no B2B receivables generated"
        for record in b2b:
            assert record.payload["type"] == EventType.INVOICE_OVERDUE.value
            assert record.payload["amount"] >= Decimal("50000")

    def test_invoices_carry_due_date_and_days_overdue(self, clean_records):
        invoices = [
            r for r in clean_records if r.payload["type"] == EventType.INVOICE_OVERDUE.value
        ]
        assert invoices
        for record in invoices:
            assert record.payload["raw_signal"]["days_overdue"] >= 1
            assert record.payload["raw_signal"]["due_date"]


# --------------------------------------------------------------------------- #
# Edge-case injection — Section 11
# --------------------------------------------------------------------------- #


class TestEdgeCaseRates:
    """Rates asserted with tolerance: these are probabilities, not quotas."""

    @pytest.mark.parametrize(
        "case,target",
        [
            (EdgeCase.DUPLICATE, RATE_DUPLICATE),
            (EdgeCase.MALFORMED, RATE_MALFORMED),
            (EdgeCase.RESOLVED_EXTERNALLY, RATE_RESOLVED_EXTERNALLY),
            (EdgeCase.HARD_DECLINE, RATE_HARD_DECLINE),
            (EdgeCase.AMBIGUOUS, RATE_AMBIGUOUS),
        ],
    )
    def test_rate_is_near_its_target(self, batch, case, target):
        actual = rate(batch, case)
        assert target * 0.5 <= actual <= target * 1.75, f"{case.value} at {actual:.2%}"

    @pytest.mark.parametrize("case", list(EdgeCase))
    def test_every_edge_case_actually_occurs(self, batch, case):
        """Section 15: "verify edge cases actually trigger"."""
        assert batch.stats["edge_case_counts"][case.value] > 0

    def test_hard_decline_rate_is_scaled_to_eligible_types(self):
        """Only 2 of 5 event types have a Section 6 hard cause, so the
        per-eligible probability must be scaled or the batch rate can never
        reach 10%."""
        eligible_share = len(HARD_DECLINE_CAUSE_BY_EVENT_TYPE) / len(EventType)
        assert RATE_HARD_DECLINE_ON_ELIGIBLE == pytest.approx(
            RATE_HARD_DECLINE / eligible_share
        )

    def test_rates_hold_at_the_smaller_batch_size(self):
        small = generate_batch(50, now=REFERENCE)
        for case in (EdgeCase.DUPLICATE, EdgeCase.RESOLVED_EXTERNALLY):
            assert small.stats["edge_case_counts"][case.value] > 0


class TestEdgeCasesOverlap:
    """Section 11: independent probabilities, NOT mutually exclusive buckets.

    Every assertion here excludes DUPLICATE, because a replay inherits its
    original's flags and would satisfy an overlap check for free. Mutation
    testing caught exactly that: bucketing the real rolls still left
    ``malformed+duplicate`` pairs behind and an earlier, laxer version of these
    tests passed anyway. Overlap has to be proven among independently rolled
    flags or it proves nothing.
    """

    @staticmethod
    def _independent_flags(record) -> set:
        return {c for c in record.edge_cases if c != EdgeCase.DUPLICATE}

    def test_some_records_carry_more_than_one_flag(self, batch):
        assert batch.stats["records_with_multiple_edge_cases"] > 0

    def test_overlap_exists_among_independently_rolled_flags(self, batch):
        multi = [r for r in batch.records if len(self._independent_flags(r)) > 1]
        assert multi, "no record carries 2+ non-duplicate flags — injection is bucketed"

    def test_more_than_one_distinct_combination_occurs(self, batch):
        combos = {
            tuple(sorted(c.value for c in self._independent_flags(r)))
            for r in batch.records
            if len(self._independent_flags(r)) > 1
        }
        assert len(combos) > 1

    def test_malformed_can_coincide_with_another_flag(self, batch):
        overlapping = [
            r
            for r in batch.records
            if r.has(EdgeCase.MALFORMED) and len(self._independent_flags(r)) > 1
        ]
        assert overlapping, "malformed never overlaps — injection is bucketed, not independent"

    def test_resolved_externally_can_coincide_with_an_earlier_rolled_flag(self, batch):
        """Must overlap with a flag decided BEFORE it in generation order.

        Checking against ``malformed`` alone is not enough: malformed is rolled
        after this one, so it would still overlap even if this roll were
        bucketed. Mutation testing caught that loophole. The flags below are all
        decided earlier, so co-occurrence here can only mean the rolls are
        genuinely independent.
        """
        earlier = {EdgeCase.MULTI_EVENT_CUSTOMER, EdgeCase.HARD_DECLINE, EdgeCase.AMBIGUOUS}
        overlapping = [
            r
            for r in batch.records
            if r.has(EdgeCase.RESOLVED_EXTERNALLY) and (self._independent_flags(r) & earlier)
        ]
        assert overlapping, "resolved_externally never overlaps an earlier flag — bucketed"


class TestDuplicates:
    def test_duplicates_replay_an_earlier_source_ref(self, batch):
        refs = Counter(
            r.payload["source_ref"] for r in batch.records if r.payload.get("source_ref")
        )
        assert any(count > 1 for count in refs.values())

    def test_duplicates_are_marked_as_replays(self, batch):
        replays = [
            r
            for r in batch.records
            if r.has(EdgeCase.DUPLICATE) and isinstance(r.payload.get("raw_signal"), dict)
        ]
        assert replays
        assert any(r.payload["raw_signal"].get("replayed") for r in replays)

    def test_a_replay_points_at_its_original(self, batch):
        ids = {r.payload.get("id") for r in batch.records}
        replays = [
            r
            for r in batch.records
            if r.has(EdgeCase.DUPLICATE)
            and isinstance(r.payload.get("raw_signal"), dict)
            and r.payload["raw_signal"].get("replay_of_event_id")
        ]
        assert replays
        for record in replays:
            assert record.payload["raw_signal"]["replay_of_event_id"] in ids


class TestMalformedRecords:
    def test_every_malformed_record_really_is_broken(self, batch):
        """A record flagged malformed that still validates would silently
        weaken the validation test."""
        malformed = [r for r in batch.records if r.has(EdgeCase.MALFORMED)]
        assert malformed
        for record in malformed:
            payload = record.payload
            amount = payload.get("amount")
            broken = (
                any(field not in payload for field in REQUIRED_FIELDS)
                or payload.get("source_ref") is None
                or not isinstance(payload.get("raw_signal"), dict)
                or payload.get("currency") != "INR"
                or not isinstance(amount, Decimal)
                or (isinstance(amount, Decimal) and amount <= 0)
            )
            assert broken, f"{payload.get('id')} flagged malformed but is valid"

    def test_clean_records_are_never_broken(self, clean_records):
        for record in clean_records:
            assert all(field in record.payload for field in REQUIRED_FIELDS)
            assert record.payload["currency"] == "INR"

    def test_several_distinct_malformations_occur(self, batch):
        variants = {
            r.payload.get("_malformation") for r in batch.records if r.has(EdgeCase.MALFORMED)
        }
        assert len(variants) >= 3


class TestAmbiguousRecords:
    def test_ambiguous_records_have_no_ground_truth_label(self, batch):
        """Section 11: they must land in the low-confidence bucket rather than
        being force-classified."""
        ambiguous = [r for r in batch.records if r.has(EdgeCase.AMBIGUOUS)]
        assert ambiguous
        for record in ambiguous:
            assert record.ground_truth_root_cause is None

    def test_ambiguous_records_carry_no_discriminating_error_code(self, batch):
        codes = {
            r.payload["raw_signal"].get("gateway_error_code")
            for r in batch.records
            if r.has(EdgeCase.AMBIGUOUS) and isinstance(r.payload.get("raw_signal"), dict)
        }
        assert codes == {AMBIGUOUS_ERROR_CODE}

    def test_unlabelled_count_matches_the_ambiguous_count(self, batch):
        assert batch.stats["unlabelled_ambiguous_records"] == batch.stats["edge_case_counts"][
            EdgeCase.AMBIGUOUS.value
        ]


class TestHardDeclineRecords:
    def test_hard_decline_records_use_a_section_6_hard_cause(self, batch):
        hard = [r for r in batch.records if r.has(EdgeCase.HARD_DECLINE)]
        assert hard
        for record in hard:
            assert record.ground_truth_root_cause in {
                RootCauseCode.ISSUER_DECLINED,
                RootCauseCode.BANK_REJECTED,
            }

    def test_hard_decline_error_codes_are_recognised_as_hard(self, batch):
        hard = [
            r
            for r in batch.records
            if r.has(EdgeCase.HARD_DECLINE) and isinstance(r.payload.get("raw_signal"), dict)
        ]
        assert hard
        for record in hard:
            assert is_hard_decline(record.payload["raw_signal"]["gateway_error_code"])

    def test_generated_hard_declines_are_never_retried_by_the_gateway(self, batch):
        """The end-to-end gating check: generator output meets gateway policy."""
        from app.gateways.base import RetryRequest

        gateway = LocalSimulationGateway(seed=DEFAULT_SEED)
        executed = 0
        candidates = [
            r
            for r in batch.records
            if r.has(EdgeCase.HARD_DECLINE) and not r.has(EdgeCase.MALFORMED)
        ]
        assert candidates
        for record in candidates:
            payload = record.payload
            response = gateway.initiate_retry(
                RetryRequest(
                    event_id=payload["id"],
                    source_ref=payload["source_ref"],
                    event_type=EventType(payload["type"]),
                    amount=payload["amount"],
                    attempt_number=1,
                    idempotency_key=f"idem_{payload['id']}",
                    failure_reason=payload["raw_signal"].get("gateway_error_code"),
                ),
                now=datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc),
            )
            if response.raw.get("executed"):
                executed += 1
        assert executed == 0


class TestExternallyResolvedRecords:
    def test_they_are_recorded_in_the_world_not_on_the_event(self, batch):
        """Section 9's re-check is only a real test if the engine cannot see the
        answer on the event itself."""
        assert batch.upstream_world
        for record in batch.records:
            raw = record.payload.get("raw_signal")
            if isinstance(raw, dict):
                assert "resolved_externally" not in raw
                assert "upstream_status" not in raw

    def test_the_gateway_reports_them_as_resolved(self, batch):
        gateway = LocalSimulationGateway(seed=DEFAULT_SEED)
        gateway.seed_upstream_state(batch.upstream_world)
        checked = 0
        for record in batch.records:
            if not record.has(EdgeCase.RESOLVED_EXTERNALLY) or record.has(EdgeCase.MALFORMED):
                continue
            payload = record.payload
            result = gateway.check_status(
                payload["source_ref"],
                EventType(payload["type"]),
                now=datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc),
            )
            assert result.is_resolved_externally
            checked += 1
        assert checked > 0

    def test_clean_records_are_not_falsely_reported_resolved(self, batch):
        gateway = LocalSimulationGateway(seed=DEFAULT_SEED)
        gateway.seed_upstream_state(batch.upstream_world)
        for record in batch.records:
            if record.edge_cases or not record.payload.get("source_ref"):
                continue
            result = gateway.check_status(
                record.payload["source_ref"],
                EventType(record.payload["type"]),
                now=datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc),
            )
            assert result.is_resolved_externally is False

    def test_world_uses_valid_upstream_statuses(self, batch):
        for status in batch.upstream_world.values():
            assert UpstreamStatus(status)


class TestMultiEventCustomers:
    def test_some_customers_have_several_events(self, batch):
        """Section 11 asks for a few; these exercise per-customer cooldown and
        do-not-contact scoping."""
        assert batch.stats["multi_event_customer_count"] >= 3
        assert batch.stats["max_events_for_one_customer"] > 1

    def test_every_referenced_customer_has_a_profile(self, clean_records, batch):
        profile_ids = {c["customer_id"] for c in batch.customers}
        for record in clean_records:
            assert record.payload["customer_id"] in profile_ids

    def test_profiles_carry_the_fields_the_engine_reads(self, batch):
        for profile in batch.customers:
            assert 0.0 <= profile["payment_success_rate"] <= 1.0
            assert isinstance(profile["lifetime_value"], Decimal)
            assert isinstance(profile["do_not_contact"], bool)

    def test_some_customers_are_do_not_contact(self, batch):
        assert any(profile["do_not_contact"] for profile in batch.customers)


# --------------------------------------------------------------------------- #
# Label isolation and honest reporting
# --------------------------------------------------------------------------- #


class TestGroundTruthLabelsDoNotLeak:
    def test_no_root_cause_appears_inside_the_event(self, batch):
        """If the label rode along in raw_signal the rule engine could read it
        and every Section 4a accuracy number would be meaningless."""
        for record in batch.records:
            raw = record.payload.get("raw_signal")
            if isinstance(raw, dict):
                assert not any("root_cause" in str(key) for key in raw)

    def test_labels_are_available_on_the_record(self, batch):
        assert batch.stats["labelled_records"] > 0

    def test_labels_are_valid_for_their_event_type(self, batch):
        for record in batch.records:
            cause = record.ground_truth_root_cause
            if cause is None or record.has(EdgeCase.MALFORMED):
                continue
            event_type = EventType(record.payload["type"])
            assert cause in ROOT_CAUSES_BY_EVENT_TYPE[event_type]

    def test_broken_ptp_is_never_an_originating_cause(self, batch):
        """Section 4: it only arises when the promise watcher raises a follow-up
        event, never at detection."""
        for record in batch.records:
            assert record.ground_truth_root_cause != RootCauseCode.BROKEN_PTP


class TestBatchIsNotTriviallyResolvable:
    def test_outcomes_are_mixed_not_all_success(self, batch):
        """Section 11: a 100% resolution rate is a red flag, not a win."""
        from app.gateways.base import RetryRequest

        gateway = LocalSimulationGateway(seed=DEFAULT_SEED)
        gateway.seed_upstream_state(batch.upstream_world)
        outcomes = Counter()
        for record in batch.records:
            payload = record.payload
            if not isinstance(payload.get("raw_signal"), dict) or not payload.get("source_ref"):
                outcomes["skipped_malformed"] += 1
                continue
            event_type = EventType(payload["type"])
            moment = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)
            if gateway.check_status(
                payload["source_ref"], event_type, now=moment
            ).is_resolved_externally:
                outcomes["already_resolved"] += 1
                continue
            response = gateway.initiate_retry(
                RetryRequest(
                    event_id=payload["id"],
                    source_ref=payload["source_ref"],
                    event_type=event_type,
                    amount=Decimal("100.00"),
                    attempt_number=1,
                    idempotency_key=f"batch_{payload['id']}",
                    failure_reason=payload["raw_signal"].get("gateway_error_code"),
                ),
                now=moment,
            )
            outcomes["refused" if response.retry_refused else response.status.value] += 1

        total = sum(outcomes.values())
        assert outcomes["success"] / total < 0.60, "suspiciously high recovery rate"
        assert outcomes["success"] > 0, "nothing recovers at all"
        assert len(outcomes) >= 4, "outcome distribution is too narrow to be realistic"


# --------------------------------------------------------------------------- #
# Reproducibility — Section 11's fixed seed
# --------------------------------------------------------------------------- #


class TestReproducibility:
    def test_same_seed_produces_identical_records(self):
        first = generate_batch(200, seed=42, now=REFERENCE)
        second = generate_batch(200, seed=42, now=REFERENCE)
        for left, right in zip(first.records, second.records):
            assert left.payload == right.payload
            assert left.ground_truth_root_cause == right.ground_truth_root_cause
            assert left.edge_cases == right.edge_cases

    def test_same_seed_produces_an_identical_upstream_world(self):
        assert (
            generate_batch(200, seed=42, now=REFERENCE).upstream_world
            == generate_batch(200, seed=42, now=REFERENCE).upstream_world
        )

    def test_same_seed_produces_identical_stats(self):
        assert (
            generate_batch(200, seed=42, now=REFERENCE).stats
            == generate_batch(200, seed=42, now=REFERENCE).stats
        )

    def test_a_different_seed_produces_different_data(self):
        assert (
            generate_batch(200, seed=42, now=REFERENCE).records[0].payload
            != generate_batch(200, seed=43, now=REFERENCE).records[0].payload
        )

    def test_customer_profiles_are_stable_across_runs(self):
        first = {c["customer_id"]: c for c in generate_batch(200, seed=42, now=REFERENCE).customers}
        second = {
            c["customer_id"]: c for c in generate_batch(200, seed=42, now=REFERENCE).customers
        }
        assert first == second

    def test_the_gateway_replays_a_batch_identically(self):
        """Generator and gateway are reproducible together, not just apart."""
        from app.gateways.base import RetryRequest

        def run() -> list[tuple]:
            batch = generate_batch(200, seed=42, now=REFERENCE)
            gateway = LocalSimulationGateway(seed=42)
            gateway.seed_upstream_state(batch.upstream_world)
            results = []
            for record in batch.records:
                payload = record.payload
                if not isinstance(payload.get("raw_signal"), dict) or not payload.get(
                    "source_ref"
                ):
                    continue
                response = gateway.initiate_retry(
                    RetryRequest(
                        event_id=payload["id"],
                        source_ref=payload["source_ref"],
                        event_type=EventType(payload["type"]),
                        amount=Decimal("100.00"),
                        attempt_number=1,
                        idempotency_key=f"r_{payload['id']}",
                        failure_reason=payload["raw_signal"].get("gateway_error_code"),
                    ),
                    now=datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc),
                )
                results.append((response.status.value, response.provider_ref, response.retry_refused))
            return results

        assert run() == run()
