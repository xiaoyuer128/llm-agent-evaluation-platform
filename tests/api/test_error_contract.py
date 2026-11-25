import logging
from typing import Annotated, Literal

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api.security import (
    Role,
    get_current_role,
    require_admin,
    require_operator,
    require_viewer,
)
from backend.app.core.settings import Settings
from backend.app.main import create_app

_FIXTURE_TOKEN = "test-only-development-token"
_FIXTURE_DSN = "postgresql+psycopg://app:private-fixture-password@127.0.0.1:5432/unit"
_FIXTURE_HEADER = f"Bearer {_FIXTURE_TOKEN}"


def make_settings(
    role: Role | None = None, *, app_env: Literal["local", "test", "staging", "production"] = "test"
) -> Settings:
    return Settings(
        app_env=app_env,
        log_level="INFO",
        database_url=_FIXTURE_DSN,
        cursor_signing_key="test-only-cursor-signing-key",
        dev_auth_token=_FIXTURE_TOKEN if role is not None else None,
        dev_auth_role=role,
    )


def secured_app(settings: Settings) -> FastAPI:
    application = create_app(settings=settings)
    router = APIRouter(prefix="/api/v1")

    @router.get("/test-viewer")
    def viewer(role: Annotated[Role, Depends(require_viewer)]) -> dict[str, Role]:
        return {"role": role}

    @router.post("/test-operator")
    def operator(role: Annotated[Role, Depends(require_operator)]) -> dict[str, Role]:
        return {"role": role}

    @router.delete("/test-admin")
    def admin(role: Annotated[Role, Depends(require_admin)]) -> dict[str, Role]:
        return {"role": role}

    application.include_router(router)
    return application


def assert_public_error(
    response: httpx.Response, status_code: int, code: str, request_id: str | None = None
) -> dict[str, object]:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error"}
    error = payload["error"]
    assert set(error) == {"code", "message", "request_id", "details"}
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert error["request_id"] == response.headers["X-Request-ID"]
    if request_id is not None:
        assert error["request_id"] == request_id
    return error


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (400, "INVALID_CURSOR"),
        (403, "FORBIDDEN"),
        (404, "RESOURCE_NOT_FOUND"),
        (422, "VALIDATION_ERROR"),
        (503, "SERVICE_NOT_READY"),
    ],
)
def test_http_exception_uses_public_mapping_without_reflecting_details(
    status_code: int, code: str
) -> None:
    application = create_app(settings=make_settings())

    @application.get("/test/http-error")
    def fail() -> None:
        raise HTTPException(
            status_code=status_code,
            detail=f"{_FIXTURE_DSN}; Authorization: {_FIXTURE_HEADER}",
            headers={"Authorization": _FIXTURE_HEADER, "X-Request-ID": "spoofed"},
        )

    with TestClient(application) as client:
        response = client.get("/test/http-error", headers={"X-Request-ID": "http-err"})

    assert assert_public_error(response, status_code, code, "http-err")["details"] == []
    assert _FIXTURE_DSN not in response.text
    assert _FIXTURE_TOKEN not in response.text
    assert "authorization" not in response.headers


def test_router_generated_404_and_405_follow_the_error_contract() -> None:
    with TestClient(create_app(settings=make_settings())) as client:
        missing = client.get("/api/v1/not-implemented")
        wrong_method = client.post("/health/live")

    assert_public_error(missing, 404, "RESOURCE_NOT_FOUND")
    assert_public_error(wrong_method, 405, "METHOD_NOT_ALLOWED")
    assert wrong_method.headers["Allow"] == "GET"


def test_request_validation_returns_only_safe_locations_and_types() -> None:
    application = create_app(settings=make_settings())

    @application.post("/test/validate")
    def validate(data: dict[str, int]) -> dict[str, int]:
        return data

    with TestClient(application) as client:
        response = client.post(
            "/test/validate",
            headers={"X-Request-ID": "invalid-input", "Authorization": _FIXTURE_HEADER},
            json={_FIXTURE_TOKEN: _FIXTURE_DSN},
        )

    error = assert_public_error(response, 422, "VALIDATION_ERROR", "invalid-input")
    assert error["details"] == [{"loc": ["body"], "type": "int_parsing"}]
    assert _FIXTURE_TOKEN not in response.text
    assert _FIXTURE_DSN not in response.text
    assert "authorization" not in response.text.lower()


