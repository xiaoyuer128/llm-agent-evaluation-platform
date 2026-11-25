import asyncio
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.app.core.logging import configure_logging
from backend.app.core.request_context import (
    get_request_id,
    is_valid_request_id,
    reset_request_id,
    set_request_id,
)
from backend.app.core.settings import Settings


@pytest.fixture
def isolated_backend_logger() -> Iterator[logging.Logger]:
    backend = logging.getLogger("backend")
    handlers, level, propagate = backend.handlers[:], backend.level, backend.propagate
    for handler in handlers:
        backend.removeHandler(handler)

    try:
        yield logging.getLogger("backend.app")
    finally:
        for handler in backend.handlers[:]:
            backend.removeHandler(handler)
            handler.close()
        for handler in handlers:
            backend.addHandler(handler)
        backend.setLevel(level)
        backend.propagate = propagate


def test_request_id_validation_and_reset() -> None:
    assert get_request_id() is None
    assert is_valid_request_id("Job.1_a-3:4")
    assert is_valid_request_id("a" * 128)
    assert not is_valid_request_id(7)

    outer = set_request_id("outer-id")
    try:
        inner = set_request_id("inner-id")
        try:
            assert get_request_id() == "inner-id"
        finally:
            reset_request_id(inner)
        assert get_request_id() == "outer-id"
    finally:
        reset_request_id(outer)

    assert get_request_id() is None


@pytest.mark.parametrize("value", [None, "", "a" * 129, "bad ID", "bad\n", "请求"])
def test_missing_or_invalid_request_ids_use_uuids(value: str | None) -> None:
    assert not is_valid_request_id(value)
    token = set_request_id(value)
    try:
        actual = get_request_id()
        assert actual is not None
        assert UUID(actual).version == 4
    finally:
        reset_request_id(token)
    assert get_request_id() is None


def test_request_context_is_isolated_between_async_tasks() -> None:
    async def run_concurrently() -> list[str | None]:
        barrier = asyncio.Barrier(2)

        async def read_request_id(value: str) -> str | None:
            token = set_request_id(value)
            try:
                await barrier.wait()
                return get_request_id()
            finally:
                reset_request_id(token)
                assert get_request_id() is None

        return await asyncio.gather(read_request_id("first"), read_request_id("second"))

    assert asyncio.run(run_concurrently()) == ["first", "second"]
    assert get_request_id() is None


def test_json_log_fields_and_current_request_id(
    capsys: pytest.CaptureFixture[str], isolated_backend_logger: logging.Logger
) -> None:
    configure_logging()
    token = set_request_id("req-123")
    try:
        isolated_backend_logger.info(
            "application %s",
            "started",
            extra={
                "request_id": "external-spoof",
                "run_id": "run-01",
                "item_id": "item-01",
                "attempt_no": 2,
                "unexpected": "ignored",
            },
        )
    finally:
        reset_request_id(token)

    output = json.loads(capsys.readouterr().out)
    timestamp = output.pop("timestamp")
    assert timestamp.endswith("Z")
    assert datetime.fromisoformat(timestamp).tzinfo is UTC
    assert output == {
        "level": "INFO",
        "logger": "backend.app",
        "message": "application started",
        "request_id": "req-123",
        "run_id": "run-01",
        "item_id": "item-01",
        "attempt_no": 2,
    }


def test_reconfiguration_filters_levels_without_duplicate_records(
    capsys: pytest.CaptureFixture[str], isolated_backend_logger: logging.Logger
) -> None:
    configure_logging()
    isolated_backend_logger.debug("hidden")
    configure_logging("DEBUG")
    configure_logging("DEBUG")
    isolated_backend_logger.debug("debug event")
    isolated_backend_logger.info("info event")
    configure_logging("WARNING")
    isolated_backend_logger.info("another hidden event")
    isolated_backend_logger.warning("warning event")
    isolated_backend_logger.error("error event")

    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [record["level"] for record in output] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert all(record["request_id"] is None for record in output)
    assert len(logging.getLogger("backend").handlers) == 1


def test_log_redacts_credential_formats_and_omits_private_extra_fields(
    capsys: pytest.CaptureFixture[str], isolated_backend_logger: logging.Logger
) -> None:
    configure_logging()
    isolated_backend_logger.info(
        "database=%s Authorization: Bearer %s Password=%s x-api-key=%s",
        "postgresql+psycopg://app:dsn-private@127.0.0.1/db",
        "auth-private",
        "password-private",
        "api-key-private",
        extra={
            "request_body": "raw-request-private",
            "model_input": "raw-input-private",
            "model_output": "raw-output-private",
            "authorization": "metadata-private",
            "run_id": "invalid id",
            "attempt_no": True,
        },
    )
    isolated_backend_logger.info('before model_output={"answer":"embedded-output-private"}')
    isolated_backend_logger.info(
        "Authorization: Basic %s token %s", "encoded-basic-private", "sk-example-private-key"
    )

    text = capsys.readouterr().out
    for value in (
        "dsn-private",
        "auth-private",
        "password-private",
        "api-key-private",
        "raw-request-private",
        "raw-input-private",
        "raw-output-private",
        "metadata-private",
        "embedded-output-private",
        "encoded-basic-private",
        "sk-example-private-key",
    ):
        assert value not in text
    first, second, third = [json.loads(line) for line in text.splitlines()]
    assert first["message"].count("[REDACTED]") == 4
    assert "run_id" not in first
    assert "attempt_no" not in first
    assert second["message"] == "before [REDACTED]"
    assert third["message"] == "Authorization: [REDACTED] token [REDACTED]"


def test_configured_secrets_are_redacted_even_without_a_label(
    capsys: pytest.CaptureFixture[str],
    isolated_backend_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("DEV_AUTH_TOKEN", "DEV_AUTH_ROLE"):
        monkeypatch.delenv(variable, raising=False)
        monkeypatch.delenv(variable.lower(), raising=False)
    settings = Settings(
        app_env="local",
        log_level="DEBUG",
        database_url="postgresql://app:example%2Fhidden@127.0.0.1/db",
        dev_auth_role="operator",
        dev_auth_token="opaque-dev-secret",
        cursor_signing_key="opaque-signing-secret",
    )
    configure_logging(settings=settings)
    isolated_backend_logger.debug(
        "%s %s %s %s", "example/hidden", "example%2Fhidden", "opaque-dev-secret", "opaque-signing-secret"
    )
    isolated_backend_logger.info(settings)
    configure_logging("INFO")
    isolated_backend_logger.info("%s", "opaque-signing-secret")

    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert output[0]["message"] == " ".join(["[REDACTED]"] * 4)
    assert output[0]["level"] == "DEBUG"
    assert output[1]["message"] == "Non-text log message omitted"
    assert output[2]["message"] == "[REDACTED]"


def test_exception_details_and_logging_errors_omit_raw_values(
    capsys: pytest.CaptureFixture[str], isolated_backend_logger: logging.Logger
) -> None:
    configure_logging()
    try:
        raise RuntimeError("sensitive-exception-text")
    except RuntimeError as error:
        isolated_backend_logger.exception("Could not process: %s", error)

    isolated_backend_logger.info("Invalid format %d", "unformatted-sensitive-argument")
    captured = capsys.readouterr()

    assert "sensitive-exception-text" not in captured.out + captured.err
    assert "unformatted-sensitive-argument" not in captured.out + captured.err
    first, second = [json.loads(line) for line in captured.out.splitlines()]
    assert first["error_type"] == "RuntimeError"
    assert first["message"] == "An exception occurred"
    assert second["message"] == "Log message could not be formatted"
