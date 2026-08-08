"""add partner product review replies

Revision ID: b7c8d9e0f1a2
Revises: a6d7e8f9b0c1
Create Date: 2026-08-08 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a6d7e8f9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_review_replies",
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            "char_length(btrim(body)) >= 1 AND char_length(body) <= 500",
            name="product_review_reply_body_length",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_product_review_replies_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["product_reviews.id"],
            name=op.f("fk_product_review_replies_review_id_product_reviews"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_product_review_replies_store_id_stores"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_product_review_replies_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_review_replies")),
        sa.UniqueConstraint("review_id", name=op.f("uq_product_review_replies_review_id")),
    )
    op.create_index(
        op.f("ix_product_review_replies_review_id"),
        "product_review_replies",
        ["review_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_product_review_replies_store_id"),
        "product_review_replies",
        ["store_id"],
    )
    op.create_index(
        op.f("ix_product_review_replies_created_by_user_id"),
        "product_review_replies",
        ["created_by_user_id"],
    )
    op.create_index(
        op.f("ix_product_review_replies_updated_by_user_id"),
        "product_review_replies",
        ["updated_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_product_review_replies_updated_by_user_id"),
        table_name="product_review_replies",
    )
    op.drop_index(
        op.f("ix_product_review_replies_created_by_user_id"),
        table_name="product_review_replies",
    )
    op.drop_index(
        op.f("ix_product_review_replies_store_id"),
        table_name="product_review_replies",
    )
    op.drop_index(
        op.f("ix_product_review_replies_review_id"),
        table_name="product_review_replies",
    )
    op.drop_table("product_review_replies")
