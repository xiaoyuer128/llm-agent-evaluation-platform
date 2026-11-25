"""Create the five core M1 tables.

Revision ID: 0001_m1_core
Revises: None
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_m1_core"
down_revision: None = None
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(5), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("char_length(btrim(name)) > 0", name=op.f("ck_datasets_name_nonblank")),
        sa.CheckConstraint("kind IN ('llm', 'agent')", name=op.f("ck_datasets_kind")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasets")),
        sa.UniqueConstraint("name", name=op.f("uq_datasets_name")),
    )
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(9), server_default="draft", nullable=False),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("item_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version_no > 0", name=op.f("ck_dataset_versions_version_no_positive")),
        sa.CheckConstraint(
            "item_count >= 0", name=op.f("ck_dataset_versions_item_count_nonnegative")
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published')", name=op.f("ck_dataset_versions_status")
        ),
        sa.CheckConstraint(
            "checksum ~ '^[0-9a-f]{64}$'", name=op.f("ck_dataset_versions_checksum_sha256")
        ),
        sa.CheckConstraint(
            "(status <> 'draft' OR (published_at IS NULL AND checksum IS NULL)) AND "
            "(status <> 'published' OR (published_at IS NOT NULL AND checksum IS NOT NULL "
            "AND item_count > 0))",
            name=op.f("ck_dataset_versions_publication"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_dataset_versions_dataset_id_datasets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_versions")),
        sa.UniqueConstraint(
            "dataset_id", "version_no", name=op.f("uq_dataset_versions_dataset_id_version_no")
        ),
    )
    op.create_table(
        "dataset_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("case_key", sa.String(128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("input", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("expectation", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(case_key)) > 0", name=op.f("ck_dataset_items_case_key_nonblank")
        ),
        sa.CheckConstraint("ordinal >= 0", name=op.f("ck_dataset_items_ordinal_nonnegative")),
        sa.CheckConstraint(
            "jsonb_typeof(input) = 'object'", name=op.f("ck_dataset_items_input_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(expectation) = 'object'", name=op.f("ck_dataset_items_expectation_object")
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_dataset_items_dataset_version_id_dataset_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_items")),
        sa.UniqueConstraint(
            "dataset_version_id",
            "case_key",
            name=op.f("uq_dataset_items_dataset_version_id_case_key"),
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "ordinal",
            name=op.f("uq_dataset_items_dataset_version_id_ordinal"),
        ),
        sa.UniqueConstraint(
            "id", "dataset_version_id", name=op.f("uq_dataset_items_id_dataset_version_id")
        ),
    )
    op.create_table(
        "model_configs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("config_key", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(5), nullable=False),
        sa.Column("adapter_key", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("provider_endpoint_ref", sa.String(128), nullable=False),
        sa.Column("credential_ref", sa.Text(), nullable=True),
        sa.Column(
            "parameters",
            postgresql.JSONB(none_as_null=True),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(btrim(config_key)) > 0", name=op.f("ck_model_configs_config_key_nonblank")
        ),
        sa.CheckConstraint("revision > 0", name=op.f("ck_model_configs_revision_positive")),
        sa.CheckConstraint("kind IN ('llm', 'agent')", name=op.f("ck_model_configs_kind")),
        sa.CheckConstraint(
            "char_length(btrim(adapter_key)) > 0",
            name=op.f("ck_model_configs_adapter_key_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(provider)) > 0", name=op.f("ck_model_configs_provider_nonblank")
        ),
        sa.CheckConstraint(
            "char_length(btrim(model_name)) > 0", name=op.f("ck_model_configs_model_name_nonblank")
        ),
        sa.CheckConstraint(
            "char_length(btrim(provider_endpoint_ref)) > 0",
            name=op.f("ck_model_configs_provider_endpoint_ref_nonblank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parameters) = 'object'", name=op.f("ck_model_configs_parameters_object")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_configs")),
        sa.UniqueConstraint(
            "config_key", "revision", name=op.f("uq_model_configs_config_key_revision")
        ),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(none_as_null=True),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(action)) > 0", name=op.f("ck_audit_events_action_nonblank")
        ),
        sa.CheckConstraint(
            "char_length(btrim(resource_type)) > 0",
            name=op.f("ck_audit_events_resource_type_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(request_id)) > 0", name=op.f("ck_audit_events_request_id_nonblank")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'", name=op.f("ck_audit_events_metadata_object")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_resource_created_at",
        "audit_events",
        ["resource_type", "resource_id", "created_at", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_audit_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only' USING ERRCODE = '23000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_event_mutation()
        """
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION reject_audit_event_mutation()")
    op.drop_table("model_configs")
    op.drop_table("dataset_items")
    op.drop_table("dataset_versions")
    op.drop_table("datasets")
