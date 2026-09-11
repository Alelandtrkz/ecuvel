"""add legal versioning and user evidence foundation

Revision ID: 9e5f7a8b0c1d
Revises: 8d4e5f6a7b9c
Create Date: 2026-09-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "9e5f7a8b0c1d"
down_revision: Union[str, None] = "8d4e5f6a7b9c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGAL_ACCEPTANCE_MODE = postgresql.ENUM(
    "NONE",
    "ACCEPTED",
    "ACKNOWLEDGED",
    name="legal_acceptance_mode",
    create_type=False,
)
LEGAL_ACCEPTANCE_ACTION = postgresql.ENUM(
    "ACCEPTED",
    "ACKNOWLEDGED",
    name="legal_acceptance_action",
    create_type=False,
)
LEGAL_ACCEPTANCE_SOURCE = postgresql.ENUM(
    "REGISTER",
    "CHECKOUT",
    "ACCOUNT_RECONSENT",
    "PRIVACY_NOTICE",
    name="legal_acceptance_source",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    LEGAL_ACCEPTANCE_MODE.create(bind, checkfirst=True)
    LEGAL_ACCEPTANCE_ACTION.create(bind, checkfirst=True)
    LEGAL_ACCEPTANCE_SOURCE.create(bind, checkfirst=True)

    op.create_table(
        "legal_document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family", sa.String(length=40), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("version_prefix", sa.String(length=80), nullable=False),
        sa.Column("version_identifier", sa.String(length=140), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title_snapshot", sa.String(length=200), nullable=False),
        sa.Column("template_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("content_snapshot", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("acceptance_mode", LEGAL_ACCEPTANCE_MODE, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_revision", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="version_number_positive",
        ),
        sa.CheckConstraint(
            "char_length(btrim(family)) BETWEEN 1 AND 40",
            name="family_nonblank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(slug)) BETWEEN 1 AND 100",
            name="slug_nonblank",
        ),
        sa.CheckConstraint(
            "version_prefix ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name="prefix_format",
        ),
        sa.CheckConstraint(
            "version_identifier ~ "
            "'^[a-z0-9]+(-[a-z0-9]+)*-[0-9]{4}-[0-9]{2}-[0-9]{2}-v[1-9][0-9]*$'",
            name="identifier_format",
        ),
        sa.CheckConstraint(
            "version_identifier ~ "
            "('^' || version_prefix || "
            "'-[0-9]{4}-[0-9]{2}-[0-9]{2}-v' || version_number::text || '$')",
            name="identifier_matches_fields",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_format",
        ),
        sa.CheckConstraint(
            "char_length(content_snapshot) > 0",
            name="content_nonblank",
        ),
        sa.CheckConstraint(
            "effective_at >= published_at",
            name="effective_after_publication",
        ),
        sa.CheckConstraint(
            "source_revision IS NULL OR "
            "char_length(btrim(source_revision)) BETWEEN 1 AND 64",
            name="source_revision_nonblank",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "family",
            "slug",
            "version_number",
            name="uq_legal_document_version_logical_number",
        ),
        sa.UniqueConstraint("version_identifier"),
    )
    op.create_index(
        "ix_legal_document_versions_current_lookup",
        "legal_document_versions",
        ["family", "slug", "effective_at", "published_at"],
    )

    op.create_table(
        "user_legal_acceptances",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "legal_document_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("action", LEGAL_ACCEPTANCE_ACTION, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", LEGAL_ACCEPTANCE_SOURCE, nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column(
            "version_identifier_snapshot", sa.String(length=140), nullable=False
        ),
        sa.Column("content_sha256_snapshot", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(version_identifier_snapshot) BETWEEN 1 AND 140",
            name="identifier_nonblank",
        ),
        sa.CheckConstraint(
            "content_sha256_snapshot ~ '^[0-9a-f]{64}$'",
            name="sha256_format",
        ),
        sa.CheckConstraint(
            "ip_address IS NULL OR char_length(ip_address) BETWEEN 2 AND 45",
            name="ip_length",
        ),
        sa.CheckConstraint(
            "user_agent IS NULL OR char_length(user_agent) BETWEEN 1 AND 500",
            name="user_agent_length",
        ),
        sa.ForeignKeyConstraint(
            ["legal_document_version_id"],
            ["legal_document_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "legal_document_version_id",
            name="uq_user_legal_acceptance_user_version",
        ),
    )
    op.create_index(
        "ix_user_legal_acceptances_legal_document_version_id",
        "user_legal_acceptances",
        ["legal_document_version_id"],
    )

    op.execute(
        """
        CREATE FUNCTION prevent_legal_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only legal evidence', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name in ("legal_document_versions", "user_legal_acceptances"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_legal_evidence_mutation()
            """
        )


def downgrade() -> None:
    op.drop_table("user_legal_acceptances")
    op.drop_table("legal_document_versions")
    op.execute("DROP FUNCTION prevent_legal_evidence_mutation()")
    LEGAL_ACCEPTANCE_SOURCE.drop(op.get_bind(), checkfirst=True)
    LEGAL_ACCEPTANCE_ACTION.drop(op.get_bind(), checkfirst=True)
    LEGAL_ACCEPTANCE_MODE.drop(op.get_bind(), checkfirst=True)
