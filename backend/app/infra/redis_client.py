"""Optional Redis, for coordination only.

WHAT THIS IS FOR
----------------
One thing: stopping two workers from acting on the same case at the same moment.
Revora already has database-level idempotency, and that remains the guarantee.
This adds a cheap distributed lock in front of it so that when several
processes are running the autonomous loop, they do not all pick up the same
event, do the same work and race each other to the same row.

WHAT THIS IS NOT FOR
--------------------
No financial state lives here. Not an amount, not an outcome, not a promise.
Redis is a cache with a persistence story that ranges from "good" to "you lost
the last second of writes", and a ledger cannot be built on that. Every rupee
stays in the database, and every question about money is answered from the
database.

The practical consequence is that losing Redis entirely costs correctness
nothing. A lock that cannot be taken is treated as a lock that was granted:
the work proceeds and the database's own idempotency catches any duplicate,
exactly as it does today with no Redis at all. Failing closed would be worse —
recovery would stop for every merchant because a cache went down.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("revora.redis")

#: Set false to run without Redis. Absence is a supported configuration, not a
#: degraded one — local development and the test suite both run this way.
ENV_ENABLED = "REVORA_REDIS_ENABLED"
ENV_URL = "REVORA_REDIS_URL"

DEFAULT_URL = "redis://localhost:6379/0"

#: How long a lock survives if its holder dies mid-work. Long enough for a
#: recovery pass, short enough that a crash does not wedge a case for an hour.
LOCK_TTL_SECONDS = 60

#: How long a completed operation is remembered for idempotency purposes.
IDEMPOTENCY_TTL_SECONDS = 3600

_client: Any | None = None
_checked = False


def _resolve_client() -> Any | None:
    """Connect once, or decide we are running without Redis.

    The result is cached — including the decision NOT to use Redis — so a
    missing server costs one failed connection at startup rather than a timeout
    on every call for the lifetime of the process.
    """
    global _client, _checked
    if _checked:
        return _client
    _checked = True

    if os.environ.get(ENV_ENABLED, "1").lower() in ("0", "false", "no"):
        logger.info("redis_disabled_by_configuration")
        _client = None
        return None

    try:
        import redis  # imported lazily so the dependency stays optional

        candidate = redis.Redis.from_url(
            os.environ.get(ENV_URL, DEFAULT_URL),
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            decode_responses=True,
        )
        candidate.ping()
        _client = candidate
        logger.info("redis_connected")
    except Exception:  # noqa: BLE001
        # Not an error. Running without Redis is supported, and saying so at
        # INFO avoids a stack trace in every local development log.
        logger.info("redis_unavailable_continuing_without_it")
        _client = None
    return _client


def reset_for_tests() -> None:
    """Forget the cached connection. Used by tests that toggle configuration."""
    global _client, _checked
    _client = None
    _checked = False


def is_available() -> bool:
    """Whether a working Redis is present. Callers must work either way."""
    return _resolve_client() is not None


@contextmanager
def case_lock(key: str, *, ttl: int = LOCK_TTL_SECONDS) -> Iterator[bool]:
    """Hold a short lock on one case while it is being worked.

    Yields True when this caller holds the lock, False when someone else does.

    Yields True when Redis is unavailable. That is deliberate: the lock is an
    optimisation that avoids duplicated effort, not the thing that prevents
    duplicate recovery. The database prevents that. Failing closed here would
    convert a cache outage into a total recovery outage.

    Released with a token check so a caller whose lock has already expired
    cannot delete a lock that a different worker has since acquired.
    """
    client = _resolve_client()
    if client is None:
        yield True
        return

    token = uuid.uuid4().hex
    acquired = False
    try:
        acquired = bool(client.set(f"revora:lock:{key}", token, nx=True, ex=ttl))
        yield acquired
    except Exception:  # noqa: BLE001
        logger.warning("redis_lock_failed_proceeding_without_it", extra={"key": key})
        yield True
        return
    finally:
        if acquired:
            try:
                # Compare-and-delete: only release a lock we still own.
                script = (
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('del', KEYS[1]) else return 0 end"
                )
                client.eval(script, 1, f"revora:lock:{key}", token)
            except Exception:  # noqa: BLE001
                logger.warning("redis_lock_release_failed", extra={"key": key})


def mark_done(key: str, *, ttl: int = IDEMPOTENCY_TTL_SECONDS) -> bool:
    """Record that an operation completed. True if this is the FIRST time.

    A fast pre-check that lets an obvious duplicate be dropped without touching
    the database. It is not the idempotency guarantee — the database still is —
    so when Redis is absent this returns True and the work proceeds to the
    checks that actually enforce correctness.
    """
    client = _resolve_client()
    if client is None:
        return True
    try:
        return bool(client.set(f"revora:done:{key}", "1", nx=True, ex=ttl))
    except Exception:  # noqa: BLE001
        logger.warning("redis_idempotency_check_failed", extra={"key": key})
        return True


def already_done(key: str) -> bool:
    """Whether ``key`` was recently marked done. False when Redis is absent."""
    client = _resolve_client()
    if client is None:
        return False
    try:
        return bool(client.exists(f"revora:done:{key}"))
    except Exception:  # noqa: BLE001
        return False
