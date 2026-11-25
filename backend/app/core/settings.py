"""Process environment settings for the evaluation backend."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import PostgresDsn, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsError(RuntimeError):
    """Configuration could not be loaded without exposing input values."""


class Settings(BaseSettings):
    """Typed application configuration with opt-in development credentials."""

    # BaseSettings selects known env variables; explicit extra arguments are rejected.
    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=None,
        extra="forbid",
        hide_input_in_errors=True,
        frozen=True,
    )

    app_env: Literal["local", "test", "staging", "production"]
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr
    dev_auth_token: SecretStr | None = None
    dev_auth_role: Literal["viewer", "operator", "admin"] | None = None
    cursor_signing_key: SecretStr

    @field_validator("app_env", "dev_auth_role", mode="before")
    @classmethod
    def normalize_lowercase(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            PostgresDsn(value.get_secret_value())
        except ValidationError:
            raise ValueError("must be a PostgreSQL DSN") from None
        return value

    @field_validator("dev_auth_token", "cursor_signing_key")
    @classmethod
    def validate_nonblank_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_dev_auth_scope(self) -> Self:
        has_token = self.dev_auth_token is not None
        has_role = self.dev_auth_role is not None
        if self.app_env not in {"local", "test"} and (has_token or has_role):
            raise ValueError("DEV_AUTH_TOKEN and DEV_AUTH_ROLE are permitted only in local/test")
        if has_token != has_role:
            raise ValueError("DEV_AUTH_TOKEN and DEV_AUTH_ROLE must be configured together")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once and expose only field names and error descriptions."""
    try:
        return Settings()
    except ValidationError as error:
        problems = [
            f"{'.'.join(map(str, issue['loc'])).upper() or 'SETTINGS'}: {issue['msg']}"
            for issue in error.errors(include_input=False, include_context=False, include_url=False)
        ]
    raise SettingsError("Invalid configuration\n" + "\n".join(problems))
