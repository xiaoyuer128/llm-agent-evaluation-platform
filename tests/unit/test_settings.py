import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from backend.app.core.settings import Settings, SettingsError, get_settings

_ENV_KEYS = {
    "APP_ENV",
    "LOG_LEVEL",
    "DATABASE_URL",
    "DEV_AUTH_TOKEN",
    "DEV_AUTH_ROLE",
    "CURSOR_SIGNING_KEY",
}
_DATABASE_PASSWORD = "example-db-password"
_DATABASE_URL = f"postgresql+psycopg://app:{_DATABASE_PASSWORD}@127.0.0.1:5432/llm_eval"
_CURSOR_KEY = "example-cursor-signing-key"
_DEV_TOKEN = "example-dev-identity-token"


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in tuple(os.environ):
        if key.upper() in _ENV_KEYS:
            monkeypatch.delenv(key)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", _DATABASE_URL)
    monkeypatch.setenv("CURSOR_SIGNING_KEY", _CURSOR_KEY)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_system_environment_and_case_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("app_env", " TEST ")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("DEV_AUTH_ROLE", " Operator ")
    monkeypatch.setenv("DEV_AUTH_TOKEN", _DEV_TOKEN)
    monkeypatch.setenv("M102_UNRELATED_ENV", "ignored")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.dev_auth_role == "operator"
    assert settings.database_url.get_secret_value() == _DATABASE_URL
    assert settings.cursor_signing_key.get_secret_value() == _CURSOR_KEY
    assert settings.dev_auth_token == SecretStr(_DEV_TOKEN)
    assert not hasattr(settings, "m102_unrelated_env")


def test_explicit_settings_override_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("APP_ENV", "production")

    settings = Settings(log_level="ERROR", app_env="test")

    assert settings.log_level == "ERROR"
    assert settings.app_env == "test"


def test_environment_overrides_the_default_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().log_level == "INFO"
    monkeypatch.setenv("LOG_LEVEL", "CRITICAL")
    assert Settings().log_level == "CRITICAL"


def test_dotenv_in_working_directory_is_not_loaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LOG_LEVEL=DEBUG\nDEV_AUTH_ROLE=admin\n", encoding="utf-8")

    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.dev_auth_role is None


@pytest.mark.parametrize("name", ["APP_ENV", "DATABASE_URL", "CURSOR_SIGNING_KEY"])
def test_missing_required_setting_is_identified(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(name)

    with pytest.raises(SettingsError, match=name) as error:
        get_settings()

    assert _DATABASE_PASSWORD not in str(error.value)
    assert _CURSOR_KEY not in str(error.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_ENV", "development"),
        ("LOG_LEVEL", "VERBOSE"),
        ("DEV_AUTH_ROLE", "root"),
        ("DATABASE_URL", "sqlite:///invalid"),
        ("DEV_AUTH_TOKEN", "   "),
        ("CURSOR_SIGNING_KEY", "   "),
    ],
)
def test_invalid_configuration_is_rejected(
    name: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(SettingsError, match=name):
        get_settings()


@pytest.mark.parametrize("app_env", ["local", "test"])
def test_local_and_test_allow_explicit_development_identity(
    app_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    assert Settings().dev_auth_role is None
    assert Settings().dev_auth_token is None

    monkeypatch.setenv("DEV_AUTH_ROLE", "viewer")
    monkeypatch.setenv("DEV_AUTH_TOKEN", _DEV_TOKEN)
    settings = Settings()

    assert settings.dev_auth_role == "viewer"
    assert settings.dev_auth_token == SecretStr(_DEV_TOKEN)


@pytest.mark.parametrize("app_env", ["staging", "production"])
@pytest.mark.parametrize(
    "credentials",
    [
        {"DEV_AUTH_TOKEN": _DEV_TOKEN},
        {"DEV_AUTH_ROLE": "admin"},
        {"DEV_AUTH_TOKEN": _DEV_TOKEN, "DEV_AUTH_ROLE": "admin"},
    ],
)
def test_deployed_environments_reject_any_development_identity(
    app_env: str, credentials: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    assert Settings().app_env == app_env
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(SettingsError, match="permitted only in local/test"):
        get_settings()


@pytest.mark.parametrize("name", ["DEV_AUTH_TOKEN", "DEV_AUTH_ROLE"])
def test_development_identity_requires_both_values(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, _DEV_TOKEN if name == "DEV_AUTH_TOKEN" else "admin")

    with pytest.raises(SettingsError, match="must be configured together"):
        get_settings()


def test_secrets_are_masked_in_repr_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_AUTH_TOKEN", _DEV_TOKEN)
    monkeypatch.setenv("DEV_AUTH_ROLE", "operator")
    settings = Settings()

    for field in ("database_url", "dev_auth_token", "cursor_signing_key"):
        assert isinstance(getattr(settings, field), SecretStr)
        assert settings.model_dump(mode="json")[field] == "**********"
    for rendered in (repr(settings), settings.model_dump_json()):
        for value in (_DATABASE_URL, _DATABASE_PASSWORD, _DEV_TOKEN, _CURSOR_KEY):
            assert value not in rendered


def test_configuration_errors_omit_raw_values(monkeypatch: pytest.MonkeyPatch) -> None:
    invalid_dsn = f"https://app:{_DATABASE_PASSWORD}@example.test/db"
    monkeypatch.setenv("DATABASE_URL", invalid_dsn)
    monkeypatch.setenv("APP_ENV", _DEV_TOKEN)

    with pytest.raises(ValidationError) as direct_error:
        Settings()
    with pytest.raises(SettingsError) as cached_error:
        get_settings()

    for displayed in (str(direct_error.value), repr(direct_error.value), str(cached_error.value)):
        assert _DATABASE_PASSWORD not in displayed
        assert _DEV_TOKEN not in displayed
        assert _CURSOR_KEY not in displayed
    assert cached_error.value.__context__ is None


def test_unknown_constructor_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Settings(unknown_setting="not a project setting")


def test_settings_cache_is_lazy_and_can_be_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    initial = get_settings()
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    assert initial.log_level == "INFO"
    assert get_settings() is initial
    get_settings.cache_clear()
    assert get_settings().log_level == "DEBUG"


def test_example_environment_matches_the_configuration_fields(pytestconfig: pytest.Config) -> None:
    template = (pytestconfig.rootpath / ".env.example").read_text(encoding="utf-8")
    example = dict(line.split("=", 1) for line in template.splitlines() if line)

    assert set(example) == {field.upper() for field in Settings.model_fields}
    assert ":CHANGE_ME@" in example["DATABASE_URL"]
    assert example["DEV_AUTH_TOKEN"] == "REPLACE_WITH_RANDOM_LOCAL_TOKEN"
    assert example["CURSOR_SIGNING_KEY"] == "REPLACE_WITH_RANDOM_SECRET"
