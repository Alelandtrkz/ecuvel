"""add verified product reviews

Revision ID: c7e2b9f4a1d0
Revises: a4c9e0d1b2f3
Create Date: 2026-06-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c7e2b9f4a1d0"
down_revision: Union[str, None] = "a4c9e0d1b2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


product_review_status = postgresql.ENUM(
    "PENDING_REVIEW",
    "PUBLISHED",
    "REJECTED",
    name="product_review_status",
    create_type=False,
)


def upgrade() -> None:
    product_review_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "product_reviews",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status",
            product_review_status,
            server_default="PENDING_REVIEW",
            nullable=False,
        ),
        sa.Column("public_rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "moderated_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("moderation_notes", sa.String(length=1000), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rating >= 1 AND rating <= 5",
            name="product_review_rating_range",
        ),
        sa.CheckConstraint(
            "char_length(body) >= 1 AND char_length(body) <= 2000",
            name="product_review_body_length",
        ),
        sa.ForeignKeyConstraint(
            ["moderated_by_user_id"],
            ["users.id"],
            name=op.f("fk_product_reviews_moderated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_product_reviews_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id"],
            ["order_items.id"],
            name=op.f("fk_product_reviews_order_item_id_order_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_reviews_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_product_reviews_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_reviews")),
        sa.UniqueConstraint(
            "user_id",
            "order_item_id",
            name="uq_product_reviews_user_order_item",
        ),
    )
    op.create_index(op.f("ix_product_reviews_user_id"), "product_reviews", ["user_id"])
    op.create_index(op.f("ix_product_reviews_order_id"), "product_reviews", ["order_id"])
    op.create_index(op.f("ix_product_reviews_order_item_id"), "product_reviews", ["order_item_id"])
    op.create_index(op.f("ix_product_reviews_product_id"), "product_reviews", ["product_id"])
    op.create_index(op.f("ix_product_reviews_status"), "product_reviews", ["status"])
    op.create_index(op.f("ix_product_reviews_published_at"), "product_reviews", ["published_at"])
    op.create_index(
        "ix_product_reviews_product_status_published",
        "product_reviews",
        ["product_id", "status", "published_at"],
    )
    op.create_index(
        op.f("ix_product_reviews_moderated_by_user_id"),
        "product_reviews",
        ["moderated_by_user_id"],
    )

    op.create_table(
        "product_review_images",
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=50), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "size_bytes > 0",
            name="product_review_image_size_positive",
        ),
        sa.CheckConstraint(
            "width > 0 AND height > 0",
            name="product_review_image_dimensions_positive",
        ),
        sa.CheckConstraint(
            "sort_order >= 0 AND sort_order < 5",
            name="product_review_image_sort_range",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["product_reviews.id"],
            name=op.f("fk_product_review_images_review_id_product_reviews"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_review_images")),
        sa.UniqueConstraint("public_id", name=op.f("uq_product_review_images_public_id")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_product_review_images_storage_key")),
        sa.UniqueConstraint(
            "review_id",
            "sort_order",
            name="uq_product_review_images_review_sort",
        ),
    )
    op.create_index(
        op.f("ix_product_review_images_review_id"),
        "product_review_images",
        ["review_id"],
    )
    op.create_index(
        op.f("ix_product_review_images_public_id"),
        "product_review_images",
        ["public_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_product_review_images_public_id"), table_name="product_review_images")
    op.drop_index(op.f("ix_product_review_images_review_id"), table_name="product_review_images")
    op.drop_table("product_review_images")
    op.drop_index(op.f("ix_product_reviews_moderated_by_user_id"), table_name="product_reviews")
    op.drop_index("ix_product_reviews_product_status_published", table_name="product_reviews")
    op.drop_index(op.f("ix_product_reviews_published_at"), table_name="product_reviews")
    op.drop_index(op.f("ix_product_reviews_status"), table_name="product_reviews")
    op.drop_index(op.f("ix_product_reviews_product_id"), table_name="product_reviews")
    op.drop_index(op.f("ix_product_reviews_order_item_id"), table_name="product_reviews")
    op.drop_index(op.f("ix_product_reviews_order_id"), table_name="product_reviews")
    op.drop_index(op.f("ix_product_reviews_user_id"), table_name="product_reviews")
    op.drop_table("product_reviews")
    product_review_status.drop(op.get_bind(), checkfirst=True)
