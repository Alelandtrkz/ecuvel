"""add seller offer preparation time

Revision ID: da1b2c3d4e5f
Revises: c9d0e1f2a3b4
Create Date: 2026-09-01 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "da1b2c3d4e5f"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _populated_offer_count() -> int:
    return int(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM seller_offers "
                "WHERE preparation_time_days IS NOT NULL"
            )
        )
        .scalar_one()
    )


def upgrade() -> None:
    op.add_column(
        "seller_offers",
        sa.Column("preparation_time_days", sa.SmallInteger(), nullable=True),
    )
    op.create_check_constraint(
        "seller_offer_preparation_time_valid",
        "seller_offers",
        "preparation_time_days IS NULL "
        "OR preparation_time_days BETWEEN 1 AND 2",
    )


def downgrade() -> None:
    populated = _populated_offer_count()
    if populated:
        raise RuntimeError(
            "seller offer preparation times exist; downgrade would discard "
            f"productive delivery data; incompatible rows: {populated}"
        )
    op.drop_constraint(
        "seller_offer_preparation_time_valid",
        "seller_offers",
        type_="check",
    )
    op.drop_column("seller_offers", "preparation_time_days")
