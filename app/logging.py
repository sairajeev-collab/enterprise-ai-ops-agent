"""Structured JSON logging.

We emit one JSON object per line so logs are machine-parseable by any aggregator
(Fly, Loki, CloudWatch) without a shipping agent. A contextvar carries the
per-request/per-run correlation id so every line inside a unit of work can be
grouped, which is the single most useful thing when debugging a distributed
pipeline. Kept dependency-free on purpose — stdlib logging is enough here.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import sys
from typing import Any

# Correlation id for the in-flight request or job. Set by middleware/worker.
correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

# Reserved LogRecord attributes we never want to duplicate into the JSON payload.
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        cid = correlation_id.get()
        if cid:
            payload["correlation_id"] = cid

        # Promote structured extras passed via logger.info(..., extra={...}).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger.

    Idempotent: replaces existing handlers so repeated calls (tests, reload)
    don't stack duplicate output.
    """

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn access logs are noisy and redundant with our request middleware.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
