"""add partner order decisions and commercial snapshots

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-08 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


decision_status = postgresql.ENUM(
    "PENDING", "APPROVED", "REJECTED",
    name="seller_order_decision_status",
    create_type=False,
)
rejection_reason = postgresql.ENUM(
    "OUT_OF_STOCK", "DAMAGED_OR_UNSHIPPABLE", "OTHER",
    name="seller_order_rejection_reason",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    decision_status.create(bind, checkfirst=True)
    rejection_reason.create(bind, checkfirst=True)

    op.add_column("seller_orders", sa.Column("decision_status", decision_status, nullable=True))
    op.add_column("seller_orders", sa.Column("decision_available_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("seller_orders", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("seller_orders", sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("seller_orders", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("seller_orders", sa.Column("rejected_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("seller_orders", sa.Column("rejection_reason", rejection_reason, nullable=True))
    op.add_column("seller_orders", sa.Column("rejection_comment", sa.String(length=300), nullable=True))
    op.add_column("seller_orders", sa.Column("ship_by_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("seller_orders", sa.Column("estimated_delivery_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column("seller_orders", sa.Column("estimated_delivery_to", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "seller_orders",
        sa.Column("requires_refund_resolution", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_seller_orders_approved_by_user_id_users"),
        "seller_orders", "users", ["approved_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_seller_orders_rejected_by_user_id_users"),
        "seller_orders", "users", ["rejected_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint(
        "seller_order_rejection_comment_length",
        "seller_orders",
        "rejection_comment IS NULL OR "
        "(char_length(btrim(rejection_comment)) >= 1 AND char_length(rejection_comment) <= 300)",
    )
    op.create_check_constraint(
        "seller_order_delivery_window_valid",
        "seller_orders",
        "estimated_delivery_from IS NULL OR estimated_delivery_to IS NULL "
        "OR estimated_delivery_from <= estimated_delivery_to",
    )
    op.create_index(op.f("ix_seller_orders_decision_status"), "seller_orders", ["decision_status"])
    op.create_index(op.f("ix_seller_orders_decision_available_at"), "seller_orders", ["decision_available_at"])
    op.create_index(op.f("ix_seller_orders_approved_by_user_id"), "seller_orders", ["approved_by_user_id"])
    op.create_index(op.f("ix_seller_orders_rejected_by_user_id"), "seller_orders", ["rejected_by_user_id"])
    op.create_index(op.f("ix_seller_orders_ship_by_at"), "seller_orders", ["ship_by_at"])
    op.create_index(op.f("ix_seller_orders_requires_refund_resolution"), "seller_orders", ["requires_refund_resolution"])
    op.create_index(
        "ix_seller_orders_store_decision_updated",
        "seller_orders",
        ["store_id", "decision_status", "updated_at"],
    )

    op.add_column("order_items", sa.Column("commission_rate_snapshot", sa.Numeric(5, 2), nullable=True))
    op.add_column("order_items", sa.Column("commission_amount_snapshot", sa.Numeric(12, 2), nullable=True))
    op.add_column("order_items", sa.Column("category_name_snapshot", sa.String(length=120), nullable=True))
    op.add_column("order_items", sa.Column("category_code_snapshot", sa.String(length=50), nullable=True))
    op.create_check_constraint(
        "order_item_commission_rate_valid",
        "order_items",
        "commission_rate_snapshot IS NULL OR "
        "(commission_rate_snapshot >= 0 AND commission_rate_snapshot <= 100)",
    )
    op.create_check_constraint(
        "order_item_commission_amount_nonnegative",
        "order_items",
        "commission_amount_snapshot IS NULL OR commission_amount_snapshot >= 0",
    )
    op.create_check_constraint(
        "order_item_commission_snapshot_complete",
        "order_items",
        "(commission_rate_snapshot IS NULL) = (commission_amount_snapshot IS NULL)",
    )

    # Historical orders already in fulfillment must stay operable. They are
    # marked APPROVED without inventing an approving employee. CONFIRMED paid
    # orders become actionable from the actual payment approval timestamp.
    op.execute(
        sa.text(
            """
            UPDATE seller_orders AS so
            SET decision_status = 'APPROVED'::seller_order_decision_status,
                decision_available_at = payment.approved_at,
                approved_at = COALESCE(payment.approved_at, so.updated_at),
                ship_by_at = payment.approved_at + interval '24 hours',
                estimated_delivery_from = payment.approved_at + interval '24 hours',
                estimated_delivery_to = payment.approved_at + interval '48 hours'
            FROM (
                SELECT DISTINCT ON (pa.order_id) pa.order_id, pa.approved_at
                FROM payment_attempts AS pa
                WHERE pa.status = 'APPROVED'
                  AND pa.approved_at IS NOT NULL
                ORDER BY pa.order_id, pa.approved_at DESC, pa.id DESC
            ) AS payment
            WHERE so.status IN ('PICKING', 'PACKED', 'READY_FOR_PICKUP', 'COMPLETED')
              AND payment.order_id = so.order_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE seller_orders AS so
            SET decision_status = 'PENDING'::seller_order_decision_status,
                decision_available_at = payment.approved_at,
                ship_by_at = payment.approved_at + interval '24 hours',
                estimated_delivery_from = payment.approved_at + interval '24 hours',
                estimated_delivery_to = payment.approved_at + interval '48 hours'
            FROM (
                SELECT DISTINCT ON (pa.order_id) pa.order_id, pa.approved_at
                FROM payment_attempts AS pa
                WHERE pa.status = 'APPROVED'
                  AND pa.approved_at IS NOT NULL
                ORDER BY pa.order_id, pa.approved_at DESC, pa.id DESC
            ) AS payment
            WHERE so.status = 'CONFIRMED'
              AND so.decision_status IS NULL
              AND payment.order_id = so.order_id
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint("order_item_commission_snapshot_complete", "order_items", type_="check")
    op.drop_constraint("order_item_commission_amount_nonnegative", "order_items", type_="check")
    op.drop_constraint("order_item_commission_rate_valid", "order_items", type_="check")
    op.drop_column("order_items", "category_code_snapshot")
    op.drop_column("order_items", "category_name_snapshot")
    op.drop_column("order_items", "commission_amount_snapshot")
    op.drop_column("order_items", "commission_rate_snapshot")

    op.drop_index("ix_seller_orders_store_decision_updated", table_name="seller_orders")
    op.drop_index(op.f("ix_seller_orders_requires_refund_resolution"), table_name="seller_orders")
    op.drop_index(op.f("ix_seller_orders_ship_by_at"), table_name="seller_orders")
    op.drop_index(op.f("ix_seller_orders_rejected_by_user_id"), table_name="seller_orders")
    op.drop_index(op.f("ix_seller_orders_approved_by_user_id"), table_name="seller_orders")
    op.drop_index(op.f("ix_seller_orders_decision_available_at"), table_name="seller_orders")
    op.drop_index(op.f("ix_seller_orders_decision_status"), table_name="seller_orders")
    op.drop_constraint("seller_order_delivery_window_valid", "seller_orders", type_="check")
    op.drop_constraint("seller_order_rejection_comment_length", "seller_orders", type_="check")
    op.drop_constraint(op.f("fk_seller_orders_rejected_by_user_id_users"), "seller_orders", type_="foreignkey")
    op.drop_constraint(op.f("fk_seller_orders_approved_by_user_id_users"), "seller_orders", type_="foreignkey")
    op.drop_column("seller_orders", "requires_refund_resolution")
    op.drop_column("seller_orders", "estimated_delivery_to")
    op.drop_column("seller_orders", "estimated_delivery_from")
    op.drop_column("seller_orders", "ship_by_at")
    op.drop_column("seller_orders", "rejection_comment")
    op.drop_column("seller_orders", "rejection_reason")
    op.drop_column("seller_orders", "rejected_by_user_id")
    op.drop_column("seller_orders", "rejected_at")
    op.drop_column("seller_orders", "approved_by_user_id")
    op.drop_column("seller_orders", "approved_at")
    op.drop_column("seller_orders", "decision_available_at")
    op.drop_column("seller_orders", "decision_status")
    rejection_reason.drop(op.get_bind(), checkfirst=True)
    decision_status.drop(op.get_bind(), checkfirst=True)
