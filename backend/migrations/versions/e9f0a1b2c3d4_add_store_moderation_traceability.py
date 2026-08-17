"""add store moderation traceability

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "store_verification_reviews",
        sa.Column("issues_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "store_verification_reviews",
        sa.Column("checklist_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "store_onboarding_documents",
        sa.Column("replaces_document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_store_onboarding_documents_replaces_document_id_store_onboarding_documents"),
        "store_onboarding_documents",
        "store_onboarding_documents",
        ["replaces_document_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_store_onboarding_documents_replaces_document_id"),
        "store_onboarding_documents",
        ["replaces_document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_store_onboarding_documents_replaces_document_id"),
        table_name="store_onboarding_documents",
    )
    op.drop_constraint(
        op.f("fk_store_onboarding_documents_replaces_document_id_store_onboarding_documents"),
        "store_onboarding_documents",
        type_="foreignkey",
    )
    op.drop_column("store_onboarding_documents", "replaces_document_id")
    op.drop_column("store_verification_reviews", "checklist_snapshot")
    op.drop_column("store_verification_reviews", "issues_snapshot")
