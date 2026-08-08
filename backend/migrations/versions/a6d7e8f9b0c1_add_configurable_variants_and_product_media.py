"""add configurable variants and product media

Revision ID: a6d7e8f9b0c1
Revises: f3c4d5e6a7b8
Create Date: 2026-08-05 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a6d7e8f9b0c1"
down_revision: Union[str, None] = "f3c4d5e6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "product_drafts",
        sa.Column(
            "variant_configuration",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("product_draft_files", sa.Column("variant_axis_key", sa.String(80), nullable=True))
    op.add_column("product_draft_files", sa.Column("variant_value_key", sa.String(120), nullable=True))
    op.create_index("ix_product_draft_files_variant_axis_key", "product_draft_files", ["variant_axis_key"])
    op.create_index("ix_product_draft_files_variant_value_key", "product_draft_files", ["variant_value_key"])
    op.create_check_constraint(
        "product_draft_file_variant_binding_complete",
        "product_draft_files",
        "(variant_axis_key IS NULL) = (variant_value_key IS NULL)",
    )

    op.add_column(
        "products",
        sa.Column(
            "variant_configuration",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("product_variants", sa.Column("combination_key", sa.String(500), nullable=True))
    op.create_index("ix_product_variants_combination_key", "product_variants", ["combination_key"])
    op.create_unique_constraint(
        "uq_product_variants_product_combination",
        "product_variants",
        ["product_id", "combination_key"],
    )

    op.create_table(
        "product_media",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("media_type", sa.String(80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_cover", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("variant_axis_key", sa.String(80), nullable=True),
        sa.Column("variant_value_key", sa.String(120), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("size_bytes > 0", name="product_media_size_positive"),
        sa.CheckConstraint("position >= 0", name="product_media_position_nonnegative"),
        sa.CheckConstraint(
            "(variant_axis_key IS NULL) = (variant_value_key IS NULL)",
            name="product_media_variant_binding_complete",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_product_media_product_id", "product_media", ["product_id"])
    op.create_index("ix_product_media_public_id", "product_media", ["public_id"], unique=True)
    op.create_index("ix_product_media_variant_axis_key", "product_media", ["variant_axis_key"])
    op.create_index("ix_product_media_variant_value_key", "product_media", ["variant_value_key"])


def downgrade() -> None:
    op.drop_index("ix_product_media_variant_value_key", table_name="product_media")
    op.drop_index("ix_product_media_variant_axis_key", table_name="product_media")
    op.drop_index("ix_product_media_public_id", table_name="product_media")
    op.drop_index("ix_product_media_product_id", table_name="product_media")
    op.drop_table("product_media")

    op.drop_constraint("uq_product_variants_product_combination", "product_variants", type_="unique")
    op.drop_index("ix_product_variants_combination_key", table_name="product_variants")
    op.drop_column("product_variants", "combination_key")
    op.drop_column("products", "variant_configuration")

    op.drop_constraint(
        "product_draft_file_variant_binding_complete",
        "product_draft_files",
        type_="check",
    )
    op.drop_index("ix_product_draft_files_variant_value_key", table_name="product_draft_files")
    op.drop_index("ix_product_draft_files_variant_axis_key", table_name="product_draft_files")
    op.drop_column("product_draft_files", "variant_value_key")
    op.drop_column("product_draft_files", "variant_axis_key")
    op.drop_column("product_drafts", "variant_configuration")
