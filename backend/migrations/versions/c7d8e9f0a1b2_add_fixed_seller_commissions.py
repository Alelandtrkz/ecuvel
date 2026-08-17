"""add fixed seller commission snapshots

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-08-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "b6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    commission_type = sa.Enum(
        "PERCENTAGE", "FIXED", name="seller_commission_type"
    )
    commission_type.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "seller_offers",
        sa.Column(
            "commission_type", commission_type,
            server_default="PERCENTAGE", nullable=False,
        ),
    )
    op.add_column(
        "seller_offers",
        sa.Column("commission_fixed_amount", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "seller_offers",
        sa.Column(
            "commission_currency", sa.String(length=3),
            server_default="USD", nullable=False,
        ),
    )
    op.create_check_constraint(
        "seller_offer_commission_mode_valid",
        "seller_offers",
        "(commission_type = 'PERCENTAGE' AND commission_fixed_amount IS NULL) "
        "OR (commission_type = 'FIXED' AND commission_rate = 0 "
        "AND commission_fixed_amount > 0 AND commission_fixed_amount < price)",
    )
    op.create_check_constraint(
        "seller_offer_commission_currency_valid",
        "seller_offers",
        "commission_currency = 'USD'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "seller_offer_commission_currency_valid", "seller_offers", type_="check"
    )
    op.drop_constraint(
        "seller_offer_commission_mode_valid", "seller_offers", type_="check"
    )
    op.drop_column("seller_offers", "commission_currency")
    op.drop_column("seller_offers", "commission_fixed_amount")
    op.drop_column("seller_offers", "commission_type")
    sa.Enum(name="seller_commission_type").drop(op.get_bind(), checkfirst=True)
