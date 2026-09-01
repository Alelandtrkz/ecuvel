"""add explicit product media derivatives and integrity

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _processed_count() -> int:
    return int(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM product_media WHERE "
                "content_sha256 IS NOT NULL OR processing_version IS NOT NULL OR "
                "thumbnail_storage_key IS NOT NULL OR thumbnail_media_type IS NOT NULL OR "
                "thumbnail_size_bytes IS NOT NULL OR thumbnail_width IS NOT NULL OR "
                "thumbnail_height IS NOT NULL OR thumbnail_sha256 IS NOT NULL"
            )
        )
        .scalar_one()
    )


def upgrade() -> None:
    op.add_column(
        "product_media",
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "product_media",
        sa.Column("thumbnail_storage_key", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "product_media",
        sa.Column("thumbnail_media_type", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "product_media",
        sa.Column("thumbnail_size_bytes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "product_media",
        sa.Column("thumbnail_width", sa.Integer(), nullable=True),
    )
    op.add_column(
        "product_media",
        sa.Column("thumbnail_height", sa.Integer(), nullable=True),
    )
    op.add_column(
        "product_media",
        sa.Column("thumbnail_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "product_media",
        sa.Column("processing_version", sa.SmallInteger(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_product_media_thumbnail_storage_key",
        "product_media",
        ["thumbnail_storage_key"],
    )
    op.create_check_constraint(
        "product_media_processed_state_complete",
        "product_media",
        "(content_sha256 IS NULL AND processing_version IS NULL "
        "AND thumbnail_storage_key IS NULL AND thumbnail_media_type IS NULL "
        "AND thumbnail_size_bytes IS NULL AND thumbnail_width IS NULL "
        "AND thumbnail_height IS NULL AND thumbnail_sha256 IS NULL) OR "
        "(content_sha256 IS NOT NULL AND processing_version IS NOT NULL "
        "AND media_type = 'image/webp' AND width IS NOT NULL AND height IS NOT NULL "
        "AND thumbnail_storage_key IS NOT NULL "
        "AND thumbnail_media_type = 'image/webp' "
        "AND thumbnail_size_bytes IS NOT NULL AND thumbnail_width IS NOT NULL "
        "AND thumbnail_height IS NOT NULL AND thumbnail_sha256 IS NOT NULL)",
    )
    op.create_check_constraint(
        "product_media_content_sha256_format",
        "product_media",
        "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "product_media_thumbnail_sha256_format",
        "product_media",
        "thumbnail_sha256 IS NULL OR thumbnail_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "product_media_thumbnail_size_positive",
        "product_media",
        "thumbnail_size_bytes IS NULL OR thumbnail_size_bytes > 0",
    )
    op.create_check_constraint(
        "product_media_thumbnail_dimensions_positive",
        "product_media",
        "thumbnail_width IS NULL OR (thumbnail_width > 0 AND thumbnail_height > 0)",
    )
    op.create_check_constraint(
        "product_media_processing_version_positive",
        "product_media",
        "processing_version IS NULL OR processing_version > 0",
    )


def downgrade() -> None:
    processed = _processed_count()
    if processed:
        raise RuntimeError(
            "product media derivatives exist; downgrade would discard verified metadata; "
            f"incompatible rows: {processed}"
        )
    op.drop_constraint(
        "product_media_processing_version_positive",
        "product_media",
        type_="check",
    )
    op.drop_constraint(
        "product_media_thumbnail_dimensions_positive",
        "product_media",
        type_="check",
    )
    op.drop_constraint(
        "product_media_thumbnail_size_positive",
        "product_media",
        type_="check",
    )
    op.drop_constraint(
        "product_media_thumbnail_sha256_format",
        "product_media",
        type_="check",
    )
    op.drop_constraint(
        "product_media_content_sha256_format",
        "product_media",
        type_="check",
    )
    op.drop_constraint(
        "product_media_processed_state_complete",
        "product_media",
        type_="check",
    )
    op.drop_constraint(
        "uq_product_media_thumbnail_storage_key",
        "product_media",
        type_="unique",
    )
    op.drop_column("product_media", "processing_version")
    op.drop_column("product_media", "thumbnail_sha256")
    op.drop_column("product_media", "thumbnail_height")
    op.drop_column("product_media", "thumbnail_width")
    op.drop_column("product_media", "thumbnail_size_bytes")
    op.drop_column("product_media", "thumbnail_media_type")
    op.drop_column("product_media", "thumbnail_storage_key")
    op.drop_column("product_media", "content_sha256")