def test_unhandled_error_has_safe_response_and_does_not_forward_raw_exception(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    logger = logging.getLogger("backend")
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "propagate", logger.propagate)
    monkeypatch.setattr(logger, "level", logger.level)
    application = create_app(settings=make_settings())

    @application.get("/test/unhandled")
    def fail() -> None:
        raise RuntimeError(f"internal/path.py: {_FIXTURE_DSN}; Authorization: {_FIXTURE_HEADER}")

    # The default TestClient raises escaped server exceptions.
    with TestClient(application) as client:
        response = client.get("/test/unhandled", headers={"X-Request-ID": "unhandled-1"})

    assert assert_public_error(response, 500, "INTERNAL_ERROR", "unhandled-1")["details"] == []
    captured = capsys.readouterr()
    assert "unhandled-1" in captured.out
    for text in (response.text, captured.out, captured.err):
        assert "Traceback" not in text
        assert "internal/path.py" not in text
        assert _FIXTURE_DSN not in text
        assert _FIXTURE_TOKEN not in text
        assert "Authorization" not in text


@pytest.mark.parametrize(
    ("authorization", "code"),
    [
        (None, "AUTH_REQUIRED"),
        ("Bearer test-only-wrong-token", "INVALID_TOKEN"),
        ("Basic test-only-developer-credential", "INVALID_TOKEN"),
    ],
)
def test_credentials_are_required_and_invalid_credentials_are_not_reflected(
    authorization: str | None, code: str
) -> None:
    headers = {"Authorization": authorization} if authorization is not None else {}
    with TestClient(secured_app(make_settings("viewer"))) as client:
        response = client.get("/api/v1/test-viewer", headers=headers)

    assert_public_error(response, 401, code)
    assert response.headers["WWW-Authenticate"] == "Bearer"
    if authorization is not None:
        assert authorization not in response.text


@pytest.mark.parametrize(
    ("role", "expected"),
    [("viewer", (200, 403, 403)), ("operator", (200, 200, 403)), ("admin", (200, 200, 200))],
)
def test_roles_grant_the_expected_permission_hierarchy(
    role: Role, expected: tuple[int, int, int]
) -> None:
    headers = {"Authorization": _FIXTURE_HEADER}
    with TestClient(secured_app(make_settings(role))) as client:
        responses = (
            client.get("/api/v1/test-viewer", headers=headers),
            client.post("/api/v1/test-operator", headers=headers),
            client.delete("/api/v1/test-admin", headers=headers),
        )

    assert tuple(response.status_code for response in responses) == expected
    for response in responses:
        if response.status_code == 403:
            assert_public_error(response, 403, "FORBIDDEN")
        else:
            assert response.json() == {"role": role}


@pytest.mark.parametrize("app_env", ["test", "staging", "production"])
def test_authentication_fails_closed_without_credentials(
    app_env: Literal["test", "staging", "production"],
) -> None:
    with TestClient(secured_app(make_settings(app_env=app_env))) as client:
        response = client.get("/api/v1/test-viewer", headers={"Authorization": _FIXTURE_HEADER})

    assert_public_error(response, 401, "INVALID_TOKEN")


def test_security_dependencies_can_be_overridden_and_declare_bearer() -> None:
    application = secured_app(make_settings())
    application.dependency_overrides[get_current_role] = lambda: "operator"

    with TestClient(application) as client:
        response = client.post("/api/v1/test-operator")

    assert response.json() == {"role": "operator"}
    schema = secured_app(make_settings()).openapi()
    operation = schema["paths"]["/api/v1/test-operator"]["post"]
    assert operation["security"] == [{"BearerAuth": []}]
