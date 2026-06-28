"""add customer favorites

Revision ID: a4c9e0d1b2f3
Revises: f2b7c1d4e8a9
Create Date: 2026-06-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a4c9e0d1b2f3"
down_revision: Union[str, None] = "f2b7c1d4e8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "favorites",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_favorites_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_favorites_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_favorites")),
        sa.UniqueConstraint(
            "user_id",
            "product_id",
            name="uq_favorites_user_product",
        ),
    )
    op.create_index(
        op.f("ix_favorites_product_id"),
        "favorites",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "ix_favorites_product_created_at",
        "favorites",
        ["product_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_favorites_user_id"),
        "favorites",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_favorites_user_created_at",
        "favorites",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_favorites_user_created_at", table_name="favorites")
    op.drop_index(op.f("ix_favorites_user_id"), table_name="favorites")
    op.drop_index("ix_favorites_product_created_at", table_name="favorites")
    op.drop_index(op.f("ix_favorites_product_id"), table_name="favorites")
    op.drop_table("favorites")
