"""add L1A financial foundation and bank account versions

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-30 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


bank_account_type = postgresql.ENUM(
    "UNKNOWN", "CHECKING", "SAVINGS", name="bank_account_type", create_type=False
)
bank_account_version_status = postgresql.ENUM(
    "PENDING_REVIEW",
    "APPROVED",
    "SUPERSEDED",
    name="bank_account_version_status",
    create_type=False,
)
seller_commission_type = postgresql.ENUM(
    "PERCENTAGE", "FIXED", name="seller_commission_type", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    bank_account_type.create(bind, checkfirst=True)
    bank_account_version_status.create(bind, checkfirst=True)

    op.create_check_constraint("order_currency_usd", "orders", "currency = 'USD'")

    op.add_column(
        "seller_orders",
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
    )
    op.create_check_constraint(
        "seller_order_currency_usd", "seller_orders", "currency = 'USD'"
    )

    op.drop_constraint(
        "order_item_commission_snapshot_complete", "order_items", type_="check"
    )
    op.add_column(
        "order_items",
        sa.Column("store_id_snapshot", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.add_column(
        "order_items",
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
    )
    op.add_column(
        "order_items", sa.Column("gross_line_amount", sa.Numeric(12, 2), nullable=False)
    )
    op.add_column(
        "order_items",
        sa.Column("commission_type_snapshot", seller_commission_type, nullable=False),
    )
    op.add_column(
        "order_items",
        sa.Column("commission_fixed_amount_snapshot", sa.Numeric(12, 2), nullable=True),
    )
    op.alter_column(
        "order_items",
        "commission_amount_snapshot",
        existing_type=sa.Numeric(12, 2),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_order_items_store_snapshot",
        "order_items",
        "stores",
        ["store_id_snapshot"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_order_items_store_id_snapshot",
        "order_items",
        ["store_id_snapshot"],
    )
    op.create_check_constraint(
        "order_item_currency_usd", "order_items", "currency = 'USD'"
    )
    op.create_check_constraint(
        "order_item_gross_line_consistent",
        "order_items",
        "gross_line_amount = unit_price * quantity",
    )
    op.create_check_constraint(
        "order_item_commission_fixed_nonnegative",
        "order_items",
        "commission_fixed_amount_snapshot IS NULL OR commission_fixed_amount_snapshot >= 0",
    )
    op.create_check_constraint(
        "order_item_commission_snapshot_complete",
        "order_items",
        "(commission_type_snapshot = 'PERCENTAGE' "
        "AND commission_rate_snapshot IS NOT NULL "
        "AND commission_fixed_amount_snapshot IS NULL) OR "
        "(commission_type_snapshot = 'FIXED' "
        "AND commission_rate_snapshot IS NULL "
        "AND commission_fixed_amount_snapshot IS NOT NULL)",
    )

    op.create_table(
        "store_bank_account_versions",
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("holder_name", sa.String(length=150), nullable=False),
        sa.Column("holder_identification", sa.String(length=40), nullable=False),
        sa.Column("bank_name", sa.String(length=120), nullable=False),
        sa.Column(
            "account_type",
            bank_account_type,
            server_default="UNKNOWN",
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("encrypted_account_number", sa.LargeBinary(), nullable=False),
        sa.Column("encryption_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("account_last4", sa.String(length=4), nullable=False),
        sa.Column("account_fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("encryption_key_version", sa.String(length=40), nullable=False),
        sa.Column("fingerprint_key_version", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            bank_account_version_status,
            server_default="PENDING_REVIEW",
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("usable_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_onboarding_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.CheckConstraint("version > 0", name="store_bank_version_positive"),
        sa.CheckConstraint("currency = 'USD'", name="store_bank_version_currency_usd"),
        sa.CheckConstraint(
            "char_length(account_last4) = 4 AND account_last4 ~ '^[0-9]{4}$'",
            name="store_bank_version_last4_valid",
        ),
        sa.CheckConstraint(
            "octet_length(encryption_nonce) = 12",
            name="store_bank_version_nonce_valid",
        ),
        sa.CheckConstraint(
            "octet_length(encrypted_account_number) >= 17",
            name="bank_ciphertext_valid",
        ),
        sa.CheckConstraint(
            "octet_length(account_fingerprint) = 32",
            name="bank_fingerprint_valid",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING_REVIEW' AND reviewed_at IS NULL "
            "AND usable_from IS NULL AND superseded_at IS NULL) OR "
            "(status = 'APPROVED' AND reviewed_at IS NOT NULL "
            "AND usable_from IS NOT NULL AND superseded_at IS NULL) OR "
            "(status = 'SUPERSEDED' AND superseded_at IS NOT NULL "
            "AND ((reviewed_at IS NULL AND usable_from IS NULL) OR "
            "(reviewed_at IS NOT NULL AND usable_from IS NOT NULL)))",
            name="store_bank_version_state_valid",
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_onboarding_id"], ["store_onboardings.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "store_id", "version", name="uq_store_bank_account_versions_store_version"
        ),
        sa.UniqueConstraint(
            "id", "store_id", name="uq_store_bank_account_versions_id_store"
        ),
    )
    op.create_index(
        op.f("ix_store_bank_account_versions_store_id"),
        "store_bank_account_versions",
        ["store_id"],
    )
    op.create_index(
        op.f("ix_store_bank_account_versions_status"),
        "store_bank_account_versions",
        ["status"],
    )
    op.create_index(
        op.f("ix_store_bank_account_versions_usable_from"),
        "store_bank_account_versions",
        ["usable_from"],
    )
    op.create_index(
        op.f("ix_store_bank_account_versions_reviewed_by_user_id"),
        "store_bank_account_versions",
        ["reviewed_by_user_id"],
    )
    op.create_index(
        op.f("ix_store_bank_account_versions_source_onboarding_id"),
        "store_bank_account_versions",
        ["source_onboarding_id"],
    )
    op.create_index(
        "ix_store_bank_versions_store_status_usable",
        "store_bank_account_versions",
        ["store_id", "status", "usable_from"],
    )
    op.create_index(
        "uq_store_bank_versions_pending",
        "store_bank_account_versions",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING_REVIEW'"),
    )

    op.add_column(
        "seller_payouts",
        sa.Column("bank_account_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f("ix_seller_payouts_bank_account_version_id"),
        "seller_payouts",
        ["bank_account_version_id"],
    )
    op.create_foreign_key(
        "fk_seller_payouts_bank_version_store",
        "seller_payouts",
        "store_bank_account_versions",
        ["bank_account_version_id", "store_id"],
        ["id", "store_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_seller_payouts_external_reference",
        "seller_payouts",
        ["external_reference"],
    )
    op.create_check_constraint(
        "seller_payout_currency_usd", "seller_payouts", "currency = 'USD'"
    )

    op.execute(
        """
        CREATE FUNCTION prevent_order_item_financial_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.seller_order_id IS DISTINCT FROM NEW.seller_order_id
               OR OLD.store_id_snapshot IS DISTINCT FROM NEW.store_id_snapshot
               OR OLD.quantity IS DISTINCT FROM NEW.quantity
               OR OLD.unit_price IS DISTINCT FROM NEW.unit_price
               OR OLD.discount_amount IS DISTINCT FROM NEW.discount_amount
               OR OLD.tax_amount IS DISTINCT FROM NEW.tax_amount
               OR OLD.line_total IS DISTINCT FROM NEW.line_total
               OR OLD.currency IS DISTINCT FROM NEW.currency
               OR OLD.gross_line_amount IS DISTINCT FROM NEW.gross_line_amount
               OR OLD.commission_type_snapshot IS DISTINCT FROM NEW.commission_type_snapshot
               OR OLD.commission_rate_snapshot IS DISTINCT FROM NEW.commission_rate_snapshot
               OR OLD.commission_fixed_amount_snapshot IS DISTINCT FROM NEW.commission_fixed_amount_snapshot
               OR OLD.commission_amount_snapshot IS DISTINCT FROM NEW.commission_amount_snapshot
            THEN
                RAISE EXCEPTION 'order item financial facts are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_order_items_financial_immutable
        BEFORE UPDATE ON order_items
        FOR EACH ROW EXECUTE FUNCTION prevent_order_item_financial_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_seller_order_financial_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.order_id IS DISTINCT FROM NEW.order_id
               OR OLD.store_id IS DISTINCT FROM NEW.store_id
               OR OLD.subtotal IS DISTINCT FROM NEW.subtotal
               OR OLD.discount_total IS DISTINCT FROM NEW.discount_total
               OR OLD.commission_total IS DISTINCT FROM NEW.commission_total
               OR OLD.seller_net_total IS DISTINCT FROM NEW.seller_net_total
               OR OLD.currency IS DISTINCT FROM NEW.currency
            THEN
                RAISE EXCEPTION 'seller order financial facts are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_seller_orders_financial_immutable
        BEFORE UPDATE ON seller_orders
        FOR EACH ROW EXECUTE FUNCTION prevent_seller_order_financial_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_seller_orders_financial_immutable ON seller_orders")
    op.execute("DROP FUNCTION prevent_seller_order_financial_update()")
    op.execute("DROP TRIGGER trg_order_items_financial_immutable ON order_items")
    op.execute("DROP FUNCTION prevent_order_item_financial_update()")

    op.drop_constraint("seller_payout_currency_usd", "seller_payouts", type_="check")
    op.drop_constraint(
        "uq_seller_payouts_external_reference", "seller_payouts", type_="unique"
    )
    op.drop_constraint(
        "fk_seller_payouts_bank_version_store", "seller_payouts", type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_seller_payouts_bank_account_version_id"),
        table_name="seller_payouts",
    )
    op.drop_column("seller_payouts", "bank_account_version_id")

    op.drop_table("store_bank_account_versions")

    op.drop_constraint(
        "order_item_commission_snapshot_complete", "order_items", type_="check"
    )
    op.drop_constraint(
        "order_item_commission_fixed_nonnegative", "order_items", type_="check"
    )
    op.drop_constraint(
        "order_item_gross_line_consistent", "order_items", type_="check"
    )
    op.drop_constraint("order_item_currency_usd", "order_items", type_="check")
    op.drop_index("ix_order_items_store_id_snapshot", table_name="order_items")
    op.drop_constraint("fk_order_items_store_snapshot", "order_items", type_="foreignkey")
    op.execute(
        "UPDATE order_items SET commission_rate_snapshot = 0 "
        "WHERE commission_rate_snapshot IS NULL"
    )
    op.alter_column(
        "order_items",
        "commission_amount_snapshot",
        existing_type=sa.Numeric(12, 2),
        nullable=True,
    )
    op.drop_column("order_items", "commission_fixed_amount_snapshot")
    op.drop_column("order_items", "commission_type_snapshot")
    op.drop_column("order_items", "gross_line_amount")
    op.drop_column("order_items", "currency")
    op.drop_column("order_items", "store_id_snapshot")
    op.create_check_constraint(
        "order_item_commission_snapshot_complete",
        "order_items",
        "(commission_rate_snapshot IS NULL) = (commission_amount_snapshot IS NULL)",
    )

    op.drop_constraint("seller_order_currency_usd", "seller_orders", type_="check")
    op.drop_column("seller_orders", "currency")
    op.drop_constraint("order_currency_usd", "orders", type_="check")

    bank_account_version_status.drop(op.get_bind(), checkfirst=True)
    bank_account_type.drop(op.get_bind(), checkfirst=True)
