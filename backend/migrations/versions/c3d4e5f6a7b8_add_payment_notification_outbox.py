"""add payment notification outbox

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-28 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_notification_outbox",
        sa.Column("payment_attempt_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.String(length=15),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "id",
            sa.UUID(),
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
        sa.CheckConstraint(
            "attempts >= 0", name="ck_payment_notification_attempts"
        ),
        sa.CheckConstraint(
            "event_type IN ('PAYMENT_APPROVED', 'PAYMENT_REJECTED')",
            name="ck_payment_notification_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["payment_attempt_id"],
            ["payment_attempts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "payment_attempt_id",
            "event_type",
            name="uq_payment_notification_outbox_event",
        ),
    )
    op.create_index(
        op.f("ix_payment_notification_outbox_payment_attempt_id"),
        "payment_notification_outbox",
        ["payment_attempt_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_notification_outbox_order_id"),
        "payment_notification_outbox",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_notification_outbox_user_id"),
        "payment_notification_outbox",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_notification_outbox_status"),
        "payment_notification_outbox",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_notification_outbox_next_attempt_at"),
        "payment_notification_outbox",
        ["next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_payment_notification_outbox_next_attempt_at"),
        table_name="payment_notification_outbox",
    )
    op.drop_index(
        op.f("ix_payment_notification_outbox_status"),
        table_name="payment_notification_outbox",
    )
    op.drop_index(
        op.f("ix_payment_notification_outbox_user_id"),
        table_name="payment_notification_outbox",
    )
    op.drop_index(
        op.f("ix_payment_notification_outbox_order_id"),
        table_name="payment_notification_outbox",
    )
    op.drop_index(
        op.f("ix_payment_notification_outbox_payment_attempt_id"),
        table_name="payment_notification_outbox",
    )
    op.drop_table("payment_notification_outbox")
