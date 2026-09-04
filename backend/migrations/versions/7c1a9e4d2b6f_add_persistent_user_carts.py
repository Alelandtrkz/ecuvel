"""add persistent user carts and adoption receipts

Revision ID: 7c1a9e4d2b6f
Revises: 6499defb2c52
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c1a9e4d2b6f"
down_revision: Union[str, None] = "6499defb2c52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _row_count(table_name: str) -> int:
    return int(
        op.get_bind()
        .execute(sa.text(f"SELECT count(*) FROM {table_name}"))
        .scalar_one()
    )


def upgrade() -> None:
    op.create_table(
        "carts",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
            ["user_id"],
            ["users.id"],
            name=op.f("fk_carts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_carts")),
    )
    op.create_index(op.f("ix_carts_user_id"), "carts", ["user_id"], unique=True)

    op.create_table(
        "cart_adoptions",
        sa.Column("merge_token", sa.String(length=64), nullable=False),
        sa.Column("claimed_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claimed_user_id"],
            ["users.id"],
            name=op.f("fk_cart_adoptions_claimed_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cart_adoptions")),
        sa.UniqueConstraint(
            "merge_token",
            name=op.f("uq_cart_adoptions_merge_token"),
        ),
    )
    op.create_index(
        op.f("ix_cart_adoptions_claimed_user_id"),
        "cart_adoptions",
        ["claimed_user_id"],
        unique=False,
    )

    op.create_table(
        "cart_items",
        sa.Column("cart_id", sa.UUID(), nullable=False),
        sa.Column("seller_offer_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("selected", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
            "quantity BETWEEN 1 AND 99",
            name=op.f("ck_cart_items_cart_item_quantity_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["cart_id"],
            ["carts.id"],
            name=op.f("fk_cart_items_cart_id_carts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["seller_offer_id"],
            ["seller_offers.id"],
            name=op.f("fk_cart_items_seller_offer_id_seller_offers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cart_items")),
        sa.UniqueConstraint(
            "cart_id",
            "seller_offer_id",
            name="uq_cart_items_cart_offer",
        ),
    )
    op.create_index(op.f("ix_cart_items_cart_id"), "cart_items", ["cart_id"])
    op.create_index(
        op.f("ix_cart_items_seller_offer_id"),
        "cart_items",
        ["seller_offer_id"],
    )


def downgrade() -> None:
    populated = {
        table_name: _row_count(table_name)
        for table_name in ("cart_items", "carts", "cart_adoptions")
    }
    if any(populated.values()):
        details = ", ".join(
            f"{table_name}={count}"
            for table_name, count in populated.items()
            if count
        )
        raise RuntimeError(
            "persistent cart data exists; downgrade would discard carts or "
            f"replay protection; incompatible rows: {details}"
        )

    op.drop_table("cart_items")
    op.drop_table("cart_adoptions")
    op.drop_table("carts")
