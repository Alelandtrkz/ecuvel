"""add partner product drafts

Revision ID: e1f2a3b4c5d6
Revises: d9f1a2b3c4e5
Create Date: 2026-06-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d9f1a2b3c4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


product_draft_status = postgresql.ENUM(
    "DRAFT",
    "INCOMPLETE",
    "READY_FOR_REVIEW",
    "SUBMITTED",
    "CHANGES_REQUESTED",
    "APPROVED",
    "REJECTED",
    name="product_draft_status",
    create_type=False,
)
product_draft_file_kind = postgresql.ENUM(
    "IMAGE",
    "DOCUMENT",
    name="product_draft_file_kind",
    create_type=False,
)
product_draft_file_status = postgresql.ENUM(
    "ACTIVE",
    "DELETED",
    name="product_draft_file_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (
        product_draft_status,
        product_draft_file_kind,
        product_draft_file_status,
    ):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "product_drafts",
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subcategory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=250), nullable=True),
        sa.Column("brand", sa.String(length=120), nullable=True),
        sa.Column("model_number", sa.String(length=120), nullable=True),
        sa.Column("seller_sku", sa.String(length=80), nullable=True),
        sa.Column("barcode", sa.String(length=80), nullable=True),
        sa.Column("condition", sa.String(length=40), nullable=True),
        sa.Column("country_origin", sa.String(length=80), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("package_contents", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("highlights", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("warranty_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("variants", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("pricing_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("inventory_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("dimensions_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("documents_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", product_draft_status, server_default="DRAFT", nullable=False),
        sa.Column("completion_percentage", sa.Integer(), server_default="0", nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "completion_percentage >= 0 AND completion_percentage <= 100",
            name="product_draft_completion_percentage_valid",
        ),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subcategory_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_id", "seller_sku", name="uq_product_drafts_store_sku"),
    )
    op.create_index(op.f("ix_product_drafts_category_id"), "product_drafts", ["category_id"], unique=False)
    op.create_index(op.f("ix_product_drafts_created_by_user_id"), "product_drafts", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_product_drafts_status"), "product_drafts", ["status"], unique=False)
    op.create_index(op.f("ix_product_drafts_store_id"), "product_drafts", ["store_id"], unique=False)
    op.create_index(op.f("ix_product_drafts_subcategory_id"), "product_drafts", ["subcategory_id"], unique=False)
    op.create_index(op.f("ix_product_drafts_submitted_at"), "product_drafts", ["submitted_at"], unique=False)
    op.create_index(op.f("ix_product_drafts_template_key"), "product_drafts", ["template_key"], unique=False)

    op.create_table(
        "product_draft_files",
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", product_draft_file_kind, nullable=False),
        sa.Column("status", product_draft_file_status, server_default="ACTIVE", nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_cover", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("document_type", sa.String(length=80), nullable=True),
        sa.Column("label", sa.String(length=160), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("position >= 0", name="product_draft_file_position_nonnegative"),
        sa.CheckConstraint("size_bytes > 0", name="product_draft_file_size_positive"),
        sa.ForeignKeyConstraint(["draft_id"], ["product_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(op.f("ix_product_draft_files_draft_id"), "product_draft_files", ["draft_id"], unique=False)
    op.create_index(op.f("ix_product_draft_files_kind"), "product_draft_files", ["kind"], unique=False)
    op.create_index(op.f("ix_product_draft_files_status"), "product_draft_files", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_product_draft_files_status"), table_name="product_draft_files")
    op.drop_index(op.f("ix_product_draft_files_kind"), table_name="product_draft_files")
    op.drop_index(op.f("ix_product_draft_files_draft_id"), table_name="product_draft_files")
    op.drop_table("product_draft_files")

    op.drop_index(op.f("ix_product_drafts_template_key"), table_name="product_drafts")
    op.drop_index(op.f("ix_product_drafts_submitted_at"), table_name="product_drafts")
    op.drop_index(op.f("ix_product_drafts_subcategory_id"), table_name="product_drafts")
    op.drop_index(op.f("ix_product_drafts_store_id"), table_name="product_drafts")
    op.drop_index(op.f("ix_product_drafts_status"), table_name="product_drafts")
    op.drop_index(op.f("ix_product_drafts_created_by_user_id"), table_name="product_drafts")
    op.drop_index(op.f("ix_product_drafts_category_id"), table_name="product_drafts")
    op.drop_table("product_drafts")

    bind = op.get_bind()
    for enum in (
        product_draft_file_status,
        product_draft_file_kind,
        product_draft_status,
    ):
        enum.drop(bind, checkfirst=True)
