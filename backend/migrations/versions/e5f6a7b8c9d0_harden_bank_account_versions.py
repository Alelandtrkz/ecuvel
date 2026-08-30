"""harden bank account version lifecycle and identity

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-30 04:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_store_bank_versions_approved",
        "store_bank_account_versions",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("status = 'APPROVED'"),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_store_bank_account_version_identity_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.store_id IS DISTINCT FROM NEW.store_id
               OR OLD.version IS DISTINCT FROM NEW.version
               OR OLD.holder_name IS DISTINCT FROM NEW.holder_name
               OR OLD.holder_identification IS DISTINCT FROM NEW.holder_identification
               OR OLD.bank_name IS DISTINCT FROM NEW.bank_name
               OR OLD.account_type IS DISTINCT FROM NEW.account_type
               OR OLD.currency IS DISTINCT FROM NEW.currency
               OR OLD.encrypted_account_number IS DISTINCT FROM NEW.encrypted_account_number
               OR OLD.encryption_nonce IS DISTINCT FROM NEW.encryption_nonce
               OR OLD.account_last4 IS DISTINCT FROM NEW.account_last4
               OR OLD.account_fingerprint IS DISTINCT FROM NEW.account_fingerprint
               OR OLD.encryption_key_version IS DISTINCT FROM NEW.encryption_key_version
               OR OLD.fingerprint_key_version IS DISTINCT FROM NEW.fingerprint_key_version
               OR OLD.source_onboarding_id IS DISTINCT FROM NEW.source_onboarding_id
            THEN
                RAISE EXCEPTION 'bank account version identity is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_store_bank_account_versions_identity_immutable
        BEFORE UPDATE ON store_bank_account_versions
        FOR EACH ROW
        EXECUTE FUNCTION prevent_store_bank_account_version_identity_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_store_bank_account_versions_identity_immutable "
        "ON store_bank_account_versions"
    )
    op.execute("DROP FUNCTION prevent_store_bank_account_version_identity_update()")
    op.drop_index(
        "uq_store_bank_versions_approved",
        table_name="store_bank_account_versions",
    )
