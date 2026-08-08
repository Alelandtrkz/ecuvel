"""add seller payouts and auditable payout eligibility

Revision ID: e2f3a4b5c6d7
Revises: d0e1f2a3b4c5
Create Date: 2026-08-08 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


payout_status = postgresql.ENUM(
    "SCHEDULED",
    "PAID",
    "ON_HOLD",
    "CANCELLED",
    name="seller_payout_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    payout_status.create(bind, checkfirst=True)
    op.execute(
        "CREATE SEQUENCE seller_payout_number_seq "
        "START WITH 1 INCREMENT BY 1 NO MINVALUE "
        "MAXVALUE 99999999 CACHE 1"
    )

    op.add_column(
        "seller_orders",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "seller_orders",
        sa.Column("payout_eligible_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "seller_order_payout_requires_delivery",
        "seller_orders",
        "payout_eligible_at IS NULL OR delivered_at IS NOT NULL",
    )
    op.create_check_constraint(
        "seller_order_payout_after_delivery",
        "seller_orders",
        "payout_eligible_at IS NULL OR payout_eligible_at >= delivered_at",
    )
    op.create_index(
        op.f("ix_seller_orders_delivered_at"), "seller_orders", ["delivered_at"]
    )
    op.create_index(
        op.f("ix_seller_orders_payout_eligible_at"),
        "seller_orders",
        ["payout_eligible_at"],
    )
    op.create_index(
        "ix_seller_orders_store_payout_eligible",
        "seller_orders",
        ["store_id", "payout_eligible_at"],
    )

    op.create_table(
        "seller_payouts",
        sa.Column(
            "payout_number",
            sa.String(length=20),
            server_default=sa.text(
                "('PAY-' || lpad(nextval('seller_payout_number_seq'::regclass)::text, 8, '0'))"
            ),
            nullable=False,
        ),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            payout_status,
            server_default="SCHEDULED",
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("gross_sales_total", sa.Numeric(12, 2), server_default="0.00", nullable=False),
        sa.Column("discount_total", sa.Numeric(12, 2), server_default="0.00", nullable=False),
        sa.Column("commission_total", sa.Numeric(12, 2), server_default="0.00", nullable=False),
        sa.Column("net_total", sa.Numeric(12, 2), server_default="0.00", nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_reference", sa.String(length=200), nullable=True),
        sa.Column("destination_bank_name_snapshot", sa.String(length=120), nullable=True),
        sa.Column("destination_account_last4", sa.String(length=4), nullable=True),
        sa.Column("receipt_storage_key", sa.String(length=500), nullable=True),
        sa.Column("receipt_original_filename", sa.String(length=255), nullable=True),
        sa.Column("receipt_media_type", sa.String(length=80), nullable=True),
        sa.Column("receipt_size_bytes", sa.Integer(), nullable=True),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint("gross_sales_total >= 0", name="seller_payout_gross_nonnegative"),
        sa.CheckConstraint("discount_total >= 0", name="seller_payout_discount_nonnegative"),
        sa.CheckConstraint("commission_total >= 0", name="seller_payout_commission_nonnegative"),
        sa.CheckConstraint("net_total >= 0", name="seller_payout_net_nonnegative"),
        sa.CheckConstraint(
            "net_total = gross_sales_total - discount_total - commission_total",
            name="seller_payout_total_consistent",
        ),
        sa.CheckConstraint(
            "destination_account_last4 IS NULL OR char_length(destination_account_last4) = 4",
            name="seller_payout_account_last4_valid",
        ),
        sa.CheckConstraint(
            "(receipt_storage_key IS NULL AND receipt_original_filename IS NULL "
            "AND receipt_media_type IS NULL AND receipt_size_bytes IS NULL "
            "AND receipt_sha256 IS NULL) OR "
            "(receipt_storage_key IS NOT NULL AND receipt_original_filename IS NOT NULL "
            "AND receipt_media_type IS NOT NULL AND receipt_size_bytes > 0 "
            "AND receipt_sha256 IS NOT NULL)",
            name="seller_payout_receipt_metadata_complete",
        ),
        sa.CheckConstraint(
            "status != 'PAID' OR (paid_at IS NOT NULL AND external_reference IS NOT NULL)",
            name="seller_payout_paid_state_valid",
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payout_number", name="uq_seller_payouts_payout_number"),
        sa.UniqueConstraint(
            "receipt_storage_key", name="uq_seller_payouts_receipt_storage_key"
        ),
    )
    op.create_index(op.f("ix_seller_payouts_payout_number"), "seller_payouts", ["payout_number"])
    op.create_index(op.f("ix_seller_payouts_store_id"), "seller_payouts", ["store_id"])
    op.create_index(op.f("ix_seller_payouts_status"), "seller_payouts", ["status"])
    op.create_index(op.f("ix_seller_payouts_scheduled_for"), "seller_payouts", ["scheduled_for"])
    op.create_index(op.f("ix_seller_payouts_paid_at"), "seller_payouts", ["paid_at"])
    op.create_index(
        "ix_seller_payouts_store_status_schedule",
        "seller_payouts",
        ["store_id", "status", "scheduled_for"],
    )

    op.create_table(
        "seller_payout_items",
        sa.Column("payout_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seller_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gross_amount_snapshot", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_amount_snapshot", sa.Numeric(12, 2), nullable=False),
        sa.Column("commission_amount_snapshot", sa.Numeric(12, 2), nullable=False),
        sa.Column("net_amount_snapshot", sa.Numeric(12, 2), nullable=False),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "gross_amount_snapshot >= 0", name="seller_payout_item_gross_nonnegative"
        ),
        sa.CheckConstraint(
            "discount_amount_snapshot >= 0", name="seller_payout_item_discount_nonnegative"
        ),
        sa.CheckConstraint(
            "commission_amount_snapshot >= 0", name="seller_payout_item_commission_nonnegative"
        ),
        sa.CheckConstraint(
            "net_amount_snapshot >= 0", name="seller_payout_item_net_nonnegative"
        ),
        sa.CheckConstraint(
            "net_amount_snapshot = gross_amount_snapshot - discount_amount_snapshot "
            "- commission_amount_snapshot",
            name="seller_payout_item_total_consistent",
        ),
        sa.ForeignKeyConstraint(["payout_id"], ["seller_payouts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["seller_order_id"], ["seller_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("payout_id", "seller_order_id"),
        sa.UniqueConstraint("seller_order_id", name="uq_seller_payout_items_seller_order"),
    )
    op.create_index(
        op.f("ix_seller_payout_items_seller_order_id"),
        "seller_payout_items",
        ["seller_order_id"],
    )
    op.create_index(
        "ix_seller_payout_items_payout_eligible",
        "seller_payout_items",
        ["payout_id", "eligible_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_seller_payout_items_payout_eligible", table_name="seller_payout_items")
    op.drop_index(op.f("ix_seller_payout_items_seller_order_id"), table_name="seller_payout_items")
    op.drop_table("seller_payout_items")
    op.drop_index("ix_seller_payouts_store_status_schedule", table_name="seller_payouts")
    op.drop_index(op.f("ix_seller_payouts_paid_at"), table_name="seller_payouts")
    op.drop_index(op.f("ix_seller_payouts_scheduled_for"), table_name="seller_payouts")
    op.drop_index(op.f("ix_seller_payouts_status"), table_name="seller_payouts")
    op.drop_index(op.f("ix_seller_payouts_store_id"), table_name="seller_payouts")
    op.drop_index(op.f("ix_seller_payouts_payout_number"), table_name="seller_payouts")
    op.drop_table("seller_payouts")
    op.drop_index("ix_seller_orders_store_payout_eligible", table_name="seller_orders")
    op.drop_index(op.f("ix_seller_orders_payout_eligible_at"), table_name="seller_orders")
    op.drop_index(op.f("ix_seller_orders_delivered_at"), table_name="seller_orders")
    op.drop_constraint("seller_order_payout_after_delivery", "seller_orders", type_="check")
    op.drop_constraint("seller_order_payout_requires_delivery", "seller_orders", type_="check")
    op.drop_column("seller_orders", "payout_eligible_at")
    op.drop_column("seller_orders", "delivered_at")
    op.execute("DROP SEQUENCE seller_payout_number_seq")
    payout_status.drop(op.get_bind(), checkfirst=True)
