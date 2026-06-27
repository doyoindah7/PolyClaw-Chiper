"""Structured logging config — JSON or text format."""
from __future__ import annotations

import logging
import os
import sys


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure logging.

    Args:
        level: log level (DEBUG/INFO/WARNING/ERROR)
        fmt: "json" for structured JSON logs, "text" for human-readable
    """
    level_num = getattr(logging, level.upper(), logging.INFO)

    if fmt == "json":
        # Simple JSON formatter (avoid structlog dependency at module level for simplicity)
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level_num)

    # Reduce httpx noise
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


class JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter."""

    # Reserved LogRecord attributes that cannot be overwritten via extra={}
    RESERVED = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        import json
        import time

        log_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Add any non-reserved extra attributes
        for key, value in record.__dict__.items():
            if key not in self.RESERVED and not key.startswith("_"):
                log_entry[key] = value
        return json.dumps(log_entry, default=str)
