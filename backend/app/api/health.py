"""Public liveness and readiness probes."""

import logging
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from backend.app.api.errors import ErrorResponse
from backend.app.api.security import get_app_settings
from backend.app.core.settings import Settings

type ReadinessCheck = Callable[[], bool]

_logger = logging.getLogger(__name__)
health_router = APIRouter(prefix="/health", tags=["health"])


def get_readiness_check(request: Request) -> ReadinessCheck | None:
    return request.app.state.readiness_check


@health_router.get("/live")
def live() -> dict[str, Literal["ok"]]:
    return {"status": "ok"}


@health_router.get("/ready", responses={503: {"model": ErrorResponse}})
def ready(
    check: Annotated[ReadinessCheck | None, Depends(get_readiness_check)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, Literal["ready"]]:
    # Production readiness stays closed until a production identity provider is wired.
    if settings.app_env == "production" or check is None:
        raise HTTPException(status_code=503)
    try:
        is_ready = check()
    except Exception:
        _logger.warning("Readiness check failed")
        is_ready = False
    if is_ready is not True:
        raise HTTPException(status_code=503)
    return {"status": "ready"}
