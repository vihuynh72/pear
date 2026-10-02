"""Structured logging that Cloud Logging understands, readable locally.

- JSON (Cloud Run): one JSON object per line with `severity`, `message`, request_id and the
  `logging.googleapis.com/trace` field so logs group under the request's trace.
- pretty (local): `12:01:02 INFO  [req=ab12] scan.barcode key=off:123 total_ms=412`.

Use `log_event("scan.done", product_key=..., timings_ms=...)` for anything you might want to
filter or chart later; extra kwargs become jsonPayload fields (log-based metrics read them).
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
trace_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace", default=None)

_PROJECT = "pear-510410"
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        trace = trace_var.get()
        if trace:
            payload["logging.googleapis.com/trace"] = f"projects/{_PROJECT}/traces/{trace}"
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class _PrettyFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        rid = request_id_var.get()
        extras = " ".join(
            f"{k}={json.dumps(v, default=str, ensure_ascii=False) if not isinstance(v, str) else v}"
            for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_")
        )
        line = f"{ts} {record.levelname:<5} {('[req=' + rid[:8] + '] ') if rid else ''}{record.name}: {record.getMessage()}"
        if extras:
            line += "  " + extras
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(fmt: str = "pretty", level: str = "INFO", project: str | None = None) -> None:
    global _PROJECT
    if project:
        _PROJECT = project
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter() if fmt == "json" else _PrettyFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "google.auth", "urllib3", "grpc", "opentelemetry"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # uvicorn's access log duplicates our request summary line.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def log_event(event: str, level: int = logging.INFO, logger: str = "pear", **fields: Any) -> None:
    """Emit one structured event line. `event` becomes the message and an `event` field.

    Field names that collide with LogRecord attributes (message, msg, args, name, ...) get a
    trailing underscore instead of crashing the logging call.
    """
    safe = {(f"{k}_" if k in _RESERVED else k): v for k, v in fields.items()}
    logging.getLogger(logger).log(level, event, extra={"event": event, **safe})
