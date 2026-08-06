"""Application logging configuration."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


_HANDLER_MARKER = "_azure_ipranges_api_json_handler"
_RESERVED_FIELDS = frozenset(
    logging.LogRecord(
        name="",
        level=0,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__
) | {"asctime", "message"}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_FIELDS and not key.startswith("_")
        }
        payload.update(
            {
                "timestamp": datetime.fromtimestamp(record.created, UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )

        if record.exc_info:
            exception_type = record.exc_info[0]
            exception_value = record.exc_info[1]
            payload["exception"] = {
                "type": (
                    exception_type.__name__
                    if exception_type is not None
                    else type(exception_value).__name__
                ),
                "message": str(exception_value),
                "traceback": self.formatException(record.exc_info),
            }
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def _parse_log_level(log_level: str) -> int:
    normalized_level = log_level.strip().upper()
    level = logging.getLevelNamesMapping().get(normalized_level)
    if level is None:
        valid_levels = ", ".join(
            name
            for name, value in logging.getLevelNamesMapping().items()
            if isinstance(value, int)
        )
        raise ValueError(
            f"Invalid log level {log_level!r}. Expected one of: {valid_levels}"
        )
    return level


def configure_logging(log_level: str) -> None:
    """Configure reusable, structured application logging to standard output."""
    level = _parse_log_level(log_level)
    root_logger = logging.getLogger()

    configured_handlers = [
        handler
        for handler in root_logger.handlers
        if getattr(handler, _HANDLER_MARKER, False)
    ]
    if configured_handlers:
        handler = configured_handlers[0]
        for duplicate in configured_handlers[1:]:
            root_logger.removeHandler(duplicate)
            duplicate.close()
        handler.stream = sys.stdout
    else:
        handler = logging.StreamHandler(sys.stdout)
        setattr(handler, _HANDLER_MARKER, True)
        root_logger.addHandler(handler)

    handler.setLevel(level)
    handler.setFormatter(_JsonFormatter())
    root_logger.setLevel(level)

    for logger_name in ("httpcore", "httpx", "uvicorn.access"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
