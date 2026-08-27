"""harden payment review identity and invariants

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    duplicate_approved = bind.execute(
        sa.text(
            """
            SELECT order_id
            FROM payment_attempts
            WHERE status = 'APPROVED'
            GROUP BY order_id
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate_approved is not None:
        raise RuntimeError(
            "No puede aplicarse la migración: existen múltiples pagos APPROVED para un pedido."
        )

    op.execute(
        "CREATE SEQUENCE payment_attempt_public_code_seq "
        "START WITH 1 INCREMENT BY 1 NO MINVALUE MAXVALUE 99999999 CACHE 1"
    )
    op.add_column(
        "payment_attempts",
        sa.Column("public_code", sa.String(length=20), nullable=True),
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (ORDER BY created_at ASC, id ASC) AS sequence_number
            FROM payment_attempts
        )
        UPDATE payment_attempts AS payment
        SET public_code = 'PMT-' || lpad(ranked.sequence_number::text, 8, '0')
        FROM ranked
        WHERE payment.id = ranked.id
        """
    )
    payment_count = int(
        bind.execute(sa.text("SELECT count(*) FROM payment_attempts")).scalar_one()
    )
    if payment_count:
        bind.execute(
            sa.text("SELECT setval('payment_attempt_public_code_seq', :value, true)"),
            {"value": payment_count},
        )
    else:
        op.execute("SELECT setval('payment_attempt_public_code_seq', 1, false)")
    op.alter_column(
        "payment_attempts",
        "public_code",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default=sa.text(
            "('PMT-' || lpad(nextval('payment_attempt_public_code_seq'::regclass)::text, 8, '0'))"
        ),
    )
    op.create_index(
        op.f("ix_payment_attempts_public_code"),
        "payment_attempts",
        ["public_code"],
        unique=True,
    )
    op.create_index(
        "uq_payment_attempts_one_approved_per_order",
        "payment_attempts",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status = 'APPROVED'"),
    )

    op.add_column(
        "payment_proofs",
        sa.Column("rejection_reason_code", sa.String(length=50), nullable=True),
    )
    op.create_check_constraint(
        "payment_proof_rejection_reason_code_valid",
        "payment_proofs",
        "rejection_reason_code IS NULL OR rejection_reason_code IN ("
        "'AMOUNT_MISMATCH', 'DESTINATION_ACCOUNT_MISMATCH', "
        "'DUPLICATE_PROOF', 'UNREADABLE_PROOF', 'INVALID_DATE', "
        "'UNVERIFIABLE_TRANSACTION', 'INVALID_DOCUMENT', 'OTHER')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "payment_proof_rejection_reason_code_valid",
        "payment_proofs",
        type_="check",
    )
    op.drop_column("payment_proofs", "rejection_reason_code")
    op.drop_index(
        "uq_payment_attempts_one_approved_per_order",
        table_name="payment_attempts",
    )
    op.drop_index(
        op.f("ix_payment_attempts_public_code"),
        table_name="payment_attempts",
    )
    op.drop_column("payment_attempts", "public_code")
    op.execute("DROP SEQUENCE payment_attempt_public_code_seq")
