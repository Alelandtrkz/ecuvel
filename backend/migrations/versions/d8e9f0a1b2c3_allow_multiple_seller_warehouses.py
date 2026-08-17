"""allow multiple seller warehouses with one explicit default location

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-08-16 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Early beta databases may already have applied the original c7 migration.
    # Warehouse ownership is intentionally one-to-many; the unambiguous default
    # remains enforced by uq_store_inventory_default_location.
    op.execute(sa.text("DROP INDEX IF EXISTS uq_warehouses_seller_store"))


def downgrade() -> None:
    # Do not reintroduce a one-warehouse-per-store restriction. The preceding
    # migration no longer creates it on fresh databases either.
    pass
