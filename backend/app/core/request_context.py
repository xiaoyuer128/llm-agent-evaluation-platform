"""Request identifiers scoped to the current execution context."""

import re
from contextvars import ContextVar, Token
from typing import TypeGuard
from uuid import uuid4

_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def is_valid_request_id(value: object) -> TypeGuard[str]:
    """Check whether an external identifier is safe to propagate."""
    return isinstance(value, str) and _REQUEST_ID_PATTERN.fullmatch(value) is not None


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None = None) -> Token[str | None]:
    """Bind a valid identifier or generate a UUID. Reset the returned token in finally."""
    request_id = value if is_valid_request_id(value) else str(uuid4())
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)
