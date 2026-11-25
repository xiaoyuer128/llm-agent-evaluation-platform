"""The five core relations introduced in the first M1 migration."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("name"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_nonblank"),
        CheckConstraint("kind IN ('llm', 'agent')", name="kind"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(5))
    description: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id", "version_no", name="uq_dataset_versions_dataset_id_version_no"
        ),
        CheckConstraint("version_no > 0", name="version_no_positive"),
        CheckConstraint("item_count >= 0", name="item_count_nonnegative"),
        CheckConstraint("status IN ('draft', 'published')", name="status"),
        CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="checksum_sha256"),
        CheckConstraint(
            "(status <> 'draft' OR (published_at IS NULL AND checksum IS NULL)) AND "
            "(status <> 'published' OR (published_at IS NOT NULL AND checksum IS NOT NULL "
            "AND item_count > 0))",
            name="publication",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"))
    version_no: Mapped[int]
    status: Mapped[str] = mapped_column(String(9), server_default="draft")
    checksum: Mapped[str | None] = mapped_column(String(64))
    item_count: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetItem(Base):
    __tablename__ = "dataset_items"
    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id", "case_key", name="uq_dataset_items_dataset_version_id_case_key"
        ),
        UniqueConstraint(
            "dataset_version_id", "ordinal", name="uq_dataset_items_dataset_version_id_ordinal"
        ),
        UniqueConstraint("id", "dataset_version_id", name="uq_dataset_items_id_dataset_version_id"),
        CheckConstraint("char_length(btrim(case_key)) > 0", name="case_key_nonblank"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        CheckConstraint("jsonb_typeof(input) = 'object'", name="input_object"),
        CheckConstraint("jsonb_typeof(expectation) = 'object'", name="expectation_object"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    dataset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT")
    )
    case_key: Mapped[str] = mapped_column(String(128))
    ordinal: Mapped[int]
    input: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    expectation: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ModelConfig(Base):
    __tablename__ = "model_configs"
    __table_args__ = (
        UniqueConstraint("config_key", "revision", name="uq_model_configs_config_key_revision"),
        CheckConstraint("char_length(btrim(config_key)) > 0", name="config_key_nonblank"),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("kind IN ('llm', 'agent')", name="kind"),
        CheckConstraint("char_length(btrim(adapter_key)) > 0", name="adapter_key_nonblank"),
        CheckConstraint("char_length(btrim(provider)) > 0", name="provider_nonblank"),
        CheckConstraint("char_length(btrim(model_name)) > 0", name="model_name_nonblank"),
        CheckConstraint(
            "char_length(btrim(provider_endpoint_ref)) > 0", name="provider_endpoint_ref_nonblank"
        ),
        CheckConstraint("jsonb_typeof(parameters) = 'object'", name="parameters_object"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    config_key: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int]
    kind: Mapped[str] = mapped_column(String(5))
    adapter_key: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(128))
    model_name: Mapped[str] = mapped_column(String(128))
    provider_endpoint_ref: Mapped[str] = mapped_column(String(128))
    # This column is for a server-side reference. Secret values remain outside the schema.
    credential_ref: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True), server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("char_length(btrim(action)) > 0", name="action_nonblank"),
        CheckConstraint("char_length(btrim(resource_type)) > 0", name="resource_type_nonblank"),
        CheckConstraint("char_length(btrim(request_id)) > 0", name="request_id_nonblank"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata_object"),
        Index(
            "ix_audit_events_resource_created_at",
            "resource_type",
            "resource_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    actor_id: Mapped[UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(128))
    resource_type: Mapped[str] = mapped_column(String(128))
    resource_id: Mapped[UUID] = mapped_column(Uuid)
    request_id: Mapped[str] = mapped_column(String(128))
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB(none_as_null=True), server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
