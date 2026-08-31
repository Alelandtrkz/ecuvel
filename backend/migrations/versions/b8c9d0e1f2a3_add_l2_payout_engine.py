"""add L2 payout calendar, release semantics and lifecycle enforcement

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar_one())


def _assert_upgrade_preconditions() -> None:
    checks = (
        (
            "SELECT count(*) FROM seller_payouts WHERE status = 'CANCELLED'",
            "cancelled payout history exists; release history cannot be inferred",
        ),
        (
            "SELECT count(*) FROM (SELECT external_reference FROM seller_payouts "
            "WHERE external_reference IS NOT NULL GROUP BY external_reference "
            "HAVING count(*) > 1) duplicate_refs",
            "duplicate payout external references exist",
        ),
        (
            "SELECT count(*) FROM seller_payouts WHERE "
            "(status = 'PAID' AND (paid_at IS NULL OR external_reference IS NULL "
            "OR btrim(external_reference) = '')) OR "
            "(status <> 'PAID' AND (paid_at IS NOT NULL OR external_reference IS NOT NULL))",
            "payout lifecycle rows are incompatible with L2",
        ),
        (
            "SELECT count(*) FROM seller_payouts "
            "WHERE paid_at IS NOT NULL AND paid_at < scheduled_for",
            "payout paid_at predates scheduled_for",
        ),
        (
            "SELECT count(*) FROM (SELECT seller_order_id FROM seller_payout_items "
            "GROUP BY seller_order_id HAVING count(*) > 1) duplicate_items",
            "duplicate seller payout item assignments exist",
        ),
        (
            "SELECT count(*) FROM seller_payouts WHERE bank_account_version_id IS NULL",
            "payouts without an exact bank account version exist",
        ),
        (
            "SELECT count(*) FROM seller_payouts WHERE status <> 'PAID' AND "
            "(receipt_storage_key IS NOT NULL OR receipt_original_filename IS NOT NULL "
            "OR receipt_media_type IS NOT NULL OR receipt_size_bytes IS NOT NULL "
            "OR receipt_sha256 IS NOT NULL)",
            "receipt metadata exists outside PAID payouts",
        ),
    )
    for sql, message in checks:
        count = _count(sql)
        if count:
            raise RuntimeError(f"{message}; incompatible rows: {count}")


def upgrade() -> None:
    _assert_upgrade_preconditions()

    op.add_column(
        "seller_payouts",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "seller_payout_items",
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(
        "uq_seller_payout_items_seller_order",
        "seller_payout_items",
        type_="unique",
    )
    op.create_index(
        "uq_seller_payout_items_active_seller_order",
        "seller_payout_items",
        ["seller_order_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        op.f("ix_seller_payout_items_released_at"),
        "seller_payout_items",
        ["released_at"],
    )
    op.create_index(
        op.f("ix_seller_payouts_cancelled_at"),
        "seller_payouts",
        ["cancelled_at"],
    )
    op.alter_column(
        "seller_payouts",
        "bank_account_version_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )

    op.drop_constraint(
        op.f("ck_seller_payouts_seller_payout_paid_state_valid"),
        "seller_payouts",
        type_="check",
    )
    op.create_check_constraint(
        "seller_payout_state_valid",
        "seller_payouts",
        "(status IN ('SCHEDULED', 'ON_HOLD') AND paid_at IS NULL "
        "AND external_reference IS NULL AND cancelled_at IS NULL) OR "
        "(status = 'PAID' AND paid_at IS NOT NULL "
        "AND external_reference IS NOT NULL AND btrim(external_reference) != '' "
        "AND cancelled_at IS NULL) OR "
        "(status = 'CANCELLED' AND cancelled_at IS NOT NULL "
        "AND paid_at IS NULL AND external_reference IS NULL)",
    )
    op.create_check_constraint(
        "seller_payout_paid_not_before_schedule",
        "seller_payouts",
        "status != 'PAID' OR paid_at >= scheduled_for",
    )
    op.create_check_constraint(
        "seller_payout_receipt_paid_only",
        "seller_payouts",
        "status = 'PAID' OR (receipt_storage_key IS NULL "
        "AND receipt_original_filename IS NULL AND receipt_media_type IS NULL "
        "AND receipt_size_bytes IS NULL AND receipt_sha256 IS NULL)",
    )

    op.execute(
        """
        CREATE FUNCTION enforce_seller_payout_lifecycle()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status IS DISTINCT FROM NEW.status AND NOT (
                (OLD.status = 'SCHEDULED' AND NEW.status IN ('ON_HOLD', 'PAID', 'CANCELLED'))
                OR (OLD.status = 'ON_HOLD' AND NEW.status IN ('SCHEDULED', 'CANCELLED'))
            ) THEN
                RAISE EXCEPTION 'illegal seller payout lifecycle transition'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_seller_payout_lifecycle
        BEFORE UPDATE ON seller_payouts
        FOR EACH ROW EXECUTE FUNCTION enforce_seller_payout_lifecycle()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_seller_payout_fact_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.payout_number IS DISTINCT FROM NEW.payout_number
               OR OLD.store_id IS DISTINCT FROM NEW.store_id
               OR OLD.currency IS DISTINCT FROM NEW.currency
               OR OLD.gross_sales_total IS DISTINCT FROM NEW.gross_sales_total
               OR OLD.discount_total IS DISTINCT FROM NEW.discount_total
               OR OLD.commission_total IS DISTINCT FROM NEW.commission_total
               OR OLD.net_total IS DISTINCT FROM NEW.net_total
               OR OLD.scheduled_for IS DISTINCT FROM NEW.scheduled_for
               OR OLD.bank_account_version_id IS DISTINCT FROM NEW.bank_account_version_id
               OR OLD.destination_bank_name_snapshot IS DISTINCT FROM NEW.destination_bank_name_snapshot
               OR OLD.destination_account_last4 IS DISTINCT FROM NEW.destination_account_last4
               OR OLD.created_at IS DISTINCT FROM NEW.created_at
            THEN
                RAISE EXCEPTION 'seller payout facts are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_seller_payout_facts_immutable
        BEFORE UPDATE ON seller_payouts
        FOR EACH ROW EXECUTE FUNCTION prevent_seller_payout_fact_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_seller_payout_item_fact_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status seller_payout_status;
        DECLARE parent_cancelled_at timestamptz;
        BEGIN
            IF OLD.payout_id IS DISTINCT FROM NEW.payout_id
               OR OLD.seller_order_id IS DISTINCT FROM NEW.seller_order_id
               OR OLD.gross_amount_snapshot IS DISTINCT FROM NEW.gross_amount_snapshot
               OR OLD.discount_amount_snapshot IS DISTINCT FROM NEW.discount_amount_snapshot
               OR OLD.commission_amount_snapshot IS DISTINCT FROM NEW.commission_amount_snapshot
               OR OLD.net_amount_snapshot IS DISTINCT FROM NEW.net_amount_snapshot
               OR OLD.eligible_at IS DISTINCT FROM NEW.eligible_at
            THEN
                RAISE EXCEPTION 'seller payout item facts are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.released_at IS NOT NULL AND OLD.released_at IS DISTINCT FROM NEW.released_at THEN
                RAISE EXCEPTION 'seller payout item release is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.released_at IS NULL AND NEW.released_at IS NOT NULL THEN
                SELECT status, cancelled_at INTO parent_status, parent_cancelled_at
                FROM seller_payouts WHERE id = NEW.payout_id;
                IF parent_status != 'CANCELLED' OR parent_cancelled_at IS DISTINCT FROM NEW.released_at THEN
                    RAISE EXCEPTION 'seller payout item release requires matching cancellation'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_seller_payout_item_facts_immutable
        BEFORE UPDATE ON seller_payout_items
        FOR EACH ROW EXECUTE FUNCTION prevent_seller_payout_item_fact_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION verify_seller_payout_release_consistency()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target_payout_id uuid;
        DECLARE invalid_count bigint;
        BEGIN
            target_payout_id := COALESCE(
                (to_jsonb(NEW) ->> 'id')::uuid,
                (to_jsonb(NEW) ->> 'payout_id')::uuid
            );
            SELECT count(*) INTO invalid_count
            FROM seller_payout_items i
            JOIN seller_payouts p ON p.id = i.payout_id
            WHERE p.id = target_payout_id
              AND ((p.status = 'CANCELLED' AND i.released_at IS DISTINCT FROM p.cancelled_at)
                   OR (p.status != 'CANCELLED' AND i.released_at IS NOT NULL));
            IF invalid_count > 0 THEN
                RAISE EXCEPTION 'seller payout release state is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_seller_payout_release_consistency
        AFTER INSERT OR UPDATE ON seller_payouts
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION verify_seller_payout_release_consistency()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_seller_payout_item_release_consistency
        AFTER INSERT OR UPDATE ON seller_payout_items
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION verify_seller_payout_release_consistency()
        """
    )


def downgrade() -> None:
    duplicate_count = _count(
        "SELECT count(*) FROM (SELECT seller_order_id FROM seller_payout_items "
        "GROUP BY seller_order_id HAVING count(*) > 1) duplicate_items"
    )
    if duplicate_count:
        raise RuntimeError(
            "payout cancellation/re-payout history cannot be represented by the previous schema; "
            f"duplicate seller orders: {duplicate_count}"
        )

    op.execute("DROP TRIGGER trg_seller_payout_item_release_consistency ON seller_payout_items")
    op.execute("DROP TRIGGER trg_seller_payout_release_consistency ON seller_payouts")
    op.execute("DROP FUNCTION verify_seller_payout_release_consistency()")
    op.execute("DROP TRIGGER trg_seller_payout_item_facts_immutable ON seller_payout_items")
    op.execute("DROP FUNCTION prevent_seller_payout_item_fact_update()")
    op.execute("DROP TRIGGER trg_seller_payout_facts_immutable ON seller_payouts")
    op.execute("DROP FUNCTION prevent_seller_payout_fact_update()")
    op.execute("DROP TRIGGER trg_seller_payout_lifecycle ON seller_payouts")
    op.execute("DROP FUNCTION enforce_seller_payout_lifecycle()")

    op.drop_constraint(
        op.f("ck_seller_payouts_seller_payout_receipt_paid_only"),
        "seller_payouts",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_seller_payouts_seller_payout_paid_not_before_schedule"),
        "seller_payouts",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_seller_payouts_seller_payout_state_valid"),
        "seller_payouts",
        type_="check",
    )
    op.create_check_constraint(
        "seller_payout_paid_state_valid",
        "seller_payouts",
        "status != 'PAID' OR (paid_at IS NOT NULL AND external_reference IS NOT NULL)",
    )
    op.alter_column(
        "seller_payouts",
        "bank_account_version_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.drop_index(op.f("ix_seller_payouts_cancelled_at"), table_name="seller_payouts")
    op.drop_index(op.f("ix_seller_payout_items_released_at"), table_name="seller_payout_items")
    op.drop_index(
        "uq_seller_payout_items_active_seller_order",
        table_name="seller_payout_items",
    )
    op.create_unique_constraint(
        "uq_seller_payout_items_seller_order",
        "seller_payout_items",
        ["seller_order_id"],
    )
    op.drop_column("seller_payout_items", "released_at")
    op.drop_column("seller_payouts", "cancelled_at")
