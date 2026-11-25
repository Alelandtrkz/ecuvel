"""Persist the checkout pickup point. Historical destinations stay unknown."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b47e912cf630"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("pickup_warehouse_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_orders_pickup_warehouse", "orders", "warehouses",
                          ["pickup_warehouse_id"], ["id"], ondelete="RESTRICT")


def downgrade():
    op.drop_constraint("fk_orders_pickup_warehouse", "orders", type_="foreignkey")
    op.drop_column("orders", "pickup_warehouse_id")
