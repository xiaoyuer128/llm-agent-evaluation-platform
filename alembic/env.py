"""Run Alembic migrations with the configured synchronous PostgreSQL driver."""

from sqlalchemy import Connection

from alembic import context
from backend.app.core.settings import get_settings
from backend.app.db import models
from backend.app.db.session import create_database_engine, database_url

target_metadata = models.Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(get_settings()),
        target_metadata=target_metadata,
        compare_type=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        run_migrations(connection)
        return

    engine = create_database_engine(get_settings())
    try:
        with engine.connect() as connection:
            run_migrations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
