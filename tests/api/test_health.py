import asyncio
from unittest.mock import Mock
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.api.health import get_readiness_check
from backend.app.api.router import api_router
from backend.app.core.request_context import get_request_id, reset_request_id, set_request_id
from backend.app.core.settings import Settings
from backend.app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        log_level="INFO",
        database_url="postgresql+psycopg://app:unused@127.0.0.1:5432/unit",
        cursor_signing_key="test-only-cursor-key",
        dev_auth_token=None,
        dev_auth_role=None,
    )


def test_factory_loads_configuration_and_initializes_logging_during_startup(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    load = Mock(return_value=settings)
    configure = Mock()
    monkeypatch.setattr("backend.app.main.get_settings", load)
    monkeypatch.setattr("backend.app.main.configure_logging", configure)

    application = create_app()
    isolated = create_app(settings=settings)
    load.assert_not_called()
    configure.assert_not_called()

    with TestClient(application):
        load.assert_called_once_with()
        configure.assert_called_once_with(settings=settings)
        assert application.state.settings is settings
        assert getattr(isolated.state, "settings", None) is None

    with TestClient(isolated):
        assert isolated.state.settings is settings
    load.assert_called_once_with()
    assert configure.call_count == 2


def test_live_does_not_call_readiness_or_require_auth(settings: Settings) -> None:
    check = Mock(side_effect=AssertionError("liveness must not probe dependencies"))
    with TestClient(create_app(settings=settings, readiness_check=check)) as client:
        response = client.get("/health/live", headers={"X-Request-ID": "liveness:123"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "liveness:123"
    check.assert_not_called()


def test_readiness_supports_dependency_override(settings: Settings) -> None:
    check = Mock(return_value=True)
    application = create_app(settings=settings)
    application.dependency_overrides[get_readiness_check] = lambda: check

    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    check.assert_called_once_with()


@pytest.mark.parametrize("check_outcome", ["not_configured", "unready", "exception"])
def test_readiness_failure_contract(settings: Settings, check_outcome: str) -> None:
    check = None
    if check_outcome == "unready":
        check = Mock(return_value=False)
    elif check_outcome == "exception":
        check = Mock(side_effect=RuntimeError("postgresql://fixture:private@unit/db"))

    with TestClient(create_app(settings=settings, readiness_check=check)) as client:
        response = client.get("/health/ready", headers={"X-Request-ID": "probe-01"})

    assert response.status_code == 503
    assert response.headers["X-Request-ID"] == "probe-01"
    assert response.json() == {
        "error": {
            "code": "SERVICE_NOT_READY",
            "message": "服务尚未就绪",
            "request_id": "probe-01",
            "details": [],
        }
    }
    if check is not None:
        check.assert_called_once_with()


def test_production_readiness_stays_closed_without_production_auth(
    settings: Settings,
) -> None:
    production_settings = Settings(
        app_env="production",
        log_level="INFO",
        database_url=settings.database_url,
        cursor_signing_key=settings.cursor_signing_key,
        dev_auth_token=None,
        dev_auth_role=None,
    )
    check = Mock(return_value=True)
    with TestClient(create_app(settings=production_settings, readiness_check=check)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
    check.assert_not_called()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Request-ID": "invalid id"},
        {"X-Request-ID": "x" * 129},
        [("X-Request-ID", "first"), ("X-Request-ID", "second")],
    ],
)
def test_missing_or_invalid_request_id_is_replaced(
    settings: Settings, headers: dict[str, str] | list[tuple[str, str]]
) -> None:
    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/health/live", headers=headers)

    assert response.status_code == 200
    assert UUID(response.headers["X-Request-ID"]).version == 4


def test_concurrent_requests_keep_distinct_ids(settings: Settings) -> None:
    async def run() -> None:
        application = create_app(settings=settings)
        barrier = asyncio.Barrier(2)

        @application.get("/test/request-id")
        async def observe() -> dict[str, str | None]:
            await barrier.wait()
            return {"request_id": get_request_id()}

        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first, second = await asyncio.gather(
                client.get("/test/request-id", headers={"X-Request-ID": "first"}),
                client.get("/test/request-id", headers={"X-Request-ID": "second"}),
            )
        for response, expected in ((first, "first"), (second, "second")):
            assert response.headers["X-Request-ID"] == expected
            assert response.json() == {"request_id": expected}
        assert get_request_id() is None

    asyncio.run(run())


def test_exception_restores_the_original_context(settings: Settings) -> None:
    async def run() -> None:
        application = create_app(settings=settings)

        @application.get("/test/failure")
        async def fail() -> None:
            assert get_request_id() == "inner"
            raise RuntimeError("controlled fixture failure")

        token = set_request_id("outer")
        try:
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/test/failure", headers={"X-Request-ID": "inner"})
            assert response.status_code == 500
            assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
            assert get_request_id() == "outer"
        finally:
            reset_request_id(token)
        assert get_request_id() is None

    asyncio.run(run())


def test_swagger_and_openapi_are_public(settings: Settings) -> None:
    assert api_router.prefix == "/api/v1"
    with TestClient(create_app(settings=settings)) as client:
        swagger = client.get("/docs")
        schema = client.get("/openapi.json")

    assert swagger.status_code == schema.status_code == 200
    assert "/openapi.json" in swagger.text
    assert "X-Request-ID" in swagger.headers
    assert "X-Request-ID" in schema.headers
    document = schema.json()
    assert set(document["paths"]) == {"/health/live", "/health/ready"}
    assert document["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert "security" not in document
    assert "503" in document["paths"]["/health/ready"]["get"]["responses"]
