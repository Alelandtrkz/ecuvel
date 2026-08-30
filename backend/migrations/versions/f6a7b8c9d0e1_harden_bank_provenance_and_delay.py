"""harden bank account provenance and usability delay

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-30 05:25:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SOURCE_ONBOARDING_FK = (
    "fk_store_bank_account_versions_source_onboarding_id_sto_50b9"
)


def _replace_identity_function(*, include_primary_identity: bool) -> None:
    primary_identity_checks = """
               OLD.id IS DISTINCT FROM NEW.id
               OR OLD.created_at IS DISTINCT FROM NEW.created_at
               OR
    """ if include_primary_identity else ""
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION prevent_store_bank_account_version_identity_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF {primary_identity_checks} OLD.store_id IS DISTINCT FROM NEW.store_id
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


def upgrade() -> None:
    _replace_identity_function(include_primary_identity=True)
    op.drop_constraint(
        _SOURCE_ONBOARDING_FK,
        "store_bank_account_versions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        _SOURCE_ONBOARDING_FK,
        "store_bank_account_versions",
        "store_onboardings",
        ["source_onboarding_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "store_bank_version_usability_delay",
        "store_bank_account_versions",
        "reviewed_at IS NULL OR "
        "usable_from >= reviewed_at + INTERVAL '48 hours'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "store_bank_version_usability_delay",
        "store_bank_account_versions",
        type_="check",
    )
    op.drop_constraint(
        _SOURCE_ONBOARDING_FK,
        "store_bank_account_versions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        _SOURCE_ONBOARDING_FK,
        "store_bank_account_versions",
        "store_onboardings",
        ["source_onboarding_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _replace_identity_function(include_primary_identity=False)
