"""Synchronous engine, request sessions, and schema readiness."""

from collections.abc import Iterator
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import Request
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.settings import Settings

_ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


def database_url(settings: Settings) -> URL:
    try:
        url = make_url(settings.database_url.get_secret_value())
    except ArgumentError:
        raise ValueError("DATABASE_URL must be a valid PostgreSQL connection URL") from None
    if url.drivername not in {"postgresql", "postgres", "postgresql+psycopg"}:
        raise ValueError("DATABASE_URL must use the synchronous psycopg driver")
    return url.set(drivername="postgresql+psycopg")


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(
        database_url(settings),
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={"connect_timeout": 5},
        echo=False,
        hide_parameters=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, autocommit=False)


def get_db_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


def database_readiness_check(engine: Engine) -> bool:
    expected_heads = set(ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_heads())
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT 1")) != 1:
                return False
            current_heads = set(MigrationContext.configure(connection).get_current_heads())
            return bool(expected_heads) and current_heads == expected_heads
    except SQLAlchemyError:
        return False
