"""Operational logging: JSON lines on stdout, every record carrying its client id when it has one.

This is separate from the business audit trail, which the Engine writes to the Sheet.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        client_id = getattr(record, "client_id", None)
        if client_id:
            entry["client_id"] = client_id
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


class ClientLogger(logging.LoggerAdapter):
    """A logger that stamps every record with the client it is about."""

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        extra = dict(kwargs.get("extra") or {})
        extra["client_id"] = (self.extra or {}).get("client_id")
        kwargs["extra"] = extra
        return msg, kwargs


def client_logger(name: str, client_id: str) -> ClientLogger:
    return ClientLogger(logging.getLogger(name), {"client_id": client_id})
