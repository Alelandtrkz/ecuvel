"""add public sequential identifiers

Revision ID: f3c4d5e6a7b8
Revises: e1f2a3b4c5d6
Create Date: 2026-06-28 00:00:00.000000

"""
from __future__ import annotations

import re
import unicodedata
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f3c4d5e6a7b8"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("CREATE SEQUENCE IF NOT EXISTS user_registration_number_seq")
    op.add_column(
        "users",
        sa.Column("registration_number", sa.BigInteger(), nullable=True),
    )
    op.execute(
        """
        WITH numbered AS (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS number
            FROM users
        )
        UPDATE users
        SET registration_number = numbered.number
        FROM numbered
        WHERE users.id = numbered.id
        """
    )
    op.execute(
        """
        SELECT setval(
            'user_registration_number_seq',
            GREATEST(COALESCE((SELECT max(registration_number) FROM users), 0), 1),
            COALESCE((SELECT max(registration_number) FROM users), 0) > 0
        )
        """
    )
    op.alter_column(
        "users",
        "registration_number",
        nullable=False,
        server_default=sa.text("nextval('user_registration_number_seq'::regclass)"),
    )
    op.create_unique_constraint(
        "uq_users_registration_number",
        "users",
        ["registration_number"],
    )
    op.create_check_constraint(
        "ck_users_registration_number_positive",
        "users",
        "registration_number > 0",
    )
    op.create_index(
        op.f("ix_users_registration_number"),
        "users",
        ["registration_number"],
        unique=False,
    )

    op.execute("CREATE SEQUENCE IF NOT EXISTS store_registration_number_seq")
    op.add_column(
        "stores",
        sa.Column("registration_number", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "stores",
        sa.Column("product_code_prefix", sa.String(length=3), nullable=True),
    )
    op.execute(
        """
        WITH numbered AS (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS number
            FROM stores
        )
        UPDATE stores
        SET registration_number = numbered.number
        FROM numbered
        WHERE stores.id = numbered.id
        """
    )
    rows = bind.execute(
        sa.text(
            """
            SELECT id, name, legal_name, public_code
            FROM stores
            ORDER BY created_at, id
            """
        )
    )
    for row in rows:
        prefix = _normalize_store_prefix(row.name or row.legal_name or row.public_code)
        bind.execute(
            sa.text(
                """
                UPDATE stores
                SET product_code_prefix = :prefix
                WHERE id = CAST(:store_id AS uuid)
                """
            ),
            {"prefix": prefix, "store_id": str(row.id)},
        )
    op.execute(
        """
        SELECT setval(
            'store_registration_number_seq',
            GREATEST(COALESCE((SELECT max(registration_number) FROM stores), 0), 1),
            COALESCE((SELECT max(registration_number) FROM stores), 0) > 0
        )
        """
    )
    op.alter_column(
        "stores",
        "registration_number",
        nullable=False,
        server_default=sa.text("nextval('store_registration_number_seq'::regclass)"),
    )
    op.alter_column("stores", "product_code_prefix", nullable=False)
    op.create_unique_constraint(
        "uq_stores_registration_number",
        "stores",
        ["registration_number"],
    )
    op.create_check_constraint(
        "ck_stores_registration_number_positive",
        "stores",
        "registration_number > 0",
    )
    op.create_check_constraint(
        "ck_stores_product_code_prefix_format",
        "stores",
        "product_code_prefix ~ '^[A-Z0-9]{3}$'",
    )
    op.create_index(
        op.f("ix_stores_registration_number"),
        "stores",
        ["registration_number"],
        unique=False,
    )

    op.create_table(
        "store_product_counters",
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_value", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "last_value >= 0 AND last_value <= 999999",
            name="ck_store_product_counters_last_value_range",
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("store_id"),
    )

    op.execute(
        """
        UPDATE product_drafts
        SET condition = 'NEW'
        WHERE condition IS DISTINCT FROM 'NEW'
        """
    )
    op.execute(
        """
        UPDATE product_drafts
        SET barcode = seller_sku
        WHERE seller_sku IS NOT NULL
        """
    )
    op.create_unique_constraint(
        "uq_product_drafts_seller_sku",
        "product_drafts",
        ["seller_sku"],
    )
    op.create_check_constraint(
        "ck_product_drafts_condition_new_only",
        "product_drafts",
        "condition IS NULL OR condition = 'NEW'",
    )
    op.create_check_constraint(
        "ck_product_drafts_barcode_matches_seller_sku",
        "product_drafts",
        "barcode IS NULL OR seller_sku IS NULL OR barcode = seller_sku",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_product_drafts_barcode_matches_seller_sku",
        "product_drafts",
        type_="check",
    )
    op.drop_constraint(
        "ck_product_drafts_condition_new_only",
        "product_drafts",
        type_="check",
    )
    op.drop_constraint(
        "uq_product_drafts_seller_sku",
        "product_drafts",
        type_="unique",
    )

    op.drop_table("store_product_counters")

    op.drop_index(op.f("ix_stores_registration_number"), table_name="stores")
    op.drop_constraint(
        "ck_stores_product_code_prefix_format",
        "stores",
        type_="check",
    )
    op.drop_constraint(
        "ck_stores_registration_number_positive",
        "stores",
        type_="check",
    )
    op.drop_constraint(
        "uq_stores_registration_number",
        "stores",
        type_="unique",
    )
    op.drop_column("stores", "product_code_prefix")
    op.drop_column("stores", "registration_number")
    op.execute("DROP SEQUENCE IF EXISTS store_registration_number_seq")

    op.drop_index(op.f("ix_users_registration_number"), table_name="users")
    op.drop_constraint(
        "ck_users_registration_number_positive",
        "users",
        type_="check",
    )
    op.drop_constraint(
        "uq_users_registration_number",
        "users",
        type_="unique",
    )
    op.drop_column("users", "registration_number")
    op.execute("DROP SEQUENCE IF EXISTS user_registration_number_seq")


def _normalize_store_prefix(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").upper()
    letters = re.findall(r"[A-Z0-9]", ascii_text)
    if not letters:
        return "ECU"
    return ("".join(letters) + "XXX")[:3]
