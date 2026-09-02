"""FastAPI application. BUILD_SPEC Section 12.

    "main.py — FastAPI app, mounts all routers, correlation-id middleware"

Routers are mounted as each session builds them. Session 4 mounts batch,
exceptions and audit; events, policies, scripts and ml arrive in later sessions
and are deliberately absent rather than stubbed, so the OpenAPI schema always
describes what actually exists.

The correlation-id middleware is the one piece of cross-cutting infrastructure
here. It accepts an inbound ``X-Correlation-ID`` so a caller can tie a request to
its own tracing, generates one otherwise, binds it for the duration of the
request so every log line picks it up automatically, and echoes it back on the
response. That is the whole tracing story — Section 1 asks for correlation IDs,
not a distributed tracing system.
"""

from __future__ import annotations

import logging

import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import init_db, utcnow
from app.routers import audit as audit_router
from app.routers import batch as batch_router
from app.routers import events as events_router
from app.routers import communications as communications_router
from app.routers import notifications as notifications_router
from app.routers import reports as reports_router
from app.routers import exceptions as exceptions_router
from app.routers import policies as policies_router
from app.routers import promises as promises_router
from app.routers import scripts as scripts_router
from app.services.logging_config import (
    configure_logging,
    correlation_scope,
    log_event,
    new_correlation_id,
)

CORRELATION_HEADER = "X-Correlation-ID"

configure_logging()
logger = logging.getLogger("revora.api")

def _start_autonomous_recovery(settings) -> "asyncio.Task | None":
    """Begin processing payment events without anyone asking.

    Recovery is what Revora does; requiring a merchant to press a button for it
    to happen made the product look like a report generator with a refresh
    control.

    Disabled under pytest. A background task writing to the database while a
    test asserts against it would make the whole suite non-deterministic, and a
    flaky suite is worse than no suite.
    """
    if not getattr(settings, "autonomous_recovery", False):
        return None
    if "PYTEST_CURRENT_TEST" in os.environ:
        return None
    return asyncio.create_task(_autonomous_recovery_loop(settings))


async def _autonomous_recovery_loop(settings) -> None:
    """Take on a few events, wait, repeat.

    Each pass goes through the REAL pipeline — detect, diagnose, decide, policy,
    execute, verify, ledger, audit. Nothing here writes a metric directly, and
    nothing bypasses a policy gate: this only decides WHEN the engine runs,
    never what it concludes.

    Failures are logged with a traceback and the loop continues. One bad pass
    should not silently stop recovery for the rest of the session, and it must
    not take the API down with it.
    """
    from app.database import SessionLocal
    from app.routers.batch import build_gateway, run_batch, verify_pending_cases
    from app.schemas.batch import BatchRequest

    interval = max(2, int(settings.autonomous_interval_seconds))
    size = max(1, int(settings.autonomous_batch_size))

    # ONE gateway for the life of the process.
    #
    # The simulator holds upstream state — notably a subscription's pending
    # auto-retry — on the instance. Building a fresh gateway each pass threw
    # that away, so a case waiting on the provider could never be resolved by a
    # later check. A single Razorpay is also the more faithful model.
    gateway = build_gateway(settings.default_gateway)

    # A short grace period so the first request after startup is not competing
    # with a batch for the database.
    await asyncio.sleep(interval)

    while True:
        try:
            def _pass() -> int:
                session = SessionLocal()
                try:
                    # Look again at cases waiting on the provider BEFORE taking
                    # on new ones. Money that has already arrived should be
                    # recorded before Revora goes looking for more work.
                    verify_pending_cases(session, gateway, now=utcnow())
                    return run_batch(
                        session, BatchRequest(count=size), gateway=gateway
                    ).processed
                finally:
                    session.close()

            # run_batch is synchronous and does real work; off the event loop it
            # would block every request for its duration.
            processed = await asyncio.to_thread(_pass)
            log_event(
                logger,
                logging.INFO,
                "autonomous_recovery_pass",
                stage="batch",
                action="autonomous_pass",
                outcome="ok",
                processed=processed,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "autonomous_recovery_pass_failed",
                extra={"stage": "batch", "action": "autonomous_pass", "outcome": "error"},
            )
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create tables and log readiness.

    A lifespan handler rather than the deprecated ``on_event("startup")``.
    Settings are read but never logged: the Razorpay keys live there, and
    Section 3 keeps secrets out of everything but ``.env``.
    """
    init_db()
    settings = get_settings()
    worker = _start_autonomous_recovery(settings)
    log_event(
        logger,
        logging.INFO,
        "application_started",
        stage="api",
        action="startup",
        outcome="ready",
        default_gateway=settings.default_gateway.value,
        # Whether keys exist, never what they are.
        razorpay_configured=settings.razorpay_configured,
    )
    yield

    if worker is not None:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(
    lifespan=lifespan,
    title="Revora",
    description=(
        "Revenue recovery that reasons, decides and stops. "
        "Razorpay Buildathon — Track 03."
    ),
    version="0.4.0",
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Bind a correlation id for the request and echo it back.

    Every log line emitted while handling the request inherits this id via the
    ContextVar in logging_config, so nothing downstream has to pass it along.
    """
    correlation_id = request.headers.get(CORRELATION_HEADER) or new_correlation_id("req")
    with correlation_scope(correlation_id):
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - log, then let FastAPI's handler run
            log_event(
                logger,
                logging.ERROR,
                "unhandled_request_error",
                stage="api",
                action=f"{request.method} {request.url.path}",
                outcome="error",
                exc_info=True,
            )
            raise
        response.headers[CORRELATION_HEADER] = correlation_id
        return response


@app.get("/health", tags=["meta"])
def health() -> JSONResponse:
    """Liveness probe."""
    return JSONResponse({"status": "ok", "version": app.version})


app.include_router(events_router.router)
app.include_router(batch_router.router)
app.include_router(exceptions_router.router)
app.include_router(audit_router.router)
app.include_router(policies_router.router)
app.include_router(promises_router.router)
app.include_router(communications_router.router)
app.include_router(notifications_router.router)
app.include_router(reports_router.router)
app.include_router(scripts_router.router)
