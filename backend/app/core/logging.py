"""Structured (JSON lines) logging. Never log secrets."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

# Google API keys (AIza..., AQ....), OAuth bearer tokens and key=/token= query params.
_SECRET_RE = re.compile(
    r"AIza[0-9A-Za-z_\-]{20,}"
    r"|\bAQ\.[0-9A-Za-z_\-]{20,}"
    r"|\bya29\.[0-9A-Za-z_\-.]{20,}"
    r"|(?i:bearer)\s+[0-9A-Za-z_\-.=]{20,}"
    r"|(?i:(?:api_?key|key|access_token|token)=)[^&\s\"']+"
)


def redact_secrets(text: str) -> str:
    """Mask credential-shaped substrings before text is logged or written to a report."""
    return _SECRET_RE.sub("[REDACTED]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # The SDK and HTTP client are chatty at INFO and may log request URLs.
    for noisy in ("httpx", "httpcore", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
