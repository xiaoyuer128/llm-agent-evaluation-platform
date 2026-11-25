"""FastAPI application factory and ASGI entry point."""

from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
from functools import partial
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from backend.app.api.errors import handle_http_exception, handle_validation_error
from backend.app.api.health import ReadinessCheck, health_router
from backend.app.api.middleware import RequestIDMiddleware
from backend.app.api.router import api_router
from backend.app.api.security import bearer_scheme
from backend.app.core.logging import configure_logging
from backend.app.core.settings import Settings, get_settings
from backend.app.db.session import (
    create_database_engine,
    create_session_factory,
    database_readiness_check,
)


def create_app(
    *,
    settings: Settings | None = None,
    readiness_check: ReadinessCheck | None = None,
    enable_database_readiness: bool = False,
) -> FastAPI:
    """Create an isolated app with optional settings and readiness injection."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        app_settings = settings if settings is not None else get_settings()
        configure_logging(settings=app_settings)
        application.state.settings = app_settings
        with ExitStack() as cleanup:
            if enable_database_readiness:
                engine = create_database_engine(app_settings)
                cleanup.callback(engine.dispose)
                application.state.session_factory = create_session_factory(engine)
                if readiness_check is None:
                    application.state.readiness_check = partial(database_readiness_check, engine)
            yield

    application = FastAPI(title="LLM Agent Evaluation Platform", lifespan=lifespan, debug=False)
    application.state.readiness_check = readiness_check
    application.add_exception_handler(HTTPException, handle_http_exception)
    application.add_exception_handler(RequestValidationError, handle_validation_error)
    application.add_middleware(RequestIDMiddleware)
    application.include_router(health_router)
    application.include_router(api_router)

    default_openapi = application.openapi

    def openapi_with_bearer() -> dict[str, Any]:
        # The business router is empty in M1-03, so no route adds the scheme yet.
        schema = default_openapi()
        security_schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        security_schemes.setdefault(
            bearer_scheme.scheme_name,
            bearer_scheme.model.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        return schema

    application.openapi = openapi_with_bearer
    return application


app = create_app(enable_database_readiness=True)
