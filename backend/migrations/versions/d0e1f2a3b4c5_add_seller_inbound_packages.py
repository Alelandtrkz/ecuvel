"""add seller inbound packages

Revision ID: d0e1f2a3b4c5
Revises: c8d9e0f1a2b3
Create Date: 2026-08-08 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


inbound_status = postgresql.ENUM(
    "CREATED",
    "READY_FOR_DROPOFF",
    "RECEIVED_BY_ECUVEL",
    "CANCELLED",
    name="seller_inbound_package_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    inbound_status.create(bind, checkfirst=True)
    op.execute(
        "CREATE SEQUENCE seller_inbound_package_public_seq "
        "START WITH 1 INCREMENT BY 1 NO MINVALUE "
        "MAXVALUE 999999999999 CACHE 1"
    )

    op.add_column(
        "users",
        sa.Column(
            "is_ecuvel_staff",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_users_is_ecuvel_staff"),
        "users",
        ["is_ecuvel_staff"],
    )

    op.create_table(
        "seller_inbound_packages",
        sa.Column("seller_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_code", sa.String(length=16), nullable=False),
        sa.Column("barcode", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            inbound_status,
            server_default="CREATED",
            nullable=False,
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ready_for_dropoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ready_for_dropoff_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("received_location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
            "barcode = package_code",
            name="seller_inbound_package_barcode_matches_code",
        ),
        sa.CheckConstraint(
            "(status != 'CREATED') OR "
            "(ready_for_dropoff_at IS NULL AND received_at IS NULL "
            "AND received_location_id IS NULL AND cancelled_at IS NULL)",
            name="seller_inbound_package_created_state_valid",
        ),
        sa.CheckConstraint(
            "(status != 'READY_FOR_DROPOFF') OR "
            "(ready_for_dropoff_at IS NOT NULL AND received_at IS NULL "
            "AND received_location_id IS NULL AND cancelled_at IS NULL)",
            name="seller_inbound_package_ready_state_valid",
        ),
        sa.CheckConstraint(
            "(status != 'RECEIVED_BY_ECUVEL') OR "
            "(ready_for_dropoff_at IS NOT NULL AND received_at IS NOT NULL "
            "AND received_location_id IS NOT NULL AND cancelled_at IS NULL)",
            name="seller_inbound_package_received_state_valid",
        ),
        sa.CheckConstraint(
            "(status != 'CANCELLED') OR "
            "(cancelled_at IS NOT NULL AND received_at IS NULL)",
            name="seller_inbound_package_cancelled_state_valid",
        ),
        sa.ForeignKeyConstraint(
            ["seller_order_id"], ["seller_orders.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["ready_for_dropoff_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["received_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["received_location_id"],
            ["warehouse_locations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "package_code", name="uq_seller_inbound_packages_package_code"
        ),
        sa.UniqueConstraint(
            "barcode", name="uq_seller_inbound_packages_barcode"
        ),
    )
    op.create_index(
        op.f("ix_seller_inbound_packages_seller_order_id"),
        "seller_inbound_packages",
        ["seller_order_id"],
    )
    op.create_index(
        op.f("ix_seller_inbound_packages_status"),
        "seller_inbound_packages",
        ["status"],
    )
    op.create_index(
        op.f("ix_seller_inbound_packages_created_by_user_id"),
        "seller_inbound_packages",
        ["created_by_user_id"],
    )
    op.create_index(
        op.f("ix_seller_inbound_packages_ready_for_dropoff_by_user_id"),
        "seller_inbound_packages",
        ["ready_for_dropoff_by_user_id"],
    )
    op.create_index(
        op.f("ix_seller_inbound_packages_received_by_user_id"),
        "seller_inbound_packages",
        ["received_by_user_id"],
    )
    op.create_index(
        op.f("ix_seller_inbound_packages_received_location_id"),
        "seller_inbound_packages",
        ["received_location_id"],
    )
    op.create_index(
        "ix_seller_inbound_packages_order_status",
        "seller_inbound_packages",
        ["seller_order_id", "status"],
    )

    op.create_table(
        "seller_inbound_package_items",
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
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
            "quantity > 0",
            name="seller_inbound_package_item_quantity_positive",
        ),
        sa.ForeignKeyConstraint(
            ["package_id"], ["seller_inbound_packages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id"], ["order_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("package_id", "order_item_id"),
    )
    op.create_index(
        op.f("ix_seller_inbound_package_items_order_item_id"),
        "seller_inbound_package_items",
        ["order_item_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_seller_inbound_package_items_order_item_id"),
        table_name="seller_inbound_package_items",
    )
    op.drop_table("seller_inbound_package_items")
    op.drop_index(
        "ix_seller_inbound_packages_order_status",
        table_name="seller_inbound_packages",
    )
    op.drop_index(
        op.f("ix_seller_inbound_packages_received_location_id"),
        table_name="seller_inbound_packages",
    )
    op.drop_index(
        op.f("ix_seller_inbound_packages_received_by_user_id"),
        table_name="seller_inbound_packages",
    )
    op.drop_index(
        op.f("ix_seller_inbound_packages_ready_for_dropoff_by_user_id"),
        table_name="seller_inbound_packages",
    )
    op.drop_index(
        op.f("ix_seller_inbound_packages_created_by_user_id"),
        table_name="seller_inbound_packages",
    )
    op.drop_index(
        op.f("ix_seller_inbound_packages_status"),
        table_name="seller_inbound_packages",
    )
    op.drop_index(
        op.f("ix_seller_inbound_packages_seller_order_id"),
        table_name="seller_inbound_packages",
    )
    op.drop_table("seller_inbound_packages")
    op.drop_index(op.f("ix_users_is_ecuvel_staff"), table_name="users")
    op.drop_column("users", "is_ecuvel_staff")
    op.execute("DROP SEQUENCE seller_inbound_package_public_seq")
    inbound_status.drop(op.get_bind(), checkfirst=True)
