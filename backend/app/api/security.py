"""Opt-in development Bearer authentication and role dependencies."""

import hmac
from typing import Annotated, Literal

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException

from backend.app.api.errors import InvalidTokenError
from backend.app.core.settings import Settings

type Role = Literal["viewer", "operator", "admin"]

bearer_scheme = HTTPBearer(scheme_name="BearerAuth", auto_error=False)


def get_app_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503)
    return settings


def get_current_role(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Role:
    authorization_headers = request.headers.getlist("authorization")
    if not authorization_headers:
        raise HTTPException(status_code=401)

    token = settings.dev_auth_token
    role = settings.dev_auth_role
    if (
        len(authorization_headers) != 1
        or credentials is None
        or settings.app_env not in {"local", "test"}
        or token is None
        or role is None
        or not hmac.compare_digest(
            credentials.credentials.encode("utf-8"),
            token.get_secret_value().encode("utf-8"),
        )
    ):
        raise InvalidTokenError()
    return role


def require_viewer(role: Annotated[Role, Depends(get_current_role)]) -> Role:
    return role


def require_operator(role: Annotated[Role, Depends(get_current_role)]) -> Role:
    if role not in {"operator", "admin"}:
        raise HTTPException(status_code=403)
    return role


def require_admin(role: Annotated[Role, Depends(get_current_role)]) -> Role:
    if role != "admin":
        raise HTTPException(status_code=403)
    return role
