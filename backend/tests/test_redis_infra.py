"""Optional Redis coordination. BUILD_SPEC Section 12.

    cd backend && PYTHONPATH=. pytest -q tests/test_redis_infra.py

Two halves. The first runs against a REAL Redis when one is reachable and is
skipped when it is not — a locking test that silently passes without a server
would be worthless. The second half asserts the behaviour that matters far
more: with no Redis at all, nothing breaks and no correctness is lost.
"""

from __future__ import annotations

import os

import pytest

from app.infra import redis_client


def redis_reachable() -> bool:
    redis_client.reset_for_tests()
    os.environ.pop(redis_client.ENV_ENABLED, None)
    return redis_client.is_available()


needs_redis = pytest.mark.skipif(
    not redis_reachable(), reason="no Redis server reachable"
)


@pytest.fixture(autouse=True)
def _clean_state():
    redis_client.reset_for_tests()
    yield
    os.environ.pop(redis_client.ENV_ENABLED, None)
    redis_client.reset_for_tests()


class TestWithoutRedis:
    """The configuration that must never lose correctness."""

    def test_absence_is_a_supported_configuration(self):
        os.environ[redis_client.ENV_ENABLED] = "0"
        redis_client.reset_for_tests()
        assert redis_client.is_available() is False

    def test_a_lock_is_granted_when_redis_is_absent(self):
        """Failing closed would turn a cache outage into a recovery outage."""
        os.environ[redis_client.ENV_ENABLED] = "0"
        redis_client.reset_for_tests()
        with redis_client.case_lock("evt_1") as held:
            assert held is True

    def test_two_callers_both_proceed_when_redis_is_absent(self):
        """Correctness then rests on the database, exactly as it always has."""
        os.environ[redis_client.ENV_ENABLED] = "0"
        redis_client.reset_for_tests()
        with redis_client.case_lock("evt_same") as first:
            with redis_client.case_lock("evt_same") as second:
                assert first is True
                assert second is True

    def test_idempotency_defers_to_the_database_when_absent(self):
        os.environ[redis_client.ENV_ENABLED] = "0"
        redis_client.reset_for_tests()
        assert redis_client.mark_done("op_1") is True
        assert redis_client.mark_done("op_1") is True
        assert redis_client.already_done("op_1") is False

    def test_an_unreachable_server_does_not_raise(self):
        os.environ[redis_client.ENV_ENABLED] = "1"
        os.environ[redis_client.ENV_URL] = "redis://127.0.0.1:59999/0"
        redis_client.reset_for_tests()
        try:
            assert redis_client.is_available() is False
            with redis_client.case_lock("evt_x") as held:
                assert held is True
        finally:
            os.environ.pop(redis_client.ENV_URL, None)


@needs_redis
class TestWithRealRedis:
    """Exercised against an actual server, or skipped."""

    def test_a_lock_is_exclusive(self):
        with redis_client.case_lock("evt_exclusive") as first:
            assert first is True
            with redis_client.case_lock("evt_exclusive") as second:
                assert second is False, "two workers took the same case lock"

    def test_a_lock_is_released_afterwards(self):
        with redis_client.case_lock("evt_release") as held:
            assert held is True
        with redis_client.case_lock("evt_release") as again:
            assert again is True

    def test_different_cases_do_not_block_each_other(self):
        with redis_client.case_lock("evt_a") as a:
            with redis_client.case_lock("evt_b") as b:
                assert a is True and b is True

    def test_a_caller_cannot_release_someone_elses_lock(self):
        """A lock that expired and was re-taken must not be deleted by its
        original holder — that would hand the case to a third worker."""
        client = redis_client._resolve_client()
        client.set("revora:lock:evt_stolen", "someone-elses-token", ex=30)
        with redis_client.case_lock("evt_stolen") as held:
            assert held is False
        assert client.get("revora:lock:evt_stolen") == "someone-elses-token"
        client.delete("revora:lock:evt_stolen")

    def test_idempotency_marks_only_once(self):
        key = "op_once"
        redis_client._resolve_client().delete(f"revora:done:{key}")
        assert redis_client.mark_done(key) is True
        assert redis_client.mark_done(key) is False
        assert redis_client.already_done(key) is True
        redis_client._resolve_client().delete(f"revora:done:{key}")


class TestNoFinancialStateInRedis:
    def test_the_module_never_stores_money(self):
        """A ledger cannot be built on a cache."""
        import inspect

        source = inspect.getsource(redis_client)
        for forbidden in ("amount", "Decimal", "recovered", "ledger", "outcome"):
            assert f"{forbidden} =" not in source
            assert f"set(f\"revora:{forbidden}" not in source

    def test_only_coordination_keys_are_used(self):
        """Exactly two namespaces, both about coordination.

        Asserted as a SET of prefixes rather than a count of occurrences: a
        count breaks the moment someone adds a comment, which teaches people to
        edit the number instead of thinking about what changed.
        """
        import inspect
        import re

        source = inspect.getsource(redis_client)
        namespaces = set(re.findall(r'revora:([a-z_]+):', source))
        assert namespaces == {"lock", "done"}, namespaces
