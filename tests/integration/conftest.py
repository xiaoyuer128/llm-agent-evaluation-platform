"""Guarded PostgreSQL fixtures for migration and recovery tests."""

import os
from collections.abc import Callable, Iterator
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import URL, Connection, Engine, make_url, text
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.schema import CreateSchema, DropSchema

from alembic import command
from backend.app.core.settings import Settings
from backend.app.db.session import create_database_engine


def _test_url() -> URL:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.exit("Set TEST_DATABASE_URL to a dedicated PostgreSQL 17 database ending in _test.")
    try:
        url = make_url(value)
        development = (
            make_url(os.environ["DATABASE_URL"]) if os.environ.get("DATABASE_URL") else None
        )
    except ArgumentError:
        pytest.exit("TEST_DATABASE_URL and DATABASE_URL must be valid SQLAlchemy URLs.")

    if (
        url.drivername not in {"postgresql", "postgresql+psycopg"}
        or not url.database
        or not url.database.endswith("_test")
        or {"dbname", "database", "service", "options"} & set(url.query)
    ):
        pytest.exit("The test URL must explicitly select a PostgreSQL database ending in _test.")
    if development is not None:
        development_names = {development.database}
        for key in ("dbname", "database"):
            override = development.query.get(key)
            if isinstance(override, str):
                development_names.add(override)
            elif override is not None:
                development_names.update(override)
        if url.database in development_names:
            pytest.exit("TEST_DATABASE_URL must select a different database from DATABASE_URL.")
    return url


def _settings(url: URL) -> Settings:
    return Settings(
        app_env="test",
        log_level="ERROR",
        database_url=url.render_as_string(hide_password=False),
        cursor_signing_key="migration-test-only-signing-placeholder",
        dev_auth_token=None,
        dev_auth_role=None,
    )


def _assert_destination(connection: Connection, expected_database: str) -> None:
    if connection.scalar(text("SELECT current_database()")) != expected_database:
        pytest.exit("Connected database does not match the isolated _test target.")
    if int(connection.scalar(text("SHOW server_version_num"))) // 10000 != 17:
        pytest.exit("Migration tests require a real PostgreSQL 17 server.")


@pytest.fixture(scope="session")
def test_database() -> Iterator[tuple[URL, Engine]]:
    url = _test_url()
    assert url.database is not None
    public_url = url.update_query_dict({"options": "-csearch_path=public"})
    engine = create_database_engine(_settings(public_url))
    try:
        try:
            with engine.begin() as connection:
                _assert_destination(connection, url.database)
                config = Config("alembic.ini")
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        except SQLAlchemyError:
            pytest.fail("Could not prepare the dedicated PostgreSQL test database.", pytrace=False)
        yield url, engine
    finally:
        engine.dispose()


@pytest.fixture
def isolated_database(test_database: tuple[URL, Engine]) -> Iterator[tuple[Settings, Engine]]:
    url, maintenance_engine = test_database
    assert url.database is not None
    schema = f"m104_{uuid4().hex}"
    with maintenance_engine.begin() as connection:
        _assert_destination(connection, url.database)
        connection.execute(CreateSchema(schema))

    settings = _settings(url.update_query_dict({"options": f"-csearch_path={schema}"}))
    engine = create_database_engine(settings)
    try:
        yield settings, engine
    finally:
        engine.dispose()
        with maintenance_engine.begin() as connection:
            _assert_destination(connection, url.database)
            connection.execute(DropSchema(schema, cascade=True))


@pytest.fixture
def migrate(isolated_database: tuple[Settings, Engine]) -> Callable[[str], None]:
    _, engine = isolated_database
    with engine.connect() as connection:
        expected_schema = connection.scalar(text("SELECT current_schema()"))
    assert isinstance(expected_schema, str) and expected_schema.startswith("m104_")
    assert engine.url.database is not None
    expected_database = engine.url.database

    def migrate_to(revision: str) -> None:
        with engine.begin() as connection:
            _assert_destination(connection, expected_database)
            assert connection.scalar(text("SELECT current_schema()")) == expected_schema
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            if revision == "base":
                command.downgrade(config, revision)
            else:
                command.upgrade(config, revision)

    return migrate_to
