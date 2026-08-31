"""drop legacy onboarding bank plaintext

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-30 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _assert_contract_preconditions() -> None:
    bind = op.get_bind()
    plaintext_rows = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM store_onboardings
                WHERE bank_account_owner IS NOT NULL
                   OR bank_account_number IS NOT NULL
                   OR bank_name IS NOT NULL
                   OR bank_id_number IS NOT NULL
                """
            )
        )
        or 0
    )
    if plaintext_rows:
        raise RuntimeError(
            "legacy onboarding bank plaintext still exists; "
            "run/verify L1B.1 before schema cleanup "
            f"({plaintext_rows} row(s) blocked)"
        )

    sensitive_snapshots = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM store_verification_reviews AS review
                CROSS JOIN LATERAL jsonb_array_elements(
                    COALESCE(review.issues_snapshot, '[]'::jsonb)
                ) AS issue
                WHERE issue->>'target_type' = 'FIELD'
                  AND issue->>'field' IN (
                      'bank_account_owner',
                      'bank_account_number',
                      'bank_name',
                      'bank_id_number'
                  )
                  AND issue ? 'previous_value'
                """
            )
        )
        or 0
    )
    if sensitive_snapshots:
        raise RuntimeError(
            "sensitive bank correction previous_value still exists; "
            "run/verify L1B.1 before schema cleanup "
            f"({sensitive_snapshots} issue(s) blocked)"
        )


def upgrade() -> None:
    # All data gates run before the first destructive DDL statement. PostgreSQL
    # transactional DDL guarantees that a failed contract leaves every column.
    _assert_contract_preconditions()
    op.drop_column("store_onboardings", "bank_account_owner")
    op.drop_column("store_onboardings", "bank_account_number")
    op.drop_column("store_onboardings", "bank_name")
    op.drop_column("store_onboardings", "bank_id_number")


def downgrade() -> None:
    # Schema compatibility only. Historical plaintext is intentionally never
    # decrypted or reconstructed from StoreBankAccountVersion.
    op.add_column(
        "store_onboardings",
        sa.Column("bank_account_owner", sa.String(length=150), nullable=True),
    )
    op.add_column(
        "store_onboardings",
        sa.Column("bank_account_number", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "store_onboardings",
        sa.Column("bank_name", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "store_onboardings",
        sa.Column("bank_id_number", sa.String(length=40), nullable=True),
    )
