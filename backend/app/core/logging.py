"""JSON logging for the backend package."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from threading import Lock
from typing import TYPE_CHECKING
from urllib.parse import unquote

from pydantic import PostgresDsn

from backend.app.core.request_context import get_request_id, is_valid_request_id

if TYPE_CHECKING:
    from backend.app.core.settings import Settings

_REDACTED = "[REDACTED]"
_POSTGRES_DSN = re.compile(r"\bpostgres(?:ql)?(?:\+[\w]+)?://[^\s\"'<>]+", re.IGNORECASE)
_AUTH_VALUE = re.compile(r"\bBearer[ \t]+[A-Za-z0-9._~+/=:-]+", re.IGNORECASE)
_API_TOKEN = re.compile(r"\b(?:sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{12,}\b")
_CREDENTIAL = re.compile(
    r"""(?ix)
    (?P<label>\b(?:authorization|proxy[-_]authorization|database[-_]url|dsn|
    (?:x[-_])?api[-_ ]key|(?:dev[-_]auth[-_]|access[-_]|refresh[-_]|auth[-_])?token|
    password|passwd|pwd|(?:cursor[-_])?signing[-_]key|client[-_]secret|secret)\b['"]?\s*[:=]\s*)
    (?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\[REDACTED\]|Basic[ \t]+[^\s,;}\]]+|[^\s,;}\]]+)
    """
)
_PRIVATE_PAYLOAD = re.compile(
    r"""\b(?:request[-_ ]body|model[-_ ]input|model[-_ ]output)\b['"]?\s*[:=].*""",
    re.IGNORECASE | re.DOTALL,
)
_configuration_lock = Lock()


def _configured_secrets(settings: "Settings | None") -> tuple[str, ...]:
    if settings is None:
        return ()

    dsn = settings.database_url.get_secret_value()
    secrets = {dsn, settings.cursor_signing_key.get_secret_value()}
    if settings.dev_auth_token is not None:
        secrets.add(settings.dev_auth_token.get_secret_value())
    for host in PostgresDsn(dsn).hosts():
        if password := host["password"]:
            secrets.update((password, unquote(password)))
    return tuple(sorted(secrets, key=len, reverse=True))


class _JsonFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...]) -> None:
        super().__init__()
        self._secrets = secrets

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, _REDACTED)
        text = _PRIVATE_PAYLOAD.sub(_REDACTED, text)
        text = _POSTGRES_DSN.sub(_REDACTED, text)
        text = _AUTH_VALUE.sub(_REDACTED, text)
        text = _API_TOKEN.sub(_REDACTED, text)
        return _CREDENTIAL.sub(lambda match: match["label"] + _REDACTED, text)

    def format(self, record: logging.LogRecord) -> str:
        error_type = record.exc_info[0] if record.exc_info else None
        if error_type is not None:
            message = "An exception occurred"
        elif isinstance(record.msg, str):
            try:
                message = self._redact(record.getMessage())
            except Exception:
                # StreamHandler error diagnostics can expose record.msg and record.args.
                message = "Log message could not be formatted"
        else:
            message = "Non-text log message omitted"

        request_id = get_request_id()
        payload: dict[str, str | int | None] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": self._redact(record.name),
            "message": message,
            "request_id": self._redact(request_id) if request_id is not None else None,
        }
        for key in ("run_id", "item_id"):
            value = getattr(record, key, None)
            if is_valid_request_id(value):
                payload[key] = self._redact(value)
        attempt_no = getattr(record, "attempt_no", None)
        if type(attempt_no) is int and attempt_no > 0:
            payload["attempt_no"] = attempt_no
        if error_type is not None:
            payload["error_type"] = self._redact(error_type.__name__)
        return json.dumps(payload, ensure_ascii=False)


class _JsonStreamHandler(logging.StreamHandler):
    """Identify the JSON handler owned by this module."""

    def handleError(self, record: logging.LogRecord) -> None:
        # The default implementation dumps unprocessed arguments to stderr.
        sys.stderr.write("Failed to emit backend log record\n")


def configure_logging(level: str | None = None, *, settings: "Settings | None" = None) -> None:
    """Configure the backend logger while preserving the host's root logging setup."""
    if level is None:
        level = settings.log_level if settings is not None else "INFO"
    logger = logging.getLogger("backend")
    with _configuration_lock:
        existing = next(
            (handler for handler in logger.handlers if isinstance(handler, _JsonStreamHandler)),
            None,
        )
        handler = existing if existing is not None else _JsonStreamHandler(sys.stdout)
        handler.setLevel(level)
        if settings is not None or handler.formatter is None:
            handler.setFormatter(_JsonFormatter(_configured_secrets(settings)))
        if existing is None:
            logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
