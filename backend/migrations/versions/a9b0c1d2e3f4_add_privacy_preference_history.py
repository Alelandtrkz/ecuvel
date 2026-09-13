"""add privacy preference history

Revision ID: a9b0c1d2e3f4
Revises: 9e5f7a8b0c1d
Create Date: 2026-09-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, None] = "9e5f7a8b0c1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRIVACY_PREFERENCE_PURPOSE = postgresql.ENUM(
    "CATALOG_BEHAVIORAL_TELEMETRY",
    name="privacy_preference_purpose",
    create_type=False,
)
PRIVACY_PREFERENCE_DECISION = postgresql.ENUM(
    "GRANTED",
    "REJECTED",
    name="privacy_preference_decision",
    create_type=False,
)
PRIVACY_PREFERENCE_SOURCE = postgresql.ENUM(
    "PRIVACY_BANNER",
    "PRIVACY_SETTINGS",
    name="privacy_preference_source",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    PRIVACY_PREFERENCE_PURPOSE.create(bind, checkfirst=True)
    PRIVACY_PREFERENCE_DECISION.create(bind, checkfirst=True)
    PRIVACY_PREFERENCE_SOURCE.create(bind, checkfirst=True)

    op.create_table(
        "user_privacy_preference_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", PRIVACY_PREFERENCE_PURPOSE, nullable=False),
        sa.Column("decision", PRIVACY_PREFERENCE_DECISION, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", PRIVACY_PREFERENCE_SOURCE, nullable=False),
        sa.Column(
            "notice_version_identifier", sa.String(length=140), nullable=True
        ),
        sa.Column("notice_content_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(notice_version_identifier IS NULL) = "
            "(notice_content_sha256 IS NULL)",
            name="notice_snapshots_paired",
        ),
        sa.CheckConstraint(
            "decision <> 'GRANTED' OR notice_version_identifier IS NOT NULL",
            name="granted_notice_required",
        ),
        sa.CheckConstraint(
            "notice_version_identifier IS NULL OR "
            "char_length(notice_version_identifier) BETWEEN 1 AND 140",
            name="notice_identifier_nonblank",
        ),
        sa.CheckConstraint(
            "notice_content_sha256 IS NULL OR "
            "notice_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="notice_sha256_format",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_privacy_preference_events_user_id",
        "user_privacy_preference_events",
        ["user_id"],
    )
    op.create_index(
        "ix_user_privacy_preference_events_current",
        "user_privacy_preference_events",
        ["user_id", "purpose", "occurred_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("user_privacy_preference_events")
    PRIVACY_PREFERENCE_SOURCE.drop(op.get_bind(), checkfirst=True)
    PRIVACY_PREFERENCE_DECISION.drop(op.get_bind(), checkfirst=True)
    PRIVACY_PREFERENCE_PURPOSE.drop(op.get_bind(), checkfirst=True)
