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

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import init_db
from app.routers import audit as audit_router
from app.routers import batch as batch_router
from app.routers import events as events_router
from app.routers import exceptions as exceptions_router
from app.routers import policies as policies_router
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

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create tables and log readiness.

    A lifespan handler rather than the deprecated ``on_event("startup")``.
    Settings are read but never logged: the Razorpay keys live there, and
    Section 3 keeps secrets out of everything but ``.env``.
    """
    init_db()
    settings = get_settings()
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
app.include_router(scripts_router.router)
