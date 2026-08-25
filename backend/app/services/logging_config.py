"""Structured JSON logging + correlation IDs. BUILD_SPEC Sections 1 and 12.

Every log line is a single JSON object, so a batch run can be grepped, filtered
by correlation id, and read by a machine. No print statements anywhere in the
application.

Correlation IDs
---------------
The id lives in a :class:`~contextvars.ContextVar`, set once by the middleware in
main.py and read automatically by the formatter. Call sites therefore do not have
to thread an id through every function signature — they log normally and the id
is attached. ContextVar (not a global) is what makes this correct under
concurrent requests: each request gets its own value.

The chain a correlation id must join up is:

    batch -> event -> decision -> execution -> audit -> exception/log

Batch runs set a batch-level id and each record carries its own event
correlation id, with the batch id recorded alongside, so a judge can filter
either way.

Secrets
-------
Section 3 keeps secrets in ``.env``. This module makes sure they cannot reach a
log line even by accident: any field whose NAME looks secret is replaced with
``"[REDACTED]"``, and any string VALUE that looks like a Razorpay key is
redacted regardless of the field it arrived under. Both directions matter — the
first catches ``{"api_key": ...}``, the second catches someone logging a whole
settings object or an exception message with a key embedded in it.
"""

from __future__ import annotations

import contextvars
import datetime as _datetime
import json
import logging
import re
import sys
import traceback
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

#: Correlation id for the current request / batch record.
_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "revora_correlation_id", default=None
)

#: Field names that must never have their value logged.
SENSITIVE_FIELD_PATTERN = re.compile(
    r"(secret|password|passwd|token|api_?key|authorization|auth|credential|"
    r"private_?key|razorpay_key)",
    re.IGNORECASE,
)

#: Value shapes that are secrets wherever they appear. Razorpay test keys are
#: prefixed rzp_test_ / rzp_live_; the live pattern is included so a
#: misconfigured production key is redacted too rather than printed.
SENSITIVE_VALUE_PATTERN = re.compile(r"rzp_(test|live)_[A-Za-z0-9]+")

REDACTED = "[REDACTED]"

#: LogRecord attributes that are standard library noise rather than context.
_STANDARD_RECORD_FIELDS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)


def new_correlation_id(prefix: str = "corr") -> str:
    """Generate a fresh correlation id."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def set_correlation_id(correlation_id: str | None) -> contextvars.Token:
    """Set the correlation id for the current context."""
    return _correlation_id.set(correlation_id)


def get_correlation_id() -> str | None:
    """Correlation id for the current context, if one is set."""
    return _correlation_id.get()


def reset_correlation_id(token: contextvars.Token) -> None:
    """Restore the previous correlation id."""
    _correlation_id.reset(token)


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Iterator[str]:
    """Bind a correlation id for the duration of a block.

    Used per batch record so each event's logs carry that event's id, and the
    previous value is restored afterwards even if the record raises.
    """
    resolved = correlation_id or new_correlation_id()
    token = set_correlation_id(resolved)
    try:
        yield resolved
    finally:
        reset_correlation_id(token)


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively strip secrets from a value before it is logged.

    Redacts on field NAME and on value SHAPE. Depth is bounded so a
    self-referential structure cannot hang the logger — logging must never be
    the thing that takes the process down.
    """
    if _depth > 6:
        return "[TRUNCATED]"

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if SENSITIVE_FIELD_PATTERN.search(str(key)):
                redacted[str(key)] = REDACTED
            else:
                redacted[str(key)] = redact(item, _depth + 1)
        return redacted

    if isinstance(value, (list, tuple, set)):
        return [redact(item, _depth + 1) for item in value]

    if isinstance(value, str):
        return SENSITIVE_VALUE_PATTERN.sub(REDACTED, value)

    return value


def _json_safe(value: Any, _depth: int = 0) -> Any:
    """Coerce a value into something json.dumps can handle."""
    if _depth > 6:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, _depth + 1) for item in value]
    if isinstance(value, _datetime.datetime):
        return value.isoformat()
    return str(value)


class JSONFormatter(logging.Formatter):
    """Renders each record as one JSON object on one line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _datetime.datetime.fromtimestamp(
                record.created, tz=_datetime.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        correlation_id = getattr(record, "correlation_id", None) or get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id

        # Anything passed via logger.info(..., extra={...}) becomes structured
        # context rather than being interpolated into the message string.
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = _json_safe(value)

        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            payload["error"] = {
                "type": exc_type.__name__ if exc_type else None,
                "message": str(exc_value) if exc_value else None,
                # Kept structured: a traceback is genuinely useful when a batch
                # record fails, and Section 9 requires failures not be swallowed.
                "traceback": traceback.format_exception(exc_type, exc_value, exc_tb),
            }

        return json.dumps(redact(payload), default=str, ensure_ascii=False)


def configure_logging(level: int | str = logging.INFO, stream=None) -> logging.Logger:
    """Install the JSON formatter on the root logger.

    Idempotent: repeated calls replace the handler rather than stacking them, so
    a test that reconfigures logging does not produce duplicate lines.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    return root


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    event_id: str | None = None,
    stage: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    correlation_id: str | None = None,
    exc_info: bool = False,
    **context: Any,
) -> None:
    """Emit a structured pipeline log line.

    The named arguments are the fields a judge needs to follow one event through
    the pipeline; anything else lands in the same JSON object as extra context.
    """
    extra: dict[str, Any] = {k: v for k, v in context.items() if k not in _STANDARD_RECORD_FIELDS}
    if event_id is not None:
        extra["event_id"] = event_id
    if stage is not None:
        extra["stage"] = stage
    if action is not None:
        extra["action"] = action
    if outcome is not None:
        extra["outcome"] = outcome
    if correlation_id is not None:
        extra["correlation_id"] = correlation_id
    logger.log(level, message, extra=extra, exc_info=exc_info)
