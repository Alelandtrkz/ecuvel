"""add phone otp authentication

Revision ID: f2b7c1d4e8a9
Revises: e9a8b7c6d5f4
Create Date: 2026-06-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f2b7c1d4e8a9"
down_revision: Union[str, None] = "e9a8b7c6d5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PHONE_OTP_PURPOSE = postgresql.ENUM(
    "LOGIN_OR_REGISTER",
    "LINK_PHONE",
    "CHANGE_PHONE",
    name="phone_otp_purpose",
)


NORMALIZE_PHONE_SQL = """
CREATE OR REPLACE FUNCTION ecuvel_normalize_ec_phone(raw_value text)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    cleaned text;
    digits text;
BEGIN
    IF raw_value IS NULL OR btrim(raw_value) = '' THEN
        RETURN NULL;
    END IF;
    cleaned := regexp_replace(raw_value, '[\\s\\-\\(\\)]', '', 'g');
    IF cleaned ~ '[A-Za-z]' THEN
        RAISE EXCEPTION 'No se puede normalizar telÃ©fono con letras: %', raw_value;
    END IF;
    IF cleaned LIKE '+%' THEN
        IF cleaned !~ '^\\+5939[0-9]{8}$' THEN
            RAISE EXCEPTION 'TelÃ©fono internacional ecuatoriano invÃ¡lido: %', raw_value;
        END IF;
        RETURN cleaned;
    END IF;
    IF cleaned !~ '^[0-9]+$' THEN
        RAISE EXCEPTION 'TelÃ©fono con caracteres invÃ¡lidos: %', raw_value;
    END IF;
    digits := cleaned;
    IF digits ~ '^09[0-9]{8}$' THEN
        RETURN '+593' || substring(digits from 2);
    END IF;
    IF digits ~ '^5939[0-9]{8}$' THEN
        RETURN '+' || digits;
    END IF;
    RAISE EXCEPTION 'TelÃ©fono ecuatoriano invÃ¡lido: %', raw_value;
END;
$$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(NORMALIZE_PHONE_SQL))

    op.add_column(
        "users",
        sa.Column("phone_normalized", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind.execute(
        sa.text(
            """
            UPDATE users
            SET phone_normalized = ecuvel_normalize_ec_phone(phone)
            WHERE phone IS NOT NULL AND btrim(phone) <> ''
            """
        )
    )
    duplicates = bind.execute(
        sa.text(
            """
            SELECT phone_normalized
            FROM users
            WHERE phone_normalized IS NOT NULL
            GROUP BY phone_normalized
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicates is not None:
        raise RuntimeError(
            "No se puede crear autenticaciÃ³n telefÃ³nica: existen telÃ©fonos "
            "duplicados al normalizar."
        )

    op.create_index(
        op.f("ix_users_phone_normalized"),
        "users",
        ["phone_normalized"],
        unique=True,
    )
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=320),
        nullable=True,
    )
    op.alter_column(
        "users",
        "email_normalized",
        existing_type=sa.String(length=320),
        nullable=True,
    )
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(length=255),
        nullable=True,
    )

    PHONE_OTP_PURPOSE.create(bind, checkfirst=True)
    op.create_table(
        "phone_otp_challenges",
        sa.Column("phone_normalized", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "purpose",
            postgresql.ENUM(
                "LOGIN_OR_REGISTER",
                "LINK_PHONE",
                "CHANGE_PHONE",
                name="phone_otp_purpose",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_phone_otp_challenges_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_phone_otp_challenges")),
    )
    for column in (
        "consumed_at",
        "expires_at",
        "last_sent_at",
        "phone_normalized",
        "purpose",
        "user_id",
        "verified_at",
    ):
        op.create_index(
            op.f(f"ix_phone_otp_challenges_{column}"),
            "phone_otp_challenges",
            [column],
            unique=False,
        )
    bind.execute(sa.text("DROP FUNCTION ecuvel_normalize_ec_phone(text)"))


def downgrade() -> None:
    for column in (
        "verified_at",
        "user_id",
        "purpose",
        "phone_normalized",
        "last_sent_at",
        "expires_at",
        "consumed_at",
    ):
        op.drop_index(
            op.f(f"ix_phone_otp_challenges_{column}"),
            table_name="phone_otp_challenges",
        )
    op.drop_table("phone_otp_challenges")
    PHONE_OTP_PURPOSE.drop(op.get_bind(), checkfirst=True)
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.alter_column(
        "users",
        "email_normalized",
        existing_type=sa.String(length=320),
        nullable=False,
    )
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=320),
        nullable=False,
    )
    op.drop_index(op.f("ix_users_phone_normalized"), table_name="users")
    op.drop_column("users", "phone_verified_at")
    op.drop_column("users", "phone_normalized")
