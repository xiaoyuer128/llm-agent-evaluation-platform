"""Exercise the first migration and the real ASGI database wiring."""

import logging
import socket
from collections.abc import Callable
from typing import Annotated
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Engine,
    Text,
    Uuid,
    delete,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.settings import Settings
from backend.app.db.models import (
    AuditEvent,
    Base,
    Dataset,
    DatasetItem,
    DatasetVersion,
    ModelConfig,
)
from backend.app.db.session import database_url, get_db_session
from backend.app.main import app, create_app

pytestmark = pytest.mark.integration

_CORE_TABLES = {"datasets", "dataset_versions", "dataset_items", "model_configs", "audit_events"}


def test_migration_replays_and_matches_orm(
    isolated_database: tuple[Settings, Engine], migrate: Callable[[str], None]
) -> None:
    _, engine = isolated_database
    assert inspect(engine).get_table_names() == []

    migrate("head")
    expected_heads = tuple(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
    assert expected_heads == ("0001_m1_core",)
    for _ in range(2):
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert set(inspector.get_table_names()) == _CORE_TABLES | {"alembic_version"}
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": True}
            )
            assert context.get_current_heads() == expected_heads
            assert compare_metadata(context, Base.metadata) == []
            for table in _CORE_TABLES:
                columns = {entry["name"]: entry for entry in inspector.get_columns(table)}
                assert isinstance(columns["id"]["type"], Uuid)
                assert inspector.get_pk_constraint(table)["constrained_columns"] == ["id"]
                for field, column in columns.items():
                    if field.endswith("_at"):
                        column_type = column["type"]
                        assert isinstance(column_type, DateTime) and column_type.timezone

            item_columns = {
                column["name"]: column["type"] for column in inspector.get_columns("dataset_items")
            }
            assert isinstance(item_columns["input"], JSONB)
            assert isinstance(item_columns["expectation"], JSONB)
            assert isinstance(item_columns["tags"], ARRAY)
            assert isinstance(item_columns["tags"].item_type, Text)
            for table, json_column in (
                ("model_configs", "parameters"),
                ("audit_events", "metadata"),
            ):
                columns = {
                    column["name"]: column["type"] for column in inspector.get_columns(table)
                }
                assert isinstance(columns[json_column], JSONB)

            assert any(
                index["column_names"] == ["resource_type", "resource_id", "created_at", "id"]
                for index in inspector.get_indexes("audit_events")
            )
            for table, parent in (
                ("dataset_versions", "datasets"),
                ("dataset_items", "dataset_versions"),
            ):
                foreign_keys = inspector.get_foreign_keys(table)
                assert [
                    (key["referred_table"], key["options"]["ondelete"]) for key in foreign_keys
                ] == [(parent, "RESTRICT")]
            for table in _CORE_TABLES:
                expected_checks = {
                    item.name
                    for item in Base.metadata.tables[table].constraints
                    if isinstance(item, CheckConstraint)
                }
                assert {
                    item["name"] for item in inspector.get_check_constraints(table)
                } == expected_checks
        migrate("head")

    migrate("base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_heads() == ()
        assert (
            connection.scalar(text("SELECT to_regprocedure('reject_audit_event_mutation()')"))
            is None
        )

    migrate("head")
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_heads() == expected_heads
        assert set(inspect(connection).get_table_names()) == _CORE_TABLES | {"alembic_version"}


@pytest.mark.parametrize(
    ("table_name", "overrides", "constraint"),
    [
        ("datasets", {}, "uq_datasets_name"),
        ("datasets", {"name": "invalid-kind", "kind": "other"}, "ck_datasets_kind"),
        ("dataset_versions", {}, "uq_dataset_versions_dataset_id_version_no"),
        ("dataset_versions", {"version_no": 2, "status": "invalid"}, "ck_dataset_versions_status"),
        (
            "dataset_versions",
            {"version_no": 2, "item_count": -1},
            "ck_dataset_versions_item_count_nonnegative",
        ),
        (
            "dataset_versions",
            {"version_no": 2, "status": "published"},
            "ck_dataset_versions_publication",
        ),
        ("dataset_versions", {"dataset_id": uuid4()}, "fk_dataset_versions_dataset_id_datasets"),
        ("dataset_items", {"ordinal": 1}, "uq_dataset_items_dataset_version_id_case_key"),
        ("dataset_items", {"case_key": "next-case"}, "uq_dataset_items_dataset_version_id_ordinal"),
        ("dataset_items", {"ordinal": -1}, "ck_dataset_items_ordinal_nonnegative"),
        ("model_configs", {"revision": 0}, "ck_model_configs_revision_positive"),
        ("model_configs", {}, "uq_model_configs_config_key_revision"),
    ],
)
def test_constraints_reject_invalid_rows(
    isolated_database: tuple[Settings, Engine],
    migrate: Callable[[str], None],
    table_name: str,
    overrides: dict[str, object],
    constraint: str,
) -> None:
    _, engine = isolated_database
    migrate("head")
    datasets, versions, items, configs = (
        Dataset.__table__,
        DatasetVersion.__table__,
        DatasetItem.__table__,
        ModelConfig.__table__,
    )

    with engine.begin() as connection:
        dataset = {
            "name": "unique-dataset",
            "kind": "llm",
            "description": "",
            "created_by": uuid4(),
        }
        dataset_id = connection.scalar(datasets.insert().values(dataset).returning(datasets.c.id))
        version = {"dataset_id": dataset_id, "version_no": 1}
        version_id = connection.scalar(versions.insert().values(version).returning(versions.c.id))
        item = {
            "dataset_version_id": version_id,
            "case_key": "case-01",
            "ordinal": 0,
            "input": {"question": "unit fixture"},
            "expectation": {"value": 1},
            "tags": ["smoke"],
        }
        connection.execute(items.insert().values(item))
        config = {
            "config_key": "fixture",
            "revision": 1,
            "kind": "llm",
            "provider": "fake",
            "adapter_key": "fake",
            "model_name": "synthetic",
            "provider_endpoint_ref": "fake",
        }
        connection.execute(configs.insert().values(config))
        valid_values = {
            "datasets": dataset,
            "dataset_versions": version,
            "dataset_items": item,
            "model_configs": config,
        }
        values = valid_values[table_name] | overrides
        with pytest.raises(IntegrityError) as error, connection.begin_nested():
            connection.execute(Base.metadata.tables[table_name].insert().values(values))
        assert error.value.orig.diag.constraint_name == constraint
        assert connection.scalar(text("SELECT 1")) == 1


def test_foreign_keys_restrict_deletion(
    isolated_database: tuple[Settings, Engine], migrate: Callable[[str], None]
) -> None:
    _, engine = isolated_database
    migrate("head")
    datasets, versions = Dataset.__table__, DatasetVersion.__table__
    with engine.begin() as connection:
        dataset_id = connection.scalar(
            datasets.insert()
            .values(name="parent", kind="llm", description="", created_by=uuid4())
            .returning(datasets.c.id)
        )
        connection.execute(versions.insert().values(dataset_id=dataset_id, version_no=1))
        with pytest.raises(IntegrityError) as error, connection.begin_nested():
            connection.execute(delete(datasets).where(datasets.c.id == dataset_id))
        assert error.value.orig.diag.constraint_name == "fk_dataset_versions_dataset_id_datasets"


def test_audit_events_are_append_only(
    isolated_database: tuple[Settings, Engine], migrate: Callable[[str], None]
) -> None:
    _, engine = isolated_database
    migrate("head")
    events = AuditEvent.__table__
    with engine.begin() as connection:
        connection.execute(
            events.insert().values(
                actor_id=uuid4(),
                action="dataset_version.published",
                resource_type="dataset_version",
                resource_id=uuid4(),
                request_id="fixture-01",
                metadata={"status": "published"},
            )
        )
        for operation in (
            update(events).values(action="changed"),
            delete(events),
            text("TRUNCATE audit_events"),
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(operation)
        assert connection.scalar(select(func.count()).select_from(events)) == 1


def test_sessions_are_independent_closed_and_never_implicitly_committed(
    isolated_database: tuple[Settings, Engine], migrate: Callable[[str], None]
) -> None:
    settings, inspection_engine = isolated_database
    migrate("head")
    application = create_app(settings=settings, enable_database_readiness=True)
    used_sessions: list[Session] = []
    datasets = Dataset.__table__

    @application.post("/test/work")
    def work(session: Annotated[Session, Depends(get_db_session)]) -> dict[str, bool]:
        used_sessions.append(session)
        session.execute(
            datasets.insert().values(
                name="rollback-on-close", kind="llm", description="", created_by=uuid4()
            )
        )
        return {"accepted": True}

    with TestClient(application) as client:
        request_engine = application.state.session_factory.kw["bind"]
        original_pool = request_engine.pool
        assert client.post("/test/work").status_code == 200
        assert client.post("/test/work").status_code == 200
        assert len(used_sessions) == 2 and used_sessions[0] is not used_sessions[1]
        assert all(not session.in_transaction() for session in used_sessions)
        assert original_pool.checkedout() == 0
        with inspection_engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(datasets)) == 0
    assert request_engine.pool is not original_pool


def _assert_not_ready(client: TestClient, request_id: str) -> str:
    result = client.get("/health/ready", headers={"X-Request-ID": request_id})
    assert result.status_code == 503
    assert result.headers["X-Request-ID"] == request_id
    assert result.json() == {
        "error": {
            "code": "SERVICE_NOT_READY",
            "message": "服务尚未就绪",
            "request_id": request_id,
            "details": [],
        }
    }
    return result.text


def test_exported_app_readiness_checks_real_postgresql_heads(
    isolated_database: tuple[Settings, Engine],
    migrate: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, engine = isolated_database
    monkeypatch.setattr("backend.app.main.get_settings", lambda: settings)
    with TestClient(app) as client:
        assert not inspect(engine).has_table("alembic_version")
        _assert_not_ready(client, "schema-missing")
        migrate("head")
        answer = client.get("/health/ready")
        assert answer.status_code == 200
        assert answer.json() == {"status": "ready"}

        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = 'unknown_fixture'"))
        _assert_not_ready(client, "schema-unknown")
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0001_m1_core'"))

        migrate("base")
        assert inspect(engine).has_table("alembic_version")
        _assert_not_ready(client, "schema-behind")
        migrate("head")
        assert client.get("/health/ready").status_code == 200


def test_connection_failure_is_private_and_does_not_affect_liveness(
    isolated_database: tuple[Settings, Engine],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _ = isolated_database
    backend_logger = logging.getLogger("backend")
    monkeypatch.setattr(backend_logger, "handlers", [])
    monkeypatch.setattr(backend_logger, "level", backend_logger.level)
    monkeypatch.setattr(backend_logger, "propagate", backend_logger.propagate)
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        url = database_url(settings).set(host="127.0.0.1", port=unavailable.getsockname()[1])
        unreachable = Settings(
            app_env="test",
            log_level="INFO",
            database_url=url.difference_update_query(["host", "hostaddr", "port"]).render_as_string(
                hide_password=False
            ),
            cursor_signing_key=settings.cursor_signing_key,
            dev_auth_token=None,
            dev_auth_role=None,
        )
        with TestClient(create_app(settings=unreachable, enable_database_readiness=True)) as client:
            assert client.get("/health/live").status_code == 200
            response = _assert_not_ready(client, "connection-refused")
    captured = capsys.readouterr()
    visible = response + caplog.text + captured.out + captured.err
    assert unreachable.database_url.get_secret_value() not in visible
    assert "postgresql://" not in visible
    assert "postgresql+psycopg://" not in visible


def test_production_remains_closed_even_with_current_migrations(
    isolated_database: tuple[Settings, Engine], migrate: Callable[[str], None]
) -> None:
    settings, _ = isolated_database
    migrate("head")
    production = Settings(
        app_env="production",
        log_level="ERROR",
        database_url=settings.database_url,
        cursor_signing_key=settings.cursor_signing_key,
        dev_auth_token=None,
        dev_auth_role=None,
    )
    with TestClient(create_app(settings=production, enable_database_readiness=True)) as client:
        _assert_not_ready(client, "production-fail-closed")
