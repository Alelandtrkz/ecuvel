"""add customer authentication and profile

Revision ID: e9a8b7c6d5f4
Revises: b1aa3259c75c
Create Date: 2026-06-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e9a8b7c6d5f4"
down_revision: Union[str, None] = "b1aa3259c75c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TOKEN_PURPOSE = postgresql.ENUM(
    "VERIFY_EMAIL",
    "RESET_PASSWORD",
    "CHANGE_EMAIL",
    name="user_account_token_purpose",
)


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        "users",
        sa.Column("email_normalized", sa.String(length=320), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("birth_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("gender", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind.execute(
        sa.text(
            "UPDATE users SET email_normalized = lower(trim(email)) "
            "WHERE email_normalized IS NULL"
        )
    )
    duplicates = bind.execute(
        sa.text(
            """
            SELECT email_normalized
            FROM users
            GROUP BY email_normalized
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicates is not None:
        raise RuntimeError(
            "No se puede crear autenticación: existen correos duplicados "
            "al normalizar mayúsculas/minúsculas."
        )
    op.alter_column(
        "users",
        "email_normalized",
        existing_type=sa.String(length=320),
        nullable=False,
    )
    op.create_index(
        op.f("ix_users_email_normalized"),
        "users",
        ["email_normalized"],
        unique=True,
    )

    TOKEN_PURPOSE.create(bind, checkfirst=True)
    op.create_table(
        "user_account_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "purpose",
            postgresql.ENUM(
                "VERIFY_EMAIL",
                "RESET_PASSWORD",
                "CHANGE_EMAIL",
                name="user_account_token_purpose",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_email", sa.String(length=320), nullable=True),
        sa.Column("new_email_normalized", sa.String(length=320), nullable=True),
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
            name=op.f("fk_user_account_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_account_tokens")),
    )
    op.create_index(
        op.f("ix_user_account_tokens_expires_at"),
        "user_account_tokens",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_account_tokens_purpose"),
        "user_account_tokens",
        ["purpose"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_account_tokens_token_hash"),
        "user_account_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_user_account_tokens_used_at"),
        "user_account_tokens",
        ["used_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_account_tokens_user_id"),
        "user_account_tokens",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_user_account_tokens_user_id"),
        table_name="user_account_tokens",
    )
    op.drop_index(
        op.f("ix_user_account_tokens_used_at"),
        table_name="user_account_tokens",
    )
    op.drop_index(
        op.f("ix_user_account_tokens_token_hash"),
        table_name="user_account_tokens",
    )
    op.drop_index(
        op.f("ix_user_account_tokens_purpose"),
        table_name="user_account_tokens",
    )
    op.drop_index(
        op.f("ix_user_account_tokens_expires_at"),
        table_name="user_account_tokens",
    )
    op.drop_table("user_account_tokens")
    TOKEN_PURPOSE.drop(op.get_bind(), checkfirst=True)
    op.drop_index(op.f("ix_users_email_normalized"), table_name="users")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "is_active")
    op.drop_column("users", "gender")
    op.drop_column("users", "birth_date")
    op.drop_column("users", "email_normalized")
